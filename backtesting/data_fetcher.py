"""
DATA FETCHER INSTITUCIONAL — Datos históricos reales
Procesa mes a mes para no acumular RAM — Railway safe.

Flujo Binance/Dukascopy:
  Descarga 1 mes → resamplea a H1 → guarda H1 → libera M1
  (nunca acumula millones de barras M1 en RAM)

Flujo MetaAPI:
  Conexión DEDICADA → get_historical_candles H1 directo
  → sin problemas de memoria
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
    "USTECm":  "USTEC",
}

TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440
}

CACHE_DIR   = "backtest_cache"
CACHE_TTL_H = 24
CHUNK_SIZE  = 1000
os.makedirs(CACHE_DIR, exist_ok=True)


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


# ─── Caché ────────────────────────────────────────────────────

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


# ─── Resampler inline (sin DuckDB para ahorrar RAM) ───────────

def _resample_m1_to_h1(m1_bars: list, symbol: str) -> list:
    """
    Resamplea barras M1 a H1 en memoria sin DuckDB.
    Procesa en bloques para no acumular todo.
    """
    if not m1_bars:
        return []
    spread = SPREAD_REAL.get(symbol, 0)
    buckets = {}
    for b in m1_bars:
        try:
            # Truncar al inicio de la hora
            t = datetime.fromisoformat(b.time.replace("Z", "+00:00"))
            bucket_key = t.replace(minute=0, second=0, microsecond=0).isoformat()
            if bucket_key not in buckets:
                buckets[bucket_key] = {
                    "open": b.open, "high": b.high,
                    "low": b.low, "close": b.close,
                    "volume": b.volume, "count": 1,
                }
            else:
                bkt = buckets[bucket_key]
                bkt["high"]    = max(bkt["high"], b.high)
                bkt["low"]     = min(bkt["low"],  b.low)
                bkt["close"]   = b.close
                bkt["volume"] += b.volume
                bkt["count"]  += 1
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
    """Conexión DEDICADA a MetaAPI — descarga H1 directo, sin problema de RAM."""

    def __init__(self, token: str, account_id: str):
        self._token      = token
        self._account_id = account_id
        self._api        = None
        self._account    = None
        self._connection = None
        self._connected  = False

    async def connect(self) -> bool:
        if not self._token or not self._account_id:
            return False
        try:
            from metaapi_cloud_sdk import MetaApi
            logger.info("DataFetcher: abriendo conexión MetaAPI DEDICADA...")
            self._api     = MetaApi(self._token)
            self._account = await self._api.metatrader_account_api.get_account(
                self._account_id
            )
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

    async def fetch_symbol(
        self, symbol: str, timeframe: str = "H1", start_year: int = 2017
    ) -> list:
        cache_file = os.path.join(CACHE_DIR, f"{symbol}_{timeframe}_exness.json")
        if _cache_valid(cache_file):
            logger.info(f"Caché Exness hit: {symbol} {timeframe}")
            return _load_cache(cache_file)

        if not self._connected or not self._connection:
            return []

        logger.info(f"Descargando {symbol} {timeframe} desde Exness MT5 ({start_year}-hoy)...")
        all_bars = []
        spread   = SPREAD_REAL.get(symbol, 0)
        now      = datetime.now(timezone.utc)
        minutes  = TF_MINUTES.get(timeframe, 60)
        current_start = datetime(start_year, 1, 1, tzinfo=timezone.utc)

        while current_start < now:
            try:
                end_chunk = current_start + timedelta(minutes=minutes * CHUNK_SIZE)
                if end_chunk > now:
                    end_chunk = now

                candles = await self._connection.get_historical_candles(
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
                                low=float(c.get("low",   0)),
                                close=cl,
                                volume=float(c.get("tickVolume", c.get("volume", 1))),
                                spread=spread, bid=cl, ask=cl + spread,
                            ))
                    except Exception:
                        continue

                if chunk_bars:
                    all_bars.extend(chunk_bars)
                    logger.info(
                        f"  {symbol}: +{len(chunk_bars)} barras "
                        f"({chunk_bars[0].time[:10]} → {chunk_bars[-1].time[:10]})"
                    )
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
            logger.info(
                f"✅ Exness {symbol}: {len(all_bars)} barras {timeframe} | "
                f"{all_bars[0].time[:10]} → {all_bars[-1].time[:10]}"
            )
        else:
            logger.warning(f"Exness {symbol}: sin datos via MetaAPI")

        return all_bars

    async def close(self):
        try:
            if self._connection:
                await self._connection.close()
            self._connected  = False
            self._connection = None
            self._account    = None
            self._api        = None
            logger.info("DataFetcher: conexión MetaAPI dedicada cerrada")
        except Exception:
            pass


# ─── Binance — chunk por mes, resamplea a H1, libera M1 ──────

class BinanceFetcher:
    """
    Descarga M1 mes a mes.
    Resamplea a H1 ANTES de acumular → máximo ~45k barras M1 en RAM a la vez.
    Guarda H1 progresivamente en caché parcial por año.
    """
    BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            )
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2017) -> list:
        binance_sym = BINANCE_SYMBOLS.get(symbol)
        if not binance_sym:
            return []

        # Caché final H1
        cache_file = os.path.join(CACHE_DIR, f"{symbol}_H1_binance.json")
        if _cache_valid(cache_file):
            logger.info(f"Binance H1 caché hit: {symbol}")
            return _load_cache(cache_file)

        logger.info(
            f"Binance: descargando {symbol} M1→H1 "
            f"({start_year}-hoy) — procesando mes a mes..."
        )
        all_h1 = []
        now    = datetime.now(timezone.utc)

        for year in range(start_year, now.year + 1):
            year_h1 = []
            for month in range(1, 13):
                if year == now.year and month >= now.month:
                    break

                # Descargar 1 mes de M1
                m1_bars = await self._fetch_month(binance_sym, symbol, year, month)

                if m1_bars:
                    # Resamplear a H1 inmediatamente
                    h1_bars = _resample_m1_to_h1(m1_bars, symbol)
                    year_h1.extend(h1_bars)

                    # Liberar M1 de memoria
                    del m1_bars
                    gc.collect()

                await asyncio.sleep(0.3)

            if year_h1:
                all_h1.extend(year_h1)
                logger.info(
                    f"  {symbol} {year}: {len(year_h1)} barras H1 acumuladas "
                    f"(total: {len(all_h1)})"
                )
                del year_h1
                gc.collect()

        if all_h1:
            all_h1.sort(key=lambda x: x.time)
            _save_cache(cache_file, all_h1)
            logger.info(
                f"✅ Binance {symbol}: {len(all_h1)} barras H1 | "
                f"{all_h1[0].time[:10]} → {all_h1[-1].time[:10]}"
            )
        return all_h1

    async def _fetch_month(self, binance_sym, symbol, year, month) -> list:
        url = (
            f"{self.BASE_URL}/{binance_sym}/1m/"
            f"{binance_sym}-1m-{year}-{month:02d}.zip"
        )
        try:
            sess = await self._get_session()
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
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

        except Exception as e:
            logger.debug(f"Binance {year}-{month:02d}: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ─── Dukascopy — ya descarga H1 directo ───────────────────────

class DukascopyFetcher:
    """Descarga H1 directamente — sin problema de memoria."""
    BASE_URL = "https://datafeed.dukascopy.com/datafeed"

    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False),
                headers={"User-Agent": "Mozilla/5.0"},
            )
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2003) -> list:
        duka_sym = DUKASCOPY_SYMBOLS.get(symbol)
        if not duka_sym:
            return []

        cache_file = os.path.join(CACHE_DIR, f"{symbol}_H1_dukascopy.json")
        if _cache_valid(cache_file):
            logger.info(f"Dukascopy caché hit: {symbol}")
            return _load_cache(cache_file)

        logger.info(f"Dukascopy: descargando {symbol} H1 ({start_year}-hoy)...")
        all_bars = []
        now = datetime.now(timezone.utc)

        for year in range(start_year, now.year + 1):
            year_count = 0
            for month in range(0, 12):
                if year == now.year and month >= now.month - 1:
                    break
                bars = await self._fetch_month(duka_sym, symbol, year, month)
                if bars:
                    all_bars.extend(bars)
                    year_count += len(bars)
                await asyncio.sleep(0.2)
            if year_count:
                logger.info(f"  {symbol} {year}: {year_count} barras H1")

        if all_bars:
            _save_cache(cache_file, all_bars)
            logger.info(f"✅ Dukascopy {symbol}: {len(all_bars)} barras H1")
        return all_bars

    async def _fetch_month(self, duka_sym, symbol, year, month) -> list:
        url = (
            f"{self.BASE_URL}/{duka_sym}/{year}/"
            f"{month:02d}/BID_candles_hour_1.bi5"
        )
        try:
            sess = await self._get_session()
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return []
                content = await r.read()
            return self._parse_bi5(content, symbol, year, month)
        except Exception as e:
            logger.debug(f"Dukascopy {year}-{month:02d}: {e}")
            return []

    def _parse_bi5(self, data, symbol, year, month) -> list:
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
                chunk = decompressed[i:i+24]
                try:
                    ms, o, h, l, c, v = struct.unpack(">IIIIIf", chunk)
                    ts = base_ts + ms / 1000
                    cl = c / price_factor
                    bars.append(Bar(
                        time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        open=o/price_factor, high=h/price_factor,
                        low=l/price_factor,  close=cl,
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


# ─── DataFetcher principal ────────────────────────────────────

class DataFetcher:
    """
    Orquestador.
    Prioridad: MetaAPI (Exness real) → Binance H1 → Dukascopy H1
    Binance procesa mes a mes para no saturar RAM en Railway.
    """

    def __init__(self, token: str = "", account_id: str = ""):
        self._token      = token or os.getenv("META_API_TOKEN", "")
        self._account_id = account_id or os.getenv("MT5_ACCOUNT_ID", "")
        self.metaapi     = MetaAPIFetcher(self._token, self._account_id)
        self.binance     = BinanceFetcher()
        self.dukascopy   = DukascopyFetcher()
        self._meta_ok    = False

    async def connect(self):
        self._meta_ok = await self.metaapi.connect()
        if not self._meta_ok:
            logger.warning(
                "DataFetcher: MetaAPI no disponible — "
                "usando Binance H1 + Dukascopy H1"
            )

    async def fetch(self, symbol: str, timeframe: str = "H1") -> tuple:
        start_year = SYMBOL_START_YEAR.get(symbol, 2010)

        # 1. MetaAPI — Exness real
        if self._meta_ok:
            bars = await self.metaapi.fetch_symbol(symbol, timeframe, start_year)
            if len(bars) >= 200:
                return bars, f"Exness MT5 (MetaAPI) {timeframe}"
            logger.warning(f"{symbol}: MetaAPI {len(bars)} barras — fallback")

        # 2. Binance → H1 (chunk por mes)
        if symbol in BINANCE_SYMBOLS:
            bars = await self.binance.fetch_symbol(symbol, start_year)
            if bars:
                return bars, "Binance Vision → H1"

        # 3. Dukascopy → H1 directo
        if symbol in DUKASCOPY_SYMBOLS:
            bars = await self.dukascopy.fetch_symbol(symbol, start_year)
            if bars:
                return bars, "Dukascopy H1"

        logger.error(f"DataFetcher: sin datos para {symbol}")
        return [], "sin datos"

    async def close(self):
        await self.metaapi.close()
        await self.binance.close()
        await self.dukascopy.close()
