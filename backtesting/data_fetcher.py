"""
DATA FETCHER INSTITUCIONAL — V3
Fix: USTECm — prueba múltiples símbolos Dukascopy + fallback Yahoo Finance H1
"""

import asyncio
import gc
import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("DataFetcher")

try:
    import aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

# ─── Config ───────────────────────────────────────────────────

SPREAD_REAL = {
    "BTCUSDm": 14.0,
    "ETHUSDm": 1.40,
    "XAUUSDm": 0.28,
    "EURUSDm": 0.0002,
    "USTECm":  2.40,
}

SYMBOL_START_YEAR = {
    "BTCUSDm": 2017,
    "ETHUSDm": 2017,
    "XAUUSDm": 2004,
    "EURUSDm": 2003,
    "USTECm":  2015,
}

BINANCE_SYMBOLS = {
    "BTCUSDm": "BTCUSDT",
    "ETHUSDm": "ETHUSDT",
}

DUKASCOPY_SYMBOLS = {
    "XAUUSDm": "XAUUSD",
    "EURUSDm": "EURUSD",
    # USTECm: probamos múltiples nombres (ver DUKASCOPY_USTEC_CANDIDATES)
}

# Candidatos a probar para USTECm en Dukascopy (en orden)
DUKASCOPY_USTEC_CANDIDATES = ["USTEC", "NAS100", "NDX", "US100", "NASDAQ"]

# Yahoo Finance ticker para USTECm (fallback final)
YAHOO_SYMBOLS = {
    "USTECm": "^NDX",   # Nasdaq 100 index
}

TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440
}

_PERSISTENT_BASE = os.getenv("PERSISTENT_DIR", "/data")
_persistent_ok   = os.path.isdir(_PERSISTENT_BASE) and os.access(_PERSISTENT_BASE, os.W_OK)
DEFAULT_CACHE_DIR = os.path.join(
    _PERSISTENT_BASE if _persistent_ok else ".", "backtest_cache"
)

CACHE_TTL_H     = 24
CHUNK_SIZE      = 1000
MONTH_TIMEOUT   = 45
AIOHTTP_TOTAL   = 30
MAX_RETRIES     = 3
SESSION_MAX_REQ = 50


# ─── Bar ──────────────────────────────────────────────────────

@dataclass
class Bar:
    time:   str
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float
    spread: float = 0.0
    bid:    float = 0.0
    ask:    float = 0.0


# ─── Caché helpers ────────────────────────────────────────────

def _cache_valid(path: str) -> bool:
    if not os.path.exists(path):
        return False
    age_h = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600
    return age_h < CACHE_TTL_H


def _load_cache(path: str) -> list:
    try:
        with open(path, "r") as f:
            return [Bar(**b) for b in json.load(f)]
    except Exception as e:
        logger.warning(f"Caché corrupto {path}: {e}")
        return []


def _save_cache(path: str, bars: list):
    try:
        with open(path, "w") as f:
            json.dump([vars(b) for b in bars], f)
    except Exception as e:
        logger.error(f"Error guardando caché {path}: {e}")


def _year_cache_path(cache_dir: str, symbol: str, source: str, year: int) -> str:
    return os.path.join(cache_dir, f"{symbol}_{source}_year_{year}.json")


