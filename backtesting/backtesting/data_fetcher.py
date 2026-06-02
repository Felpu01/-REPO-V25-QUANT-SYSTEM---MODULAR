"""
DATA FETCHER INSTITUCIONAL — Datos históricos reales de Exness MT5
Abre una conexión MetaAPI DEDICADA (separada del stream en vivo)
para descargar histórico con bid/ask/spread real del broker.

Flujo:
  1. Conecta su propia sesión MetaAPI → no toca el bot en vivo
  2. Descarga símbolo por símbolo en chunks de 1000 barras
  3. Cachea localmente (válido 24h) → no re-descarga en cada deploy
  4. Cierra la conexión cuando termina → libera recursos

Sin esta conexión, usa Binance/Dukascopy como fallback.
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

# ─── Config de símbolos ───────────────────────────────────────

# Spread real observado en Exness (del log del bot en vivo)
SPREAD_REAL = {
    "BTCUSDm": 14.0,
    "ETHUSDm": 1.40,
    "XAUUSDm": 0.28,
    "EURUSDm": 0.0002,
    "USTECm":  2.40,
}

# Año de inicio histórico por símbolo en Exness
SYMBOL_START_YEAR = {
    "BTCUSDm": 2017,
    "ETHUSDm": 2017,
    "XAUUSDm": 2004,
    "EURUSDm": 2003,
    "USTECm":  2015,
}

# Fallback Binance (crypto)
BINANCE_SYMBOLS = {
    "BTCUSDm": "BTCUSDT",
    "ETHUSDm": "ETHUSDT",
}

# Fallback Dukascopy (forex/gold/index)
DUKASCOPY_SYMBOLS = {
    "XAUUSDm": "XAUUSD",
    "EURUSDm": "EURUSD",
    "USTECm":  "USTEC",
}

TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440
}

CACHE_DIR     = "backtest_cache"
CACHE_TTL_H   = 24   # horas de validez del caché
CHUNK_SIZE    = 1000  # barras por request a MetaAPI
os.makedirs(CACHE_DIR, exist_ok=True)


# ─── Estructura Bar ───────────────────────────────────────────

@dataclass
class Bar:
    time:   str
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float
    spread: float = 0.0
    bid:    float = 0.0   # precio bid real de Exness
    ask:    float = 0.0   # precio ask real de Exness


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


# ─── DuckDB Resampler ─────────────────────────────────────────

class DuckDBResampler:
    """Resamplea M1 → cualquier TF usando DuckDB (OHLCV + spread promedio)."""

    def resample(self, bars: list, target_tf: str) -> list:
        if not bars or target_tf == "M1":
            return bars
        minutes = TF_MINUTES.get(target_tf, 60)
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
                    FIRST(open   ORDER BY time) AS open,
                    MAX(high)                   AS high,
                    MIN(low)                    AS low,
                    LAST(close   ORDER BY time) AS close,
                    SUM(volume)                 AS volume,
                    AVG(spread)                 AS spread,
                    FIRST(bid    ORDER BY time) AS bid,
                    FIRST(ask    ORDER BY time) AS ask
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
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                )
                for _, row in result.iterrows()
            ]
        except Exception as e:
            logger.warning(f"DuckDB resample error ({target_tf}): {e}")
            return bars


# ─── MetaAPI Fetcher — conexión DEDICADA ──────────────────────

class MetaAPIFetcher:
    """
    Descarga datos históricos reales de Exness MT5 via MetaAPI.
    Abre su PROPIA conexión — no interfiere con el stream del bot en vivo.
    """

    def __init__(self, token: str, account_id: str):
        self._token      = token
        self._account_id = account_id
        self._api        = None
        self._account    = None
        self._connection = None
        self._connected  = False

    async def connect(self) -> bool:
        if not self._token or not self._account_id:
            logger.warning("MetaAPIFetcher: sin token o account_id")
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
        """
        Descarga toda la historia disponible para un símbolo.
        Usa caché local — no re-descarga si ya existe y es fresco.
        """
        cache_file = os.path.join(
            CACHE_DIR, f"{symbol}_{timeframe}_exness.json"
        )
        if _cache_valid(cache_file):
            logger.info(f"Caché Exness hit: {symbol} {timeframe}")
            return _load_cache(cache_file)

        if not self._connected or not self._connection:
            logger.warning(f"MetaAPIFetcher: sin conexión para {symbol}")
            return []

        logger.info(
            f"Descargando {symbol} {timeframe} desde Exness MT5 "
            f"({start_year}-hoy)..."
        )
        all_bars = []
        spread   = SPREAD_REAL.get(symbol, 0)
        now      = datetime.now(timezone.utc)
        minutes  = TF_MINUTES.get(timeframe, 60)

        current_start = datetime(start_year, 1, 1, tzinfo=timezone.utc)

        while current_start < now:
            try:
                end_chunk = current_start + timedelta(
                    minutes=minutes * CHUNK_SIZE
                )
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
                        t = c.get("time", "")
                        if hasattr(t, "isoformat"):
                            t = t.isoformat()

                        o = float(c.get("open",  0))
                        h = float(c.get("high",  0))
                        l = float(c.get("low",   0))
                        cl= float(c.get("close", 0))
                        v = float(c.get("tickVolume", c.get("volume", 1)))

                        # bid/ask real del broker
                        # MetaAPI retorna candles BID por defecto
                        # ask = bid + spread real del símbolo
                        bid = cl
                        ask = cl + spread

                        if cl > 0:
                            chunk_bars.append(Bar(
                                time=str(t),
                                open=o, high=h, low=l, close=cl,
                                volume=v,
                                spread=spread,
                                bid=bid,
                                ask=ask,
                            ))
                    except Exception:
                        continue

                if chunk_bars:
                    all_bars.extend(chunk_bars)
                    logger.info(
                        f"  {symbol} {timeframe}: +{len(chunk_bars)} barras "
                        f"({chunk_bars[0].time[:10]} → "
                        f"{chunk_bars[-1].time[:10]})"
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
                f"spread real: {spread} | "
                f"{all_bars[0].time[:10]} → {all_bars[-1].time[:10]}"
            )
        else:
            logger.warning(
                f"Exness {symbol}: sin datos via MetaAPI — "
                f"se usará fallback"
            )

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


# ─── Binance Vision (fallback crypto) ────────────────────────

class BinanceFetcher:
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
        cache_file = os.path.join(CACHE_DIR, f"{symbol}_M1_binance.json")
        if _cache_valid(cache_file):
            logger.info(f"Binance caché hit: {symbol}")
            return _load_cache(cache_file)

        logger.info(f"Fallback Binance: descargando {symbol} M1...")
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
            logger.info(f"✅ Binance {symbol}: {len(all_bars)} barras M1")
        return all_bars

    async def _fetch_month(self, binance_sym, symbol, year, month):
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
                    ts  = int(parts[0]) / 1000
                    cl  = float(parts[4])
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


# ─── Dukascopy (fallback forex/gold/index) ────────────────────

class DukascopyFetcher:
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

        logger.info(f"Fallback Dukascopy: descargando {symbol} H1...")
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

    async def _fetch_month(self, duka_sym, symbol, year, month):
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

    def _parse_bi5(self, data, symbol, year, month):
        try:
            import lzma, struct
            decompressed = lzma.decompress(data)
            price_factor = {
                "EURUSDm": 100000.0,
                "XAUUSDm": 1000.0,
                "USTECm":  10.0,
            }.get(symbol, 1.0)
            spread   = SPREAD_REAL.get(symbol, 0)
            base_ts  = datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()
            bars     = []
            for i in range(0, len(decompressed) - 23, 24):
                chunk = decompressed[i:i+24]
                try:
                    ms, o, h, l, c, v = struct.unpack(">IIIIIf", chunk)
                    ts  = base_ts + ms / 1000
                    cl  = c / price_factor
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
    Orquestador de fuentes de datos.

    Prioridad:
      1. MetaAPI (Exness MT5 real — bid/ask/spread real del broker)
      2. Binance Vision (fallback crypto — si MetaAPI falla)
      3. Dukascopy (fallback forex/gold/index)

    La conexión MetaAPI es DEDICADA — no interfiere con el stream en vivo.
    Se abre al inicio del backtest y se cierra al terminar.
    """

    def __init__(self, token: str = "", account_id: str = ""):
        self._token      = token or os.getenv("META_API_TOKEN", "")
        self._account_id = account_id or os.getenv("MT5_ACCOUNT_ID", "")
        self.metaapi     = MetaAPIFetcher(self._token, self._account_id)
        self.binance     = BinanceFetcher()
        self.dukascopy   = DukascopyFetcher()
        self.resampler   = DuckDBResampler()
        self._meta_ok    = False

    async def connect(self):
        """Conectar la sesión MetaAPI dedicada."""
        self._meta_ok = await self.metaapi.connect()
        if not self._meta_ok:
            logger.warning(
                "DataFetcher: MetaAPI no disponible — "
                "usando Binance + Dukascopy como fallback"
            )

    async def fetch(self, symbol: str, timeframe: str = "H1") -> tuple:
        """
        Retorna (bars: list[Bar], source: str).
        Barras con spread real de Exness cuando MetaAPI está disponible.
        """
        start_year = SYMBOL_START_YEAR.get(symbol, 2010)

        # ── 1. Exness MT5 via MetaAPI (fuente primaria) ───────
        if self._meta_ok:
            bars = await self.metaapi.fetch_symbol(symbol, timeframe, start_year)
            if len(bars) >= 200:
                return bars, f"Exness MT5 (MetaAPI) {timeframe}"
            logger.warning(
                f"{symbol}: MetaAPI retornó {len(bars)} barras — usando fallback"
            )

        # ── 2. Binance Vision (fallback crypto) ───────────────
        if symbol in BINANCE_SYMBOLS:
            bars = await self.binance.fetch_symbol(symbol, start_year)
            if bars:
                if timeframe != "M1":
                    bars = self.resampler.resample(bars, timeframe)
                    return bars, f"Binance Vision → {timeframe}"
                return bars, "Binance Vision M1"

        # ── 3. Dukascopy (fallback forex/gold/index) ──────────
        if symbol in DUKASCOPY_SYMBOLS:
            bars = await self.dukascopy.fetch_symbol(symbol, start_year)
            if bars:
                if timeframe not in ("M1", "M5", "M15", "H1"):
                    bars = self.resampler.resample(bars, timeframe)
                    return bars, f"Dukascopy → {timeframe}"
                return bars, "Dukascopy H1"

        logger.error(f"DataFetcher: sin datos para {symbol}")
        return [], "sin datos"

    async def close(self):
        await self.metaapi.close()
        await self.binance.close()
        await self.dukascopy.close()
