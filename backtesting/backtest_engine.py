"""
BACKTESTING ENGINE INSTITUCIONAL — V4
Conexión MetaAPI propia e independiente del stream de precios en vivo.
Fuente primaria: MetaAPI SDK → get_historical_candles (conexión dedicada)
Fallback: Binance Vision (BTC/ETH) + Dukascopy (EUR/XAU/NAS)
Persistencia completa: caché JSON + checkpoints + resultados
DuckDB resampling | Walk-Forward 70/30 | Monte Carlo
"""

import asyncio
import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import numpy as np

logger = logging.getLogger("Backtesting")

try:
    import aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

try:
    import duckdb
    DUCKDB_OK = True
except ImportError:
    DUCKDB_OK = False

# ─── Configuración ────────────────────────────────────────────

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

SLIPPAGE_PIPS = {
    "BTCUSDm": 5.0,
    "ETHUSDm": 0.5,
    "XAUUSDm": 0.05,
    "EURUSDm": 0.00005,
    "USTECm":  1.0,
}

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}

CACHE_DIR       = "backtest_cache"
RESULTS_FILE    = "backtest_results.json"
CHECKPOINT_FILE = "backtest_checkpoint.json"
LEARNING_FILE   = "backtest_learning.json"   # persistencia para LearningEngine

os.makedirs(CACHE_DIR, exist_ok=True)


# ─── Estructuras ─────────────────────────────────────────────

@dataclass
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float = 0.0


@dataclass
class BacktestTrade:
    symbol: str
    direction: str
    entry: float
    sl: float
    tp: float
    entry_bar: int
    exit_bar: int = -1
    exit_price: float = 0.0
    outcome: str = "pending"
    pnl_r: float = 0.0
    rr_achieved: float = 0.0
    bars_held: int = 0
    pattern: str = ""
    score: float = 0.0
    slippage: float = 0.0
    year: int = 0
    hour: int = 0       # hora de entrada (para análisis de sesión)
    session: str = ""   # london/ny/asia/overlap


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    source: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    avg_rr: float = 0.0
    avg_bars_held: float = 0.0
    avg_slippage: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    yearly_stats: dict = field(default_factory=dict)
    session_stats: dict = field(default_factory=dict)   # stats por sesión
    pattern_stats: dict = field(default_factory=dict)   # stats por patrón
    best_hours: list = field(default_factory=list)      # mejores horas

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "period": f"{self.start_date} → {self.end_date}",
            "trades": self.total_trades,
            "win_rate": round(self.win_rate * 100, 1),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy": round(self.expectancy, 4),
            "max_drawdown": round(self.max_drawdown * 100, 2),
            "sharpe": round(self.sharpe_ratio, 2),
            "total_r": round(self.total_r, 2),
            "avg_rr": round(self.avg_rr, 2),
            "avg_slippage": round(self.avg_slippage, 5),
            "yearly_stats": self.yearly_stats,
            "session_stats": self.session_stats,
            "pattern_stats": self.pattern_stats,
            "best_hours": self.best_hours,
        }

    def print_report(self):
        print(f"\n{'='*60}")
        print(f"  BACKTEST — {self.symbol} {self.timeframe} [{self.source}]")
        print(f"{'='*60}")
        print(f"  Periodo:        {self.start_date} → {self.end_date}")
        print(f"  Total trades:   {self.total_trades}")
        print(f"  Win Rate:       {self.win_rate*100:.1f}%")
        print(f"  Profit Factor:  {self.profit_factor:.2f}")
        print(f"  Expectancy:     {self.expectancy:.4f}R")
        print(f"  Max Drawdown:   {self.max_drawdown*100:.2f}%")
        print(f"  Sharpe Ratio:   {self.sharpe_ratio:.2f}")
        print(f"  Total R:        {self.total_r:.2f}R")
        print(f"  Avg RR:         {self.avg_rr:.2f}")
        if self.pattern_stats:
            print(f"\n  Mejores patrones:")
            sorted_p = sorted(self.pattern_stats.items(),
                              key=lambda x: x[1].get("wr", 0), reverse=True)
            for pat, st in sorted_p[:3]:
                print(f"    {pat}: WR:{st['wr']:.0f}% Trades:{st['trades']}")
        if self.session_stats:
            print(f"\n  Stats por sesión:")
            for ses, st in self.session_stats.items():
                print(f"    {ses}: WR:{st['wr']:.0f}% Trades:{st['trades']} R:{st['total_r']:.1f}")
        if self.best_hours:
            print(f"\n  Mejores horas UTC: {self.best_hours[:5]}")
        if self.yearly_stats:
            print(f"\n  Stats por año:")
            for yr, st in sorted(self.yearly_stats.items()):
                print(f"    {yr} | WR:{st['wr']:.0f}% | Trades:{st['trades']} | R:{st['total_r']:.1f}")
        print(f"{'='*60}\n")


