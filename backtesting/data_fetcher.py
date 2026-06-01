"""
DATA FETCHER — Descarga de datos históricos
Fuentes: Binance Vision (BTC/ETH) · Dukascopy (EUR/XAU/NAS) · Yahoo (fallback)
Sin MetaAPI — no satura el stream en vivo.
Caché local JSON por símbolo (válido 24h).
"""

import asyncio
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

# ─── Constantes ───────────────────────────────────────────────

CACHE_DIR = "backtest_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

EXNESS_SYMBOLS = {
    "BTCUSDm": {"start_year": 2017, "pip": 1.0},
    "ETHUSDm": {"start_year": 2017, "pip": 0.1},
    "XAUUSDm": {"start_year": 2004, "pip": 0.01},
    "EURUSDm": {"start_year": 2003, "pip": 0.0001},
    "USTECm":  {"start_year": 2015, "pip": 1.0},
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

SPREAD_REAL = {
    "BTCUSDm": 14.0,
    "ETHUSDm": 1.40,
    "XAUUSDm": 0.28,
    "EURUSDm": 0.0002,
    "USTECm":  2.40,
}

TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440
}

CACHE_TTL_HOURS = 24  # horas de validez del caché


# ─── Estructura base ──────────────────────────────────────────

@dataclass
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float = 0.0


# ─── Helpers ──────────────────────────────────────────────────

def _cache_valid(cache_file: str, ttl_hours: int = CACHE_TTL_HOURS) -> bool:
    if not os.path.exists(cache_file):
        return False
    age_hours = (datetime.now().timestamp() - os.path.getmtime(cache_file)) / 3600
    return age_hours < ttl_hours


def _load_cache(cache_file: str) -> list:
    try:
        with open(cache_file, "r") as f:
            return [Bar(**b) for b in json.load(f)]
    except Exception as e:
        logger.warning(f"Error leyendo caché {cache_file}: {e}")
        return []


def _save_cache(cache_file: str, bars: list):
    try:
        with open(cache_file, "w") as f:
            json.dump([vars(b) for b in bars], f)
    except Exception as e:
        logger.error(f"Error guardando caché {cache_file}: {e}")


# ─── DuckDB Resampler ─────────────────────────────────────────