def _year_cache_valid(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 10


# ─── Resampler M1→H1 ──────────────────────────────────────────

def _resample_m1_to_h1(m1_bars: list, symbol: str) -> list:
    if not m1_bars:
        return []
    spread = SPREAD_REAL.get(symbol, 0)
    buckets = {}
    for b in m1_bars:
        try:
            t = datetime.fromisoformat(b.time.replace("Z", "+00:00"))
            key = t.replace(minute=0, second=0, microsecond=0).isoformat()
            if key not in buckets:
                buckets[key] = {
                    "open": b.open, "high": b.high,
                    "low":  b.low,  "close": b.close,
                    "volume": b.volume,
                }
            else:
                bkt = buckets[key]
                bkt["high"]    = max(bkt["high"], b.high)
                bkt["low"]     = min(bkt["low"],  b.low)
                bkt["close"]   = b.close
                bkt["volume"] += b.volume
        except Exception:
            continue
    h1_bars = []
    for t_str, bkt in sorted(buckets.items()):
        cl = bkt["close"]
        h1_bars.append(Bar(
            time=t_str,
            open=bkt["open"], high=bkt["high"],
            low=bkt["low"],   close=cl,
            volume=bkt["volume"],
            spread=spread, bid=cl, ask=cl + spread,
        ))
    return h1_bars


# ─── MetaAPI Fetcher ──────────────────────────────────────────

class MetaAPIFetcher:
    def __init__(self, token: str, account_id: str, cache_dir: str):
        self._token      = token
        self._account_id = account_id
        self._cache_dir  = cache_dir
        self._api = self._account = self._connection = None
        self._connected = False

    async def connect(self) -> bool:
        if not self._token or not self._account_id:
            return False
        try:
            from metaapi_cloud_sdk import MetaApi
            logger.info("DataFetcher: abriendo conexión MetaAPI DEDICADA...")
            self._api     = MetaApi(self._token)
            self._account = await self._api.metatrader_account_api.get_account(self._account_id)
            if self._account.state not in ["DEPLOYING", "DEPLOYED"]:
                await self._account.deploy()
            await self._account.wait_connected()
            self._connection = self._account.get_rpc_connection()
            await self._connection.connect()
            await self._connection.wait_synchronized()
            self._connected = True
            logger.info("✅ DataFetcher: conexión MetaAPI dedicada lista")
            return True
        except Exception as e:
            logger.error(f"DataFetcher MetaAPI connect: {e}")
            self._connected = False
            return False

    async def fetch_symbol(self, symbol: str, timeframe: str = "H1", start_year: int = 2017) -> list:
        cache_file = os.path.join(self._cache_dir, f"{symbol}_{timeframe}_exness.json")
        if _cache_valid(cache_file):
            logger.info(f"Caché Exness hit: {symbol} {timeframe}")
            return _load_cache(cache_file)
        if not self._connected or not self._connection:
            return []
        logger.info(f"Descargando {symbol} {timeframe} vía MetaAPI ({start_year}-hoy)...")
        all_bars = []
        spread   = SPREAD_REAL.get(symbol, 0)
        now      = datetime.now(timezone.utc)
        minutes  = TF_MINUTES.get(timeframe, 60)
        current_start = datetime(start_year, 1, 1, tzinfo=timezone.utc)
        while current_start < now:
            try:
                end_chunk = min(current_start + timedelta(minutes=minutes * CHUNK_SIZE), now)
                candles   = await self._connection.get_historical_candles(
                    symbol, timeframe, current_start, end_chunk, CHUNK_SIZE
                )
                if not candles:
                    current_start = end_chunk + timedelta(seconds=1)
                    await asyncio.sleep(0.5)
                    continue
                chunk_bars = []
                for c in candles:
                    try:
                        t  = c.get("time", "")
                        if hasattr(t, "isoformat"):
                            t = t.isoformat()
                        cl = float(c.get("close", 0))
                        if cl > 0:
                            chunk_bars.append(Bar(
                                time=str(t),
                                open=float(c.get("open", 0)),
                                high=float(c.get("high", 0)),
                                low=float(c.get("low", 0)),
                                close=cl,
                                volume=float(c.get("tickVolume", c.get("volume", 1))),
                                spread=spread, bid=cl, ask=cl + spread,
                            ))
                    except Exception:
                        continue
                if chunk_bars:
                    all_bars.extend(chunk_bars)
                    last_t = chunk_bars[-1].time
                    try:
                        current_start = datetime.fromisoformat(
                            last_t.replace("Z", "+00:00")
                        ) + timedelta(minutes=minutes)
                    except Exception:
                        current_start = end_chunk + timedelta(seconds=1)
                else:
                    current_start = end_chunk + timedelta(seconds=1)
                await asyncio.sleep(0.8)
            except Exception as e:
                logger.debug(f"MetaAPI chunk {symbol}: {e}")
                current_start += timedelta(days=30)
                await asyncio.sleep(2.0)
        if all_bars:
            all_bars.sort(key=lambda x: x.time)
            _save_cache(cache_file, all_bars)
            logger.info(f"✅ Exness {symbol}: {len(all_bars)} barras {timeframe}")
        else:
            logger.warning(f"Exness {symbol}: sin datos via MetaAPI")
        return all_bars

    async def close(self):
        try:
            if self._connection:
                await self._connection.close()
            self._connected = False
            self._connection = self._account = self._api = None
        except Exception:
            pass


# ─── Binance ──────────────────────────────────────────────────

class BinanceFetcher:
    BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

    def __init__(self, cache_dir: str):
        self._cache_dir = cache_dir
        self._session   = None
        self._req_count = 0

    async def _get_session(self):
        if self._session is None or self._session.closed or self._req_count >= SESSION_MAX_REQ:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session   = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
            self._req_count = 0
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2017) -> list:
        binance_sym = BINANCE_SYMBOLS.get(symbol)
        if not binance_sym:
            return []
        cache_file = os.path.join(self._cache_dir, f"{symbol}_H1_binance.json")
        if _cache_valid(cache_file):
            logger.info(f"Binance H1 caché hit: {symbol}")
            return _load_cache(cache_file)
        logger.info(f"Binance: descargando {symbol} M1→H1 ({start_year}-hoy) — mes a mes...")
        all_h1 = []
        now    = datetime.now(timezone.utc)
        for year in range(start_year, now.year + 1):
            year_cache = _year_cache_path(self._cache_dir, symbol, "binance", year)
            if _year_cache_valid(year_cache):
                year_bars = _load_cache(year_cache)
                all_h1.extend(year_bars)
                logger.info(f"  {symbol} {year}: {len(year_bars)} barras H1 (checkpoint | total: {len(all_h1)})")
                continue
            year_h1 = []
            for month in range(1, 13):
                if year == now.year and month >= now.month:
                    break
                m1_bars = await self._fetch_month_safe(binance_sym, symbol, year, month)
                if m1_bars:
                    h1_bars = _resample_m1_to_h1(m1_bars, symbol)
                    year_h1.extend(h1_bars)
                    del m1_bars
                    gc.collect()
                await asyncio.sleep(0.3)
            if year_h1:
                all_h1.extend(year_h1)
                _save_cache(year_cache, year_h1)
                logger.info(f"  {symbol} {year}: {len(year_h1)} barras H1 acumuladas (total: {len(all_h1)})")
                del year_h1
                gc.collect()
        if all_h1:
            all_h1.sort(key=lambda x: x.time)
            _save_cache(cache_file, all_h1)
            logger.info(f"✅ Binance {symbol}: {len(all_h1)} barras H1 | {all_h1[0].time[:10]} → {all_h1[-1].time[:10]}")
        return all_h1

    async def _fetch_month_safe(self, binance_sym, symbol, year, month):
        for attempt in range(MAX_RETRIES):
            try:
                return await asyncio.wait_for(
                    self._fetch_month(binance_sym, symbol, year, month),
                    timeout=MONTH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Binance {symbol} {year}-{month:02d}: timeout ({attempt+1}/{MAX_RETRIES})")
                if self._session and not self._session.closed:
                    await self._session.close()
                self._session = None
                self._req_count = 0
                await asyncio.sleep(3 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Binance {symbol} {year}-{month:02d}: {e} ({attempt+1}/{MAX_RETRIES})")
                await asyncio.sleep(2 ** attempt)
        return []

    async def _fetch_month(self, binance_sym, symbol, year, month):
        url  = f"{self.BASE_URL}/{binance_sym}/1m/{binance_sym}-1m-{year}-{month:02d}.zip"
        sess = await self._get_session()
        self._req_count += 1
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=AIOHTTP_TOTAL)) as r:
            if r.status != 200:
                return []
            content = await r.read()
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            with z.open(z.namelist()[0]) as f:
                lines = f.read().decode().strip().split("\n")
        spread = SPREAD_REAL.get(symbol, 0)
        bars   = []
        for line in lines:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                ts = int(parts[0]) / 1000
                cl = float(parts[4])
                bars.append(Bar(
                    time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    open=float(parts[1]), high=float(parts[2]),
                    low=float(parts[3]),  close=cl,
                    volume=float(parts[5]),
                    spread=spread, bid=cl, ask=cl + spread,
                ))
            except Exception:
                continue
        return bars

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ─── Dukascopy ────────────────────────────────────────────────