@dataclass
class MonteCarloResult:
    simulations: int
    median_r: float
    p5: float
    p25: float
    p75: float
    p95: float
    prob_positive: float
    max_dd_median: float

    def print_report(self, symbol: str):
        print(f"\n{'='*50}")
        print(f"  MONTE CARLO — {symbol} ({self.simulations} sims)")
        print(f"{'='*50}")
        print(f"  Mediana retorno:  {self.median_r:.2f}R")
        print(f"  Percentil  5%:    {self.p5:.2f}R")
        print(f"  Percentil 95%:    {self.p95:.2f}R")
        print(f"  Prob. positivo:   {self.prob_positive*100:.1f}%")
        print(f"  Max DD mediana:   {self.max_dd_median*100:.1f}%")
        print(f"{'='*50}\n")


# ─── MetaAPI Fetcher — conexión propia e independiente ────────

class MetaAPIFetcher:
    """
    Abre su PROPIA conexión MetaAPI, completamente separada
    del stream de precios en vivo. Así no bloquea el bot.
    Se conecta → descarga → se desconecta.
    """

    def __init__(self, token: str, account_id: str):
        self._token      = token
        self._account_id = account_id
        self._api        = None
        self._account    = None
        self._connection = None

    async def connect(self) -> bool:
        """Conecta con una nueva sesión independiente."""
        if not self._token or not self._account_id:
            return False
        try:
            from metaapi_cloud_sdk import MetaApi
            logger.info("Backtest: abriendo conexión MetaAPI dedicada...")
            self._api     = MetaApi(self._token)
            self._account = await self._api.metatrader_account_api.get_account(
                self._account_id
            )
            if self._account.state not in ['DEPLOYING', 'DEPLOYED']:
                await self._account.deploy()
            await self._account.wait_connected()

            self._connection = self._account.get_rpc_connection()
            await self._connection.connect()
            await self._connection.wait_synchronized()
            logger.info("✅ Backtest: conexión MetaAPI dedicada lista")
            return True
        except Exception as e:
            logger.warning(f"Backtest MetaAPI connect: {e}")
            self._connection = None
            return False

    async def fetch_symbol(
        self, symbol: str, timeframe: str = "H1", start_year: int = 2010
    ) -> list:
        """Descarga historia completa para un símbolo."""
        cache_file = os.path.join(CACHE_DIR, f"{symbol}_{timeframe}_exness.json")

        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            age_days = (datetime.now().timestamp() - mtime) / 86400
            if age_days < 1:  # cache válido por 1 día
                logger.info(f"Cache hit ({age_days:.1f}d): {symbol} {timeframe}")
                with open(cache_file, "r") as f:
                    data = json.load(f)
                return [Bar(**b) for b in data]

        if not self._connection:
            logger.warning(f"Sin conexión MetaAPI para {symbol}")
            return []

        logger.info(f"Descargando {symbol} H1 desde MetaAPI Exness ({start_year}-hoy)...")
        all_bars = []
        spread   = SPREAD_REAL.get(symbol, 0)
        now      = datetime.now(timezone.utc)
        minutes  = TF_MINUTES.get(timeframe, 60)
        chunk_sz = 1000

        current_start = datetime(start_year, 1, 1, tzinfo=timezone.utc)

        while current_start < now:
            try:
                end_chunk = current_start + timedelta(minutes=minutes * chunk_sz)
                if end_chunk > now:
                    end_chunk = now

                candles = await self._connection.get_historical_candles(
                    symbol, timeframe, current_start, end_chunk, chunk_sz
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
                        b = Bar(
                            time=str(t),
                            open=float(c.get("open", 0)),
                            high=float(c.get("high", 0)),
                            low=float(c.get("low", 0)),
                            close=float(c.get("close", 0)),
                            volume=float(c.get("tickVolume", c.get("volume", 1))),
                            spread=spread,
                        )
                        if b.close > 0:
                            chunk_bars.append(b)
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
                current_start = current_start + timedelta(days=30)
                await asyncio.sleep(2.0)

        if all_bars:
            all_bars.sort(key=lambda x: x.time)
            with open(cache_file, "w") as f:
                json.dump([vars(b) for b in all_bars], f)
            logger.info(
                f"✅ {symbol}: {len(all_bars)} barras cacheadas "
                f"({all_bars[0].time[:10]} → {all_bars[-1].time[:10]})"
            )
        else:
            logger.warning(f"MetaAPI {symbol}: sin datos")

        return all_bars

    async def close(self):
        try:
            if self._connection:
                await self._connection.close()
            self._connection = None
            self._account    = None
            self._api        = None
            logger.info("Backtest: conexión MetaAPI dedicada cerrada")
        except Exception:
            pass


# ─── Fetchers Fallback ────────────────────────────────────────

class BinanceFetcher:
    BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2017) -> list:
        binance_sym = BINANCE_SYMBOLS.get(symbol, symbol.replace("m", "USDT"))
        cache_file  = os.path.join(CACHE_DIR, f"{symbol}_M1_binance.json")

        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            if (datetime.now().timestamp() - mtime) / 86400 < 1:
                logger.info(f"Binance cache hit: {symbol}")
                with open(cache_file, "r") as f:
                    data = json.load(f)
                return [Bar(**b) for b in data]

        logger.info(f"Descargando {symbol} desde Binance Vision...")
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
            with open(cache_file, "w") as f:
                json.dump([vars(b) for b in all_bars], f)
            logger.info(f"✅ Binance {symbol}: {len(all_bars)} barras cacheadas")
        return all_bars

    async def _fetch_month(self, binance_sym, symbol, year, month):
        url = (
            f"{self.BASE_URL}/{binance_sym}/1m/"
            f"{binance_sym}-1m-{year}-{month:02d}.zip"
        )
        try:
            sess = await self._get_session()
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    return []
                content = await r.read()
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    with z.open(z.namelist()[0]) as f:
                        lines = f.read().decode().strip().split("\n")
                bars = []
                spread = SPREAD_REAL.get(symbol, 0)
                for line in lines:
                    parts = line.split(",")
                    if len(parts) < 6:
                        continue
                    try:
                        ts = int(parts[0]) / 1000
                        bars.append(Bar(
                            time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                            open=float(parts[1]), high=float(parts[2]),
                            low=float(parts[3]),  close=float(parts[4]),
                            volume=float(parts[5]), spread=spread,
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


class DukascopyFetcher:
    BASE_URL = "https://datafeed.dukascopy.com/datafeed"

    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"})
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2003) -> list:
        duka_sym   = DUKASCOPY_SYMBOLS.get(symbol, symbol)
        cache_file = os.path.join(CACHE_DIR, f"{symbol}_H1_dukascopy.json")

        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            if (datetime.now().timestamp() - mtime) / 86400 < 1:
                logger.info(f"Dukascopy cache hit: {symbol}")
                with open(cache_file, "r") as f:
                    data = json.load(f)
                return [Bar(**b) for b in data]

        logger.info(f"Descargando {symbol} desde Dukascopy ({start_year})...")
        all_bars = []
        now = datetime.now(timezone.utc)

        for year in range(start_year, now.year + 1):
            for month in range(0, 12):
                if year == now.year and month >= now.month - 1:
                    break
                bars = await self._fetch_month(duka_sym, symbol, year, month)
                if bars:
                    all_bars.extend(bars)
                await asyncio.sleep(0.2)
            logger.info(f"  {symbol} {year}: {len([b for b in all_bars if b.time[:4]==str(year)])} barras H1")

        if all_bars:
            with open(cache_file, "w") as f:
                json.dump([vars(b) for b in all_bars], f)
            logger.info(f"✅ Dukascopy {symbol}: {len(all_bars)} barras cacheadas")
        return all_bars

    async def _fetch_month(self, duka_sym, symbol, year, month):
        url = f"{self.BASE_URL}/{duka_sym}/{year}/{month:02d}/BID_candles_hour_1.bi5"
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

    def _parse_bi5(self, data, symbol, year, month):
        try:
            import lzma, struct
            decompressed = lzma.decompress(data)
            bars = []
            spread = SPREAD_REAL.get(symbol, 0)
            price_factor = {
                "EURUSDm": 100000.0, "XAUUSDm": 1000.0, "USTECm": 10.0
            }.get(symbol, 1.0)
            base_ts = datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()
            for i in range(0, len(decompressed) - 23, 24):
                chunk = decompressed[i:i+24]
                try:
                    ms, o, h, l, c, v = struct.unpack(">IIIIIf", chunk)
                    ts = base_ts + ms / 1000
                    bars.append(Bar(
                        time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        open=o/price_factor, high=h/price_factor,
                        low=l/price_factor, close=c/price_factor,
                        volume=float(v), spread=spread,
                    ))
                except Exception:
                    continue
            return bars
        except Exception:
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class YahooFetcher:
    async def fetch_symbol(self, symbol: str) -> list:
        YAHOO_MAP = {
            "XAUUSDm": "GC=F", "EURUSDm": "EURUSD=X",
            "USTECm": "^NDX", "BTCUSDm": "BTC-USD", "ETHUSDm": "ETH-USD",
        }
        yahoo_sym = YAHOO_MAP.get(symbol, symbol)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1d&range=max"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as sess:
                async with sess.get(url, headers={"User-Agent": "Mozilla/5.0"},
                                    timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
                    result = data.get("chart", {}).get("result", [])
                    if not result:
                        return []
                    chart = result[0]
                    timestamps = chart.get("timestamp", [])
                    ohlcv = chart.get("indicators", {}).get("quote", [{}])[0]
                    bars = []
                    spread = SPREAD_REAL.get(symbol, 0)
                    for i, ts in enumerate(timestamps):
                        try:
                            bars.append(Bar(
                                time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                                open=float(ohlcv["open"][i] or 0),
                                high=float(ohlcv["high"][i] or 0),
                                low=float(ohlcv["low"][i] or 0),
                                close=float(ohlcv["close"][i] or 0),
                                volume=float(ohlcv["volume"][i] or 0),
                                spread=spread,
                            ))
                        except Exception:
                            continue
                    return [b for b in bars if b.close > 0]
        except Exception as e:
            logger.error(f"Yahoo {symbol}: {e}")
            return []


# ─── DuckDB Resampler ─────────────────────────────────────────

class DuckDBResampler:
    def resample(self, bars: list, target_tf: str) -> list:
        if not DUCKDB_OK or not bars:
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
                    FIRST(open ORDER BY time) AS open,
                    MAX(high) AS high, MIN(low) AS low,
                    LAST(close ORDER BY time) AS close,
                    SUM(volume) AS volume, AVG(spread) AS spread
                FROM bars_df GROUP BY bucket ORDER BY bucket ASC
            """
            result = con.execute(query).df()
            con.close()
            return [Bar(
                time=str(row["bucket"]), open=float(row["open"]),
                high=float(row["high"]), low=float(row["low"]),
                close=float(row["close"]), volume=float(row["volume"]),
                spread=float(row["spread"]),
            ) for _, row in result.iterrows()]
        except Exception as e:
            logger.warning(f"DuckDB resample: {e}")
            return bars


# ─── Helpers de sesión ────────────────────────────────────────

def get_session(hour_utc: int) -> str:
    if 0 <= hour_utc < 7:
        return "asia"
    elif 7 <= hour_utc < 9:
        return "london_open"
    elif 9 <= hour_utc < 12:
        return "london"
    elif 12 <= hour_utc < 17:
        return "ny_overlap"
    elif 17 <= hour_utc < 22:
        return "ny"
    else:
        return "close"


# ─── Backtest Engine ──────────────────────────────────────────

class BacktestEngine:
    def __init__(self, token: str = "", account_id: str = "", connection=None):
        self._token      = token or os.getenv("META_API_TOKEN", "")
        self._account_id = account_id or os.getenv("MT5_ACCOUNT_ID", "")
        # Siempre usar conexión propia — ignorar la del bot en vivo
        self.metaapi   = MetaAPIFetcher(self._token, self._account_id) if self._token else None
        self.binance   = BinanceFetcher()
        self.dukascopy = DukascopyFetcher()
        self.yahoo     = YahooFetcher()
        self.resampler = DuckDBResampler()
        self._checkpoint = self._load_checkpoint()
        logger.info("BacktestEngine V4 iniciado — conexión MetaAPI dedicada")

    def _load_checkpoint(self) -> dict:
        if os.path.exists(CHECKPOINT_FILE):
            try:
                with open(CHECKPOINT_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_checkpoint(self, symbol: str, year: int, stats: dict):
        self._checkpoint[f"{symbol}_{year}"] = {
            "year": year, "stats": stats,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump(self._checkpoint, f, indent=2)
        except Exception as e:
            logger.error(f"Checkpoint save: {e}")

    def _is_checkpointed(self, symbol: str, year: int) -> bool:
        return f"{symbol}_{year}" in self._checkpoint

    async def run(
        self, symbol: str, timeframe: str = "H1",
        min_rr: float = 2.5, score_threshold: float = 0.65,
        walk_forward: bool = True,
    ) -> BacktestResult:
        logger.info(f"{'='*55}")
        logger.info(f"Backtest {symbol} {timeframe}")

        bars, source = await self._fetch_data(symbol, timeframe)

        if len(bars) < 200:
            logger.error(f"Datos insuficientes {symbol}: {len(bars)} barras")
            return BacktestResult(symbol=symbol, timeframe=timeframe,
                                  start_date="N/A", end_date="N/A")

        logger.info(f"{symbol} [{source}]: {len(bars)} barras ({bars[0].time[:10]} → {bars[-1].time[:10]})")

        if walk_forward and len(bars) > 1000:
            result = self._walk_forward(bars, symbol, timeframe, source, min_rr, score_threshold)
        else:
            trades = self._simulate_trades(bars, symbol, min_rr, score_threshold)
            result = self._calculate_metrics(trades, bars, symbol, timeframe, source)

        result.start_date = bars[0].time[:10]
        result.end_date   = bars[-1].time[:10]
        result.source     = source
        self._save_result(result)
        result.print_report()
        return result

    async def run_all(self, symbols: list = None) -> dict:
        if symbols is None:
            symbols = list(EXNESS_SYMBOLS.keys())

        logger.info(f"Backtest completo — {len(symbols)} activos: {', '.join(symbols)}")

        # Conectar MetaAPI propio ANTES de arrancar
        if self.metaapi:
            connected = await self.metaapi.connect()
            if not connected:
                logger.warning("MetaAPI dedicado no disponible — usando Binance/Dukascopy")
                self.metaapi = None

        results = {}
        for sym in symbols:
            try:
                res = await self.run(sym)
                results[sym] = res
                await asyncio.sleep(2)  # pausa entre activos
            except Exception as e:
                logger.error(f"Backtest {sym} fallido: {e}")

        self._print_summary(results)

        for sym, res in results.items():
            if res.total_trades >= 30:
                mc = self.monte_carlo(res)
                mc.print_report(sym)

        # Guardar insights de aprendizaje consolidados
        self._save_learning_insights(results)

        return results

    async def _fetch_data(self, symbol: str, timeframe: str) -> tuple:
        cfg        = EXNESS_SYMBOLS.get(symbol, {})
        start_year = cfg.get("start_year", 2010)

        # 1. MetaAPI dedicado (conexión propia)
        if self.metaapi and self.metaapi._connection:
            bars = await self.metaapi.fetch_symbol(symbol, timeframe, start_year)
            if len(bars) >= 200:
                return bars, f"MetaAPI/Exness {timeframe}"
            else:
                logger.warning(f"{symbol}: MetaAPI insuficiente ({len(bars)} barras), fallback")

        # 2. Binance Vision (crypto)
        if symbol in BINANCE_SYMBOLS:
            bars = await self.binance.fetch_symbol(symbol, start_year)
            if bars and timeframe != "M1":
                bars = self.resampler.resample(bars, timeframe)
                return bars, f"Binance Vision → {timeframe}"
            if bars:
                return bars, "Binance Vision M1"

        # 3. Dukascopy (Forex/Gold/Index)
        if symbol in DUKASCOPY_SYMBOLS:
            bars = await self.dukascopy.fetch_symbol(symbol, start_year)
            if bars and timeframe not in ["M1","M5","M15","H1"]:
                bars = self.resampler.resample(bars, timeframe)
                return bars, f"Dukascopy → {timeframe}"
            if bars:
                return bars, "Dukascopy H1"

        # 4. Yahoo (último recurso)
        bars = await self.yahoo.fetch_symbol(symbol)
        return bars, "Yahoo Finance D1"

    def _walk_forward(self, bars, symbol, timeframe, source, min_rr, score_threshold):
        logger.info(f"{symbol}: Walk-Forward 70/30...")
        split      = int(len(bars) * 0.70)
        train_bars = bars[:split]
        test_bars  = bars[split:]

        logger.info(f"  Train: {train_bars[0].time[:10]} → {train_bars[-1].time[:10]} ({len(train_bars)} barras)")
        logger.info(f"  Test:  {test_bars[0].time[:10]} → {test_bars[-1].time[:10]} ({len(test_bars)} barras)")

        train_trades = self._simulate_trades(train_bars, symbol, min_rr, score_threshold)
        train_result = self._calculate_metrics(train_trades, train_bars, symbol, timeframe, "TRAIN")
        logger.info(f"  Train WR:{train_result.win_rate*100:.1f}% PF:{train_result.profit_factor:.2f} Trades:{train_result.total_trades}")

        test_trades = self._simulate_trades(test_bars, symbol, min_rr, score_threshold)
        test_result = self._calculate_metrics(test_trades, test_bars, symbol, timeframe, "TEST OOS")
        logger.info(f"  Test  WR:{test_result.win_rate*100:.1f}% PF:{test_result.profit_factor:.2f} Trades:{test_result.total_trades}")

        test_result.yearly_stats = self._yearly_stats(test_trades)
        test_result.session_stats = self._session_stats(test_trades)
        test_result.pattern_stats = self._pattern_stats(test_trades)
        test_result.best_hours    = self._best_hours(test_trades)
        return test_result

    def _simulate_trades(self, bars, symbol, min_rr, score_threshold):
        trades   = []
        lookback = 20

        for i in range(lookback + 5, len(bars) - 10):
            window  = bars[max(0, i-lookback):i]
            bar     = bars[i]
            current = bar.close
            atr_val = self._atr(bars, i)
            if atr_val == 0 or not window:
                continue

            swing_high = max(b.high for b in window)
            swing_low  = min(b.low  for b in window)
            avg_vol    = np.mean([b.volume for b in window])

            # Hora UTC para análisis de sesión
            try:
                hour_utc = int(bar.time[11:13])
            except Exception:
                hour_utc = 0

            direction = score = None
            pattern   = ""

            if (current > swing_high and bar.close > window[-1].close
                    and bar.volume > avg_vol * 1.3):
                direction, score, pattern = "buy", 0.68, "BOS_BULL"

            elif (current < swing_low and bar.close < window[-1].close
                    and bar.volume > avg_vol * 1.3):
                direction, score, pattern = "sell", 0.68, "BOS_BEAR"

            elif (i >= 3 and bars[i-2].close < bars[i-2].open
                    and bars[i-1].close > bars[i-2].high and current > bars[i-1].close):
                direction, score, pattern = "buy", 0.72, "OB_BULL"

            elif (i >= 3 and bars[i-2].close > bars[i-2].open
                    and bars[i-1].close < bars[i-2].low and current < bars[i-1].close):
                direction, score, pattern = "sell", 0.72, "OB_BEAR"

            elif (i >= 2 and bar.low > bars[i-2].high
                    and (bar.low - bars[i-2].high) / (current + 1e-9) > 0.001):
                direction, score, pattern = "buy", 0.66, "FVG_BULL"

            elif (i >= 2 and bar.high < bars[i-2].low
                    and (bars[i-2].low - bar.high) / (current + 1e-9) > 0.001):
                direction, score, pattern = "sell", 0.66, "FVG_BEAR"

            if direction is None or score < score_threshold:
                continue

            slip   = self._slippage(symbol)
            spread = bar.spread

            if direction == "buy":
                entry = current + slip + spread
                sl    = entry - atr_val * 1.5
                tp    = entry + atr_val * 1.5 * min_rr
            else:
                entry = current - slip - spread
                sl    = entry + atr_val * 1.5
                tp    = entry - atr_val * 1.5 * min_rr

            risk = abs(entry - sl)
            if risk == 0:
                continue

            year = int(bar.time[:4]) if bar.time else 0
            trade = BacktestTrade(
                symbol=symbol, direction=direction,
                entry=entry, sl=sl, tp=tp,
                entry_bar=i, score=score,
                pattern=pattern, slippage=slip, year=year,
                hour=hour_utc, session=get_session(hour_utc),
            )
            trade = self._simulate_outcome(trade, bars, i, min_rr)
            trades.append(trade)

            if year and not self._is_checkpointed(symbol, year):
                yr_t = [t for t in trades if t.year == year]
                if yr_t:
                    yr_w = sum(1 for t in yr_t if t.outcome == "win")
                    self._save_checkpoint(symbol, year, {
                        "trades": len(yr_t),
                        "wr": round(yr_w / len(yr_t) * 100, 1),
                        "total_r": round(sum(t.pnl_r for t in yr_t), 2),
                    })
        return trades

    def _simulate_outcome(self, trade, bars, entry_bar, min_rr):
        be_activated = False
        trailing_sl  = trade.sl
        risk         = abs(trade.entry - trade.sl)

        for j in range(entry_bar + 1, min(entry_bar + 100, len(bars))):
            bar = bars[j]
            if trade.direction == "buy":
                if not be_activated and bar.high >= trade.entry + risk:
                    trailing_sl  = trade.entry
                    be_activated = True
                if be_activated:
                    new_trail = bar.high - risk * 0.8
                    if new_trail > trailing_sl:
                        trailing_sl = new_trail
                if bar.low <= trailing_sl:
                    trade.outcome    = "win" if be_activated else "loss"
                    trade.exit_price = trailing_sl
                    trade.exit_bar   = j
                    pnl = trailing_sl - trade.entry
                    trade.pnl_r      = pnl / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break
                if bar.high >= trade.tp:
                    trade.outcome    = "win"
                    trade.exit_price = trade.tp
                    trade.exit_bar   = j
                    trade.pnl_r      = (trade.tp - trade.entry) / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break
            else:
                if not be_activated and bar.low <= trade.entry - risk:
                    trailing_sl  = trade.entry
                    be_activated = True
                if be_activated:
                    new_trail = bar.low + risk * 0.8
                    if new_trail < trailing_sl:
                        trailing_sl = new_trail
                if bar.high >= trailing_sl:
                    trade.outcome    = "win" if be_activated else "loss"
                    trade.exit_price = trailing_sl
                    trade.exit_bar   = j
                    pnl = trade.entry - trailing_sl
                    trade.pnl_r      = pnl / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break
                if bar.low <= trade.tp:
                    trade.outcome    = "win"
                    trade.exit_price = trade.tp
                    trade.exit_bar   = j
                    trade.pnl_r      = (trade.entry - trade.tp) / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break

        if trade.outcome == "pending":
            trade.outcome     = "breakeven"
            trade.exit_price  = bars[min(entry_bar+30, len(bars)-1)].close
            trade.pnl_r       = 0.0
            trade.rr_achieved = 0.0

        trade.bars_held = max(0, (trade.exit_bar if trade.exit_bar > 0 else entry_bar+30) - entry_bar)
        return trade

    def _calculate_metrics(self, trades, bars, symbol, timeframe, source):
        result = BacktestResult(symbol=symbol, timeframe=timeframe,
                                start_date="", end_date="", source=source)
        result.trades       = trades
        result.total_trades = len(trades)
        if not trades:
            return result

        wins   = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        result.wins       = len(wins)
        result.losses     = len(losses)
        result.breakevens = len(trades) - len(wins) - len(losses)
        result.win_rate   = len(wins) / len(trades)

        win_rs  = [t.pnl_r for t in wins]
        loss_rs = [abs(t.pnl_r) for t in losses]
        result.avg_win_r     = float(np.mean(win_rs))  if win_rs  else 0.0
        result.avg_loss_r    = float(np.mean(loss_rs)) if loss_rs else 0.0
        result.profit_factor = sum(win_rs) / (sum(loss_rs) + 1e-9)
        result.total_r       = sum(t.pnl_r for t in trades)
        result.expectancy    = (result.win_rate * result.avg_win_r) - ((1 - result.win_rate) * result.avg_loss_r)

        rrs = [t.rr_achieved for t in trades if t.rr_achieved > 0]
        result.avg_rr = float(np.mean(rrs)) if rrs else 0.0

        bh = [t.bars_held for t in trades if t.bars_held > 0]
        result.avg_bars_held = float(np.mean(bh)) if bh else 0.0
        result.avg_slippage  = float(np.mean([t.slippage for t in trades]))

        equity = peak = max_dd = 0.0
        curve  = [0.0]
        for t in trades:
            equity += t.pnl_r
            curve.append(equity)
            if equity > peak: peak = equity
            dd = (peak - equity) / (abs(peak) + 1e-9)
            if dd > max_dd: max_dd = dd

        result.equity_curve = curve
        result.max_drawdown = max_dd

        returns = [t.pnl_r for t in trades]
        if len(returns) > 1:
            result.sharpe_ratio = (np.mean(returns) / (np.std(returns) + 1e-9)) * np.sqrt(252)

        result.yearly_stats  = self._yearly_stats(trades)
        result.session_stats = self._session_stats(trades)
        result.pattern_stats = self._pattern_stats(trades)
        result.best_hours    = self._best_hours(trades)
        return result

    def _yearly_stats(self, trades):
        years = {}
        for t in trades:
            y = str(t.year)
            if y not in years:
                years[y] = {"trades": 0, "wins": 0, "total_r": 0.0}
            years[y]["trades"]  += 1
            years[y]["total_r"] += t.pnl_r
            if t.outcome == "win":
                years[y]["wins"] += 1
        for y, st in years.items():
            st["wr"]      = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0
            st["total_r"] = round(st["total_r"], 2)
        return years

    def _session_stats(self, trades):
        sessions = {}
        for t in trades:
            s = t.session or "unknown"
            if s not in sessions:
                sessions[s] = {"trades": 0, "wins": 0, "total_r": 0.0}
            sessions[s]["trades"]  += 1
            sessions[s]["total_r"] += t.pnl_r
            if t.outcome == "win":
                sessions[s]["wins"] += 1
        for s, st in sessions.items():
            st["wr"]      = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0
            st["total_r"] = round(st["total_r"], 2)
        return sessions

    def _pattern_stats(self, trades):
        patterns = {}
        for t in trades:
            p = t.pattern or "unknown"
            if p not in patterns:
                patterns[p] = {"trades": 0, "wins": 0, "total_r": 0.0}
            patterns[p]["trades"]  += 1
            patterns[p]["total_r"] += t.pnl_r
            if t.outcome == "win":
                patterns[p]["wins"] += 1
        for p, st in patterns.items():
            st["wr"]      = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0
            st["total_r"] = round(st["total_r"], 2)
        return patterns

    def _best_hours(self, trades):
        hours = {}
        for t in trades:
            h = t.hour
            if h not in hours:
                hours[h] = {"trades": 0, "wins": 0}
            hours[h]["trades"] += 1
            if t.outcome == "win":
                hours[h]["wins"] += 1
        # Ordenar por win rate, mínimo 5 trades
        ranked = sorted(
            [(h, st["wins"] / st["trades"] * 100)
             for h, st in hours.items() if st["trades"] >= 5],
            key=lambda x: x[1], reverse=True
        )
        return [h for h, _ in ranked[:8]]

    def _save_learning_insights(self, results: dict):
        """
        Guarda insights consolidados para que el LearningEngine los use
        al arrancar — patrones ganadores, mejores sesiones, mejores horas.
        """
        insights = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": {}
        }

        for sym, res in results.items():
            if res.total_trades < 10:
                continue

            # Mejor patrón por WR
            best_pattern = max(
                res.pattern_stats.items(),
                key=lambda x: x[1].get("wr", 0),
                default=("N/A", {})
            )[0] if res.pattern_stats else "N/A"

            # Mejor sesión
            best_session = max(
                res.session_stats.items(),
                key=lambda x: x[1].get("wr", 0),
                default=("N/A", {})
            )[0] if res.session_stats else "N/A"

            insights["symbols"][sym] = {
                "win_rate": round(res.win_rate * 100, 1),
                "profit_factor": round(res.profit_factor, 2),
                "best_pattern": best_pattern,
                "best_session": best_session,
                "best_hours": res.best_hours[:5],
                "pattern_stats": res.pattern_stats,
                "session_stats": res.session_stats,
                "yearly_stats": res.yearly_stats,
                "total_trades": res.total_trades,
                "source": res.source,
            }

        try:
            with open(LEARNING_FILE, "w") as f:
                json.dump(insights, f, indent=2)
            logger.info(f"✅ Insights de aprendizaje guardados → {LEARNING_FILE}")
            # Log resumen
            for sym, data in insights["symbols"].items():
                logger.info(
                    f"  {sym}: WR:{data['win_rate']}% | "
                    f"Patron:{data['best_pattern']} | "
                    f"Sesion:{data['best_session']} | "
                    f"Horas:{data['best_hours']}"
                )
        except Exception as e:
            logger.error(f"Error guardando insights: {e}")

    def _atr(self, bars, idx, period=14):
        window = bars[max(0, idx-period):idx+1]
        if len(window) < 2:
            return 0.0
        trs = [max(
            window[i].high - window[i].low,
            abs(window[i].high - window[i-1].close),
            abs(window[i].low  - window[i-1].close),
        ) for i in range(1, len(window))]
        return float(np.mean(trs)) if trs else 0.0

    def _slippage(self, symbol):
        return SLIPPAGE_PIPS.get(symbol, 0.0001) * np.random.uniform(0.5, 1.5)

    def _save_result(self, result):
        try:
            all_results = {}
            if os.path.exists(RESULTS_FILE):
                with open(RESULTS_FILE, "r") as f:
                    all_results = json.load(f)
            all_results[f"{result.symbol}_{result.timeframe}"] = result.to_dict()
            with open(RESULTS_FILE, "w") as f:
                json.dump(all_results, f, indent=2)
        except Exception as e:
            logger.error(f"Save result: {e}")

    def _print_summary(self, results):
        print(f"\n{'='*65}")
        print(f"  RESUMEN BACKTEST INSTITUCIONAL — {len(results)} ACTIVOS")
        print(f"{'='*65}")
        for sym, res in results.items():
            print(f"  {sym:10} | WR:{res.win_rate*100:.1f}% | PF:{res.profit_factor:.2f} | "
                  f"DD:{res.max_drawdown*100:.1f}% | R:{res.total_r:.1f} | "
                  f"Trades:{res.total_trades} | [{res.source}]")
        print(f"{'='*65}\n")

    def monte_carlo(self, result, simulations=1000, trades_per_sim=100):
        if not result.trades:
            return MonteCarloResult(simulations, 0, 0, 0, 0, 0, 0, 0)
        returns       = [t.pnl_r for t in result.trades]
        sim_returns   = []
        sim_drawdowns = []
        for _ in range(simulations):
            sample   = np.random.choice(returns, size=trades_per_sim, replace=True)
            sim_returns.append(float(np.sum(sample)))
            equity = peak = max_dd = 0.0
            for r in sample:
                equity += r
                if equity > peak: peak = equity
                dd = (peak - equity) / (abs(peak) + 1e-9)
                if dd > max_dd: max_dd = dd
            sim_drawdowns.append(max_dd)
        arr = np.array(sim_returns)
        return MonteCarloResult(
            simulations=simulations,
            median_r=float(np.median(arr)),
            p5=float(np.percentile(arr, 5)),
            p25=float(np.percentile(arr, 25)),
            p75=float(np.percentile(arr, 75)),
            p95=float(np.percentile(arr, 95)),
            prob_positive=float(np.mean(arr > 0)),
            max_dd_median=float(np.median(sim_drawdowns)),
        )

    async def close(self):
        if self.metaapi:
            await self.metaapi.close()
        await self.binance.close()
        await self.dukascopy.close()