class DuckDBResampler:
    """Resamplea barras de M1 a cualquier timeframe usando DuckDB."""

    def resample(self, bars: list, target_tf: str) -> list:
        if not bars:
            return bars
        minutes = TF_MINUTES.get(target_tf, 60)
        if minutes == 1:
            return bars
        try:
            import duckdb
            import pandas as pd
            df = pd.DataFrame([vars(b) for b in bars])
            df["time"] = pd.to_datetime(df["time"])
            con = duckdb.connect()
            con.register("bars_df", df)
            query = f"""
                SELECT
                    time_bucket(INTERVAL '{minutes} minutes', time) AS bucket,
                    FIRST(open  ORDER BY time) AS open,
                    MAX(high)                  AS high,
                    MIN(low)                   AS low,
                    LAST(close  ORDER BY time) AS close,
                    SUM(volume)                AS volume,
                    AVG(spread)                AS spread
                FROM bars_df
                GROUP BY bucket
                ORDER BY bucket ASC
            """
            result = con.execute(query).df()
            con.close()
            return [
                Bar(
                    time=str(row["bucket"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    spread=float(row["spread"]),
                )
                for _, row in result.iterrows()
            ]
        except Exception as e:
            logger.warning(f"DuckDB resample error: {e} — usando barras sin resamplear")
            return bars


# ─── Binance Vision ───────────────────────────────────────────

class BinanceFetcher:
    """
    Descarga datos históricos M1 desde Binance Vision (datos públicos).
    Cubre BTC y ETH desde 2017.
    """
    BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

    def __init__(self):
        self._session = None

    async def _get_session(self) -> "aiohttp.ClientSession":
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2017) -> list:
        binance_sym = BINANCE_SYMBOLS.get(symbol)
        if not binance_sym:
            return []

        cache_file = os.path.join(CACHE_DIR, f"{symbol}_M1_binance.json")
        if _cache_valid(cache_file):
            logger.info(f"Binance caché hit: {symbol}")
            return _load_cache(cache_file)

        logger.info(f"Descargando {symbol} desde Binance Vision ({start_year}-hoy)...")
        all_bars = []
        now = datetime.now(timezone.utc)

        for year in range(start_year, now.year + 1):
            for month in range(1, 13):
                if year == now.year and month >= now.month:
                    break
                bars = await self._fetch_month(binance_sym, symbol, year, month)
                if bars:
                    all_bars.extend(bars)
                await asyncio.sleep(0.3)

        if all_bars:
            _save_cache(cache_file, all_bars)
            logger.info(
                f"✅ Binance {symbol}: {len(all_bars)} barras M1 "
                f"({all_bars[0].time[:10]} → {all_bars[-1].time[:10]})"
            )
        else:
            logger.warning(f"Binance {symbol}: sin datos")

        return all_bars

    async def _fetch_month(
        self, binance_sym: str, symbol: str, year: int, month: int
    ) -> list:
        url = (
            f"{self.BASE_URL}/{binance_sym}/1m/"
            f"{binance_sym}-1m-{year}-{month:02d}.zip"
        )
        try:
            sess = await self._get_session()
            async with sess.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                if r.status != 200:
                    return []
                content = await r.read()

            with zipfile.ZipFile(io.BytesIO(content)) as z:
                with z.open(z.namelist()[0]) as f:
                    lines = f.read().decode().strip().split("\n")

            spread = SPREAD_REAL.get(symbol, 0)
            bars = []
            for line in lines:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                try:
                    ts = int(parts[0]) / 1000
                    bars.append(Bar(
                        time=datetime.fromtimestamp(
                            ts, tz=timezone.utc
                        ).isoformat(),
                        open=float(parts[1]),
                        high=float(parts[2]),
                        low=float(parts[3]),
                        close=float(parts[4]),
                        volume=float(parts[5]),
                        spread=spread,
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


# ─── Dukascopy ────────────────────────────────────────────────

class DukascopyFetcher:
    """
    Descarga datos históricos H1 desde Dukascopy.
    Cubre EUR, XAU y NAS con historia larga (2003+).
    """
    BASE_URL = "https://datafeed.dukascopy.com/datafeed"

    def __init__(self):
        self._session = None

    async def _get_session(self) -> "aiohttp.ClientSession":
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "Mozilla/5.0"}
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

        logger.info(f"Descargando {symbol} desde Dukascopy ({start_year}-hoy)...")
        all_bars = []
        now = datetime.now(timezone.utc)

        for year in range(start_year, now.year + 1):
            year_bars = 0
            for month in range(0, 12):
                if year == now.year and month >= now.month - 1:
                    break
                bars = await self._fetch_month(duka_sym, symbol, year, month)
                if bars:
                    all_bars.extend(bars)
                    year_bars += len(bars)
                await asyncio.sleep(0.2)
            if year_bars:
                logger.info(f"  {symbol} {year}: {year_bars} barras H1")

        if all_bars:
            _save_cache(cache_file, all_bars)
            logger.info(
                f"✅ Dukascopy {symbol}: {len(all_bars)} barras H1 "
                f"({all_bars[0].time[:10]} → {all_bars[-1].time[:10]})"
            )
        else:
            logger.warning(f"Dukascopy {symbol}: sin datos")

        return all_bars

    async def _fetch_month(
        self, duka_sym: str, symbol: str, year: int, month: int
    ) -> list:
        url = (
            f"{self.BASE_URL}/{duka_sym}/{year}/"
            f"{month:02d}/BID_candles_hour_1.bi5"
        )
        try:
            sess = await self._get_session()
            async with sess.get(
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                if r.status != 200:
                    return []
                content = await r.read()
            return self._parse_bi5(content, symbol, year, month)
        except Exception as e:
            logger.debug(f"Dukascopy {year}-{month:02d}: {e}")
            return []

    def _parse_bi5(
        self, data: bytes, symbol: str, year: int, month: int
    ) -> list:
        try:
            import lzma
            import struct
            decompressed = lzma.decompress(data)
            price_factor = {
                "EURUSDm": 100000.0,
                "XAUUSDm": 1000.0,
                "USTECm":  10.0,
            }.get(symbol, 1.0)
            spread = SPREAD_REAL.get(symbol, 0)
            base_ts = datetime(
                year, month + 1, 1, tzinfo=timezone.utc
            ).timestamp()
            bars = []
            for i in range(0, len(decompressed) - 23, 24):
                chunk = decompressed[i:i+24]
                try:
                    ms, o, h, l, c, v = struct.unpack(">IIIIIf", chunk)
                    ts = base_ts + ms / 1000
                    bars.append(Bar(
                        time=datetime.fromtimestamp(
                            ts, tz=timezone.utc
                        ).isoformat(),
                        open=o / price_factor,
                        high=h / price_factor,
                        low=l  / price_factor,
                        close=c / price_factor,
                        volume=float(v),
                        spread=spread,
                    ))
                except Exception:
                    continue
            return bars
        except Exception:
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ─── Yahoo Finance (último recurso) ──────────────────────────

class YahooFetcher:
    """
    Fallback con datos D1 desde Yahoo Finance.
    Solo se usa si Binance y Dukascopy fallan.
    """
    YAHOO_MAP = {
        "XAUUSDm": "GC=F",
        "EURUSDm": "EURUSD=X",
        "USTECm":  "^NDX",
        "BTCUSDm": "BTC-USD",
        "ETHUSDm": "ETH-USD",
    }

    async def fetch_symbol(self, symbol: str) -> list:
        yahoo_sym = self.YAHOO_MAP.get(symbol, symbol)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{yahoo_sym}?interval=1d&range=max"
        )
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as sess:
                async with sess.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()

            result = data.get("chart", {}).get("result", [])
            if not result:
                return []
            chart = result[0]
            timestamps = chart.get("timestamp", [])
            ohlcv = chart.get("indicators", {}).get("quote", [{}])[0]
            spread = SPREAD_REAL.get(symbol, 0)
            bars = []
            for i, ts in enumerate(timestamps):
                try:
                    bars.append(Bar(
                        time=datetime.fromtimestamp(
                            ts, tz=timezone.utc
                        ).isoformat(),
                        open=float(ohlcv["open"][i]   or 0),
                        high=float(ohlcv["high"][i]   or 0),
                        low=float(ohlcv["low"][i]    or 0),
                        close=float(ohlcv["close"][i]  or 0),
                        volume=float(ohlcv["volume"][i] or 0),
                        spread=spread,
                    ))
                except Exception:
                    continue
            return [b for b in bars if b.close > 0]

        except Exception as e:
            logger.error(f"Yahoo {symbol}: {e}")
            return []


# ─── Fetcher principal — decide la fuente ────────────────────

class DataFetcher:
    """
    Orquestador de fuentes de datos.
    Prioridad: Binance → Dukascopy → Yahoo.
    Resamplea automáticamente al timeframe pedido.
    """

    def __init__(self):
        self.binance   = BinanceFetcher()
        self.dukascopy = DukascopyFetcher()
        self.yahoo     = YahooFetcher()
        self.resampler = DuckDBResampler()

    async def fetch(self, symbol: str, timeframe: str = "H1") -> tuple:
        """
        Retorna (bars: list[Bar], source: str).
        """
        cfg        = EXNESS_SYMBOLS.get(symbol, {})
        start_year = cfg.get("start_year", 2010)

        # 1. Binance (BTC, ETH)
        if symbol in BINANCE_SYMBOLS:
            bars = await self.binance.fetch_symbol(symbol, start_year)
            if bars:
                if timeframe != "M1":
                    bars = self.resampler.resample(bars, timeframe)
                    return bars, f"Binance Vision → {timeframe}"
                return bars, "Binance Vision M1"

        # 2. Dukascopy (EUR, XAU, NAS)
        if symbol in DUKASCOPY_SYMBOLS:
            bars = await self.dukascopy.fetch_symbol(symbol, start_year)
            if bars:
                if timeframe not in ("M1", "M5", "M15", "H1"):
                    bars = self.resampler.resample(bars, timeframe)
                    return bars, f"Dukascopy → {timeframe}"
                return bars, "Dukascopy H1"

        # 3. Yahoo (fallback D1)
        bars = await self.yahoo.fetch_symbol(symbol)
        return bars, "Yahoo Finance D1"

    async def close(self):
        await self.binance.close()
        await self.dukascopy.close()