class DukascopyFetcher:
    BASE_URL = "https://datafeed.dukascopy.com/datafeed"

    def __init__(self, cache_dir: str):
        self._cache_dir = cache_dir
        self._session   = None
        self._req_count = 0

    async def _get_session(self):
        if self._session is None or self._session.closed or self._req_count >= SESSION_MAX_REQ:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            self._req_count = 0
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2003) -> list:
        duka_sym = DUKASCOPY_SYMBOLS.get(symbol)
        if not duka_sym:
            return []
        cache_file = os.path.join(self._cache_dir, f"{symbol}_H1_dukascopy.json")
        if _cache_valid(cache_file):
            logger.info(f"Dukascopy caché hit: {symbol}")
            return _load_cache(cache_file)
        logger.info(f"Dukascopy: descargando {symbol} H1 ({start_year}-hoy)...")
        all_bars = []
        now = datetime.now(timezone.utc)
        for year in range(start_year, now.year + 1):
            year_cache = _year_cache_path(self._cache_dir, symbol, "dukascopy", year)
            if _year_cache_valid(year_cache):
                year_bars = _load_cache(year_cache)
                all_bars.extend(year_bars)
                logger.info(f"  {symbol} {year}: {len(year_bars)} barras H1 (checkpoint | total: {len(all_bars)})")
                continue
            year_bars = []
            for month in range(0, 12):
                if year == now.year and month >= now.month - 1:
                    break
                bars = await self._fetch_month_safe(duka_sym, symbol, year, month)
                if bars:
                    year_bars.extend(bars)
                await asyncio.sleep(0.3)
            if year_bars:
                all_bars.extend(year_bars)
                _save_cache(year_cache, year_bars)
                logger.info(f"  {symbol} {year}: {len(year_bars)} barras H1 (total: {len(all_bars)})")
            else:
                logger.debug(f"  {symbol} {year}: 0 barras")
        if all_bars:
            _save_cache(cache_file, all_bars)
            logger.info(f"✅ Dukascopy {symbol}: {len(all_bars)} barras H1")
        return all_bars

    async def fetch_ustec_candidates(self, start_year: int = 2015) -> list:
        """
        Prueba múltiples símbolos de Dukascopy para USTECm.
        Retorna el primero que devuelva datos.
        """
        symbol = "USTECm"
        cache_file = os.path.join(self._cache_dir, f"{symbol}_H1_dukascopy.json")
        if _cache_valid(cache_file):
            logger.info(f"Dukascopy caché hit: {symbol}")
            return _load_cache(cache_file)

        now = datetime.now(timezone.utc)

        for candidate in DUKASCOPY_USTEC_CANDIDATES:
            logger.info(f"USTECm: probando símbolo Dukascopy '{candidate}'...")
            all_bars = []
            found    = False

            for year in range(start_year, now.year + 1):
                year_cache = _year_cache_path(self._cache_dir, symbol, f"duka_{candidate}", year)
                if _year_cache_valid(year_cache):
                    year_bars = _load_cache(year_cache)
                    all_bars.extend(year_bars)
                    found = True
                    continue

                year_bars = []
                for month in range(0, 12):
                    if year == now.year and month >= now.month - 1:
                        break
                    bars = await self._fetch_month_safe(candidate, symbol, year, month)
                    if bars:
                        year_bars.extend(bars)
                        found = True
                    await asyncio.sleep(0.2)

                if year_bars:
                    all_bars.extend(year_bars)
                    _save_cache(year_cache, year_bars)
                    logger.info(f"  USTECm [{candidate}] {year}: {len(year_bars)} barras H1")

            if found and len(all_bars) > 200:
                _save_cache(cache_file, all_bars)
                logger.info(f"✅ Dukascopy USTECm [{candidate}]: {len(all_bars)} barras H1")
                return all_bars
            else:
                logger.warning(f"USTECm: '{candidate}' sin datos — probando siguiente...")

        logger.warning("USTECm: ningún símbolo Dukascopy funcionó — usando Yahoo Finance")
        return []

    async def _fetch_month_safe(self, duka_sym, symbol, year, month):
        for attempt in range(MAX_RETRIES):
            try:
                return await asyncio.wait_for(
                    self._fetch_month(duka_sym, symbol, year, month),
                    timeout=MONTH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Dukascopy {symbol} {year}-{month:02d}: timeout ({attempt+1}/{MAX_RETRIES}) — recreando sesión")
                if self._session and not self._session.closed:
                    await self._session.close()
                self._session = None
                self._req_count = 0
                await asyncio.sleep(3 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Dukascopy {symbol} {year}-{month:02d}: {e} ({attempt+1}/{MAX_RETRIES})")
                await asyncio.sleep(2 ** attempt)
        return []

    async def _fetch_month(self, duka_sym, symbol, year, month):
        url  = f"{self.BASE_URL}/{duka_sym}/{year}/{month:02d}/BID_candles_hour_1.bi5"
        sess = await self._get_session()
        self._req_count += 1
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=AIOHTTP_TOTAL)) as r:
            if r.status != 200:
                return []
            content = await r.read()
        return self._parse_bi5(content, symbol, year, month)

    def _parse_bi5(self, data, symbol, year, month):
        try:
            import lzma, struct
            decompressed = lzma.decompress(data)
            price_factor = {
                "EURUSDm": 100000.0,
                "XAUUSDm": 1000.0,
                "USTECm":  10.0,
            }.get(symbol, 1.0)
            spread  = SPREAD_REAL.get(symbol, 0)
            base_ts = datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()
            bars    = []
            for i in range(0, len(decompressed) - 23, 24):
                chunk = decompressed[i:i + 24]
                try:
                    ms, o, h, l, c, v = struct.unpack(">IIIIIf", chunk)
                    ts = base_ts + ms / 1000
                    cl = c / price_factor
                    if cl <= 0:
                        continue
                    bars.append(Bar(
                        time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        open=o / price_factor, high=h / price_factor,
                        low=l  / price_factor, close=cl,
                        volume=float(v),
                        spread=spread, bid=cl, ask=cl + spread,
                    ))
                except Exception:
                    continue
            return bars
        except Exception:
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ─── Yahoo Finance Fetcher (fallback para índices) ────────────

class YahooFetcher:
    """
    Fallback para USTECm cuando Dukascopy no tiene datos.
    Usa yfinance con ^NDX (Nasdaq 100).
    Descarga H1 (últimos 730 días) + Daily histórico para más historia.
    """

    async def fetch_ustec(self, symbol: str = "USTECm", start_year: int = 2015) -> list:
        cache_file = os.path.join(DEFAULT_CACHE_DIR, f"{symbol}_H1_yahoo.json")
        if _cache_valid(cache_file):
            logger.info(f"Yahoo caché hit: {symbol}")
            return _load_cache(cache_file)

        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance no instalado — agregar 'yfinance' a requirements.txt")
            return []

        yahoo_sym = YAHOO_SYMBOLS.get(symbol, "^NDX")
        spread    = SPREAD_REAL.get(symbol, 0)
        all_bars  = []

        # 1. Datos H1 — últimos 730 días (límite gratuito de Yahoo)
        logger.info(f"Yahoo Finance: descargando {symbol} ({yahoo_sym}) H1 — últimos 730 días...")
        try:
            ticker = yf.Ticker(yahoo_sym)
            df_h1  = ticker.history(period="730d", interval="1h", auto_adjust=True)
            if not df_h1.empty:
                for ts, row in df_h1.iterrows():
                    try:
                        cl = float(row["Close"])
                        if cl <= 0:
                            continue
                        t_str = ts.astimezone(timezone.utc).isoformat()
                        all_bars.append(Bar(
                            time=t_str,
                            open=float(row["Open"]), high=float(row["High"]),
                            low=float(row["Low"]),   close=cl,
                            volume=float(row.get("Volume", 0)),
                            spread=spread, bid=cl, ask=cl + spread,
                        ))
                    except Exception:
                        continue
                logger.info(f"  Yahoo H1: {len(all_bars)} barras")
        except Exception as e:
            logger.warning(f"Yahoo H1 {symbol}: {e}")

        # 2. Datos Daily — para historia más larga (desde start_year)
        #    Se agregan ANTES de los H1 sin duplicar fechas
        logger.info(f"Yahoo Finance: descargando {symbol} Daily ({start_year}-hoy) para historia larga...")
        try:
            df_d = ticker.history(start=f"{start_year}-01-01", interval="1d", auto_adjust=True)
            if not df_d.empty:
                h1_dates = {b.time[:10] for b in all_bars}
                daily_bars = []
                for ts, row in df_d.iterrows():
                    try:
                        date_str = ts.strftime("%Y-%m-%d")
                        if date_str in h1_dates:
                            continue  # ya cubierto por H1
                        cl = float(row["Close"])
                        if cl <= 0:
                            continue
                        # Simular 8 barras H1 por día (9:00-17:00 NY time)
                        for hour in range(9, 17):
                            t_dt = datetime(ts.year, ts.month, ts.day, hour, 0, 0, tzinfo=timezone.utc)
                            t_str = t_dt.isoformat()
                            daily_bars.append(Bar(
                                time=t_str,
                                open=float(row["Open"]) if hour == 9 else cl,
                                high=float(row["High"]) if hour in [10, 11] else cl,
                                low=float(row["Low"])   if hour in [14, 15] else cl,
                                close=cl,
                                volume=float(row.get("Volume", 0)) / 8,
                                spread=spread, bid=cl, ask=cl + spread,
                            ))
                    except Exception:
                        continue
                all_bars = daily_bars + all_bars  # historia diaria primero
                logger.info(f"  Yahoo Daily: {len(daily_bars)} barras H1 simuladas ({start_year}-hoy)")
        except Exception as e:
            logger.warning(f"Yahoo Daily {symbol}: {e}")

        if all_bars:
            all_bars.sort(key=lambda x: x.time)
            _save_cache(cache_file, all_bars)
            logger.info(f"✅ Yahoo {symbol}: {len(all_bars)} barras H1 | {all_bars[0].time[:10]} → {all_bars[-1].time[:10]}")
        return all_bars


# ─── DataFetcher principal ────────────────────────────────────

class DataFetcher:

    def __init__(self, token: str = "", account_id: str = "", cache_dir: str = None):
        self._token      = token      or os.getenv("META_API_TOKEN", "")
        self._account_id = account_id or os.getenv("MT5_ACCOUNT_ID", "")
        effective_cache  = cache_dir or DEFAULT_CACHE_DIR
        os.makedirs(effective_cache, exist_ok=True)

        if effective_cache.startswith("/data"):
            logger.info(f"DataFetcher: usando Railway Volume → {effective_cache}")
        else:
            logger.info(f"DataFetcher: usando caché local → {effective_cache}")

        self.metaapi   = MetaAPIFetcher(self._token, self._account_id, effective_cache)
        self.binance   = BinanceFetcher(effective_cache)
        self.dukascopy = DukascopyFetcher(effective_cache)
        self.yahoo     = YahooFetcher()
        self._meta_ok  = False

    async def connect(self):
        self._meta_ok = await self.metaapi.connect()
        if not self._meta_ok:
            logger.warning("DataFetcher: MetaAPI no disponible — usando Binance H1 + Dukascopy H1")

    async def fetch(self, symbol: str, timeframe: str = "H1") -> tuple:
        start_year = SYMBOL_START_YEAR.get(symbol, 2010)

        # 1. MetaAPI
        if self._meta_ok:
            bars = await self.metaapi.fetch_symbol(symbol, timeframe, start_year)
            if len(bars) >= 200:
                return bars, f"Exness MT5 (MetaAPI) {timeframe}"
            logger.warning(f"{symbol}: MetaAPI {len(bars)} barras — fallback")

        # 2. Binance
        if symbol in BINANCE_SYMBOLS:
            bars = await self.binance.fetch_symbol(symbol, start_year)
            if bars:
                return bars, "Binance Vision → H1"

        # 3. Dukascopy — símbolo estándar
        if symbol in DUKASCOPY_SYMBOLS:
            bars = await self.dukascopy.fetch_symbol(symbol, start_year)
            if bars:
                return bars, "Dukascopy H1"

        # 4. USTECm — candidatos múltiples Dukascopy → Yahoo Finance
        if symbol == "USTECm":
            bars = await self.dukascopy.fetch_ustec_candidates(start_year)
            if bars:
                return bars, "Dukascopy H1 (USTEC candidato)"
            # Último recurso: Yahoo Finance
            bars = await self.yahoo.fetch_ustec(symbol, start_year)
            if bars:
                return bars, "Yahoo Finance ^NDX → H1"

        logger.error(f"DataFetcher: sin datos para {symbol}")
        return [], "sin datos"

    async def close(self):
        await self.metaapi.close()
        await self.binance.close()
        await self.dukascopy.close()
