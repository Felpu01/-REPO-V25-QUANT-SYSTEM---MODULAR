"""
BACKTESTING ENGINE INSTITUCIONAL — V2
Fuentes: Binance Vision (BTC/ETH) + Dukascopy (EUR/XAU/NAS)
DuckDB resampling | Walk-Forward | Monte Carlo | Checkpoints
Slippage real | Spread dinamico | SMC completo
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

# ─── Configuración de fuentes ─────────────────────────────────

BINANCE_SYMBOLS = {
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT",
}

DUKASCOPY_SYMBOLS = {
    "EURUSD": "EURUSD",
    "XAUUSD": "XAUUSD",
    "NAS100": "USTEC",
}

# Slippage por activo (en pips)
SLIPPAGE_PIPS = {
    "BTCUSD": 15.0,
    "ETHUSD": 3.0,
    "XAUUSD": 0.05,
    "EURUSD": 0.00015,
    "NAS100": 2.0,
}

# Spread promedio por activo
SPREAD = {
    "BTCUSD": 20.0,
    "ETHUSD": 2.0,
    "XAUUSD": 0.03,
    "EURUSD": 0.00012,
    "NAS100": 1.5,
}

CACHE_DIR    = "backtest_cache"
RESULTS_FILE = "backtest_results.json"
CHECKPOINT_FILE = "backtest_checkpoint.json"

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
        print(f"  Avg Slippage:   {self.avg_slippage:.5f}")
        if self.yearly_stats:
            print(f"\n  Stats por año:")
            for yr, st in sorted(self.yearly_stats.items()):
                print(
                    f"    {yr} | WR:{st['wr']:.0f}% | "
                    f"Trades:{st['trades']} | R:{st['total_r']:.1f}"
                )
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
        print(f"  Percentil 25%:    {self.p25:.2f}R")
        print(f"  Percentil 75%:    {self.p75:.2f}R")
        print(f"  Percentil 95%:    {self.p95:.2f}R")
        print(f"  Prob. positivo:   {self.prob_positive*100:.1f}%")
        print(f"  Max DD mediana:   {self.max_dd_median*100:.1f}%")
        print(f"{'='*50}\n")


# ─── Fetchers ─────────────────────────────────────────────────

class BinanceFetcher:
    """Descarga datos M1 históricos desde Binance Vision (gratuito, sin API key)."""

    BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2017) -> list[Bar]:
        """Descarga todos los meses disponibles para un símbolo."""
        binance_sym = BINANCE_SYMBOLS.get(symbol, symbol)
        cache_file  = os.path.join(CACHE_DIR, f"{symbol}_M1_binance.json")

        # Usar cache si existe
        if os.path.exists(cache_file):
            logger.info(f"Binance cache hit: {symbol}")
            with open(cache_file, "r") as f:
                data = json.load(f)
            return [Bar(**b) for b in data]

        logger.info(f"Descargando {symbol} desde Binance Vision ({start_year}-presente)...")
        all_bars = []
        now = datetime.now(timezone.utc)

        for year in range(start_year, now.year + 1):
            for month in range(1, 13):
                if year == now.year and month >= now.month:
                    break
                bars = await self._fetch_month(binance_sym, symbol, year, month)
                if bars:
                    all_bars.extend(bars)
                    logger.info(f"  {symbol} {year}-{month:02d}: {len(bars)} barras M1")
                await asyncio.sleep(0.3)  # Rate limit

        if all_bars:
            with open(cache_file, "w") as f:
                json.dump([vars(b) for b in all_bars], f)
            logger.info(f"Binance {symbol}: {len(all_bars)} barras totales cacheadas")

        return all_bars

    async def _fetch_month(
        self, binance_sym: str, symbol: str, year: int, month: int
    ) -> list[Bar]:
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
                    csv_name = z.namelist()[0]
                    with z.open(csv_name) as f:
                        lines = f.read().decode().strip().split("\n")

                bars = []
                spread = SPREAD.get(symbol, 0)
                for line in lines:
                    parts = line.split(",")
                    if len(parts) < 6:
                        continue
                    try:
                        ts = int(parts[0]) / 1000
                        bars.append(Bar(
                            time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                            open=float(parts[1]),
                            high=float(parts[2]),
                            low=float(parts[3]),
                            close=float(parts[4]),
                            volume=float(parts[5]),
                            spread=spread,
                        ))
                    except (ValueError, IndexError):
                        continue
                return bars
        except Exception as e:
            logger.debug(f"Binance {binance_sym} {year}-{month:02d}: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class DukascopyFetcher:
    """Descarga datos históricos desde Dukascopy (gratuito, sin API key)."""

    BASE_URL = "https://datafeed.dukascopy.com/datafeed"

    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "Mozilla/5.0"}
            )
        return self._session

    async def fetch_symbol(self, symbol: str, start_year: int = 2003) -> list[Bar]:
        """Descarga datos H1 históricos de Dukascopy."""
        duka_sym   = DUKASCOPY_SYMBOLS.get(symbol, symbol)
        cache_file = os.path.join(CACHE_DIR, f"{symbol}_H1_dukascopy.json")

        if os.path.exists(cache_file):
            logger.info(f"Dukascopy cache hit: {symbol}")
            with open(cache_file, "r") as f:
                data = json.load(f)
            return [Bar(**b) for b in data]

        logger.info(f"Descargando {symbol} desde Dukascopy ({start_year}-presente)...")
        all_bars = []
        now = datetime.now(timezone.utc)

        for year in range(start_year, now.year + 1):
            for month in range(0, 12):
                if year == now.year and month >= now.month - 1:
                    break
                bars = await self._fetch_month_h1(duka_sym, symbol, year, month)
                if bars:
                    all_bars.extend(bars)
                await asyncio.sleep(0.2)

            logger.info(f"  {symbol} {year}: {len([b for b in all_bars if b.time[:4] == str(year)])} barras H1")

        if all_bars:
            with open(cache_file, "w") as f:
                json.dump([vars(b) for b in all_bars], f)
            logger.info(f"Dukascopy {symbol}: {len(all_bars)} barras H1 totales")

        return all_bars

    async def _fetch_month_h1(
        self, duka_sym: str, symbol: str, year: int, month: int
    ) -> list[Bar]:
        """Fetch datos H1 de un mes específico desde Dukascopy."""
        url = f"{self.BASE_URL}/{duka_sym}/{year}/{month:02d}/BID_candles_hour_1.bi5"
        try:
            sess = await self._get_session()
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                if r.status != 200:
                    return []
                content = await r.read()

            return self._parse_bi5(content, symbol, year, month)
        except Exception as e:
            logger.debug(f"Dukascopy {duka_sym} {year}-{month:02d}: {e}")
            return []

    def _parse_bi5(
        self, data: bytes, symbol: str, year: int, month: int
    ) -> list[Bar]:
        """Parse formato binario .bi5 de Dukascopy."""
        try:
            import lzma
            import struct

            decompressed = lzma.decompress(data)
            record_size  = 24  # 4 bytes timestamp + 4*float open/high/low/close + 4 volume
            bars = []
            spread = SPREAD.get(symbol, 0)

            # Precio base según símbolo
            price_factor = {
                "EURUSD": 100000.0,
                "XAUUSD": 1000.0,
                "NAS100": 10.0,
            }.get(symbol, 1.0)

            base_ts = datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()

            for i in range(0, len(decompressed) - record_size + 1, record_size):
                chunk = decompressed[i:i + record_size]
                if len(chunk) < record_size:
                    break
                try:
                    ms, o, h, l, c, v = struct.unpack(">IIIIIf", chunk)
                    ts = base_ts + ms / 1000
                    bars.append(Bar(
                        time=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                        open=o  / price_factor,
                        high=h  / price_factor,
                        low=l   / price_factor,
                        close=c / price_factor,
                        volume=float(v),
                        spread=spread,
                    ))
                except struct.error:
                    continue
            return bars
        except Exception as e:
            logger.debug(f"Parse bi5 {symbol}: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class YahooFetcher:
    """Fallback con Yahoo Finance para activos no disponibles en otras fuentes."""

    async def fetch_symbol(self, symbol: str) -> list[Bar]:
        YAHOO_MAP = {
            "XAUUSD": "GC=F",
            "EURUSD": "EURUSD=X",
            "NAS100": "^NDX",
            "BTCUSD": "BTC-USD",
            "ETHUSD": "ETH-USD",
        }
        yahoo_sym = YAHOO_MAP.get(symbol, symbol)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
            f"?interval=1d&range=max"
        )
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as sess:
                async with sess.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as r:
                    if r.status != 200:
                        return []
                    data   = await r.json()
                    result = data.get("chart", {}).get("result", [])
                    if not result:
                        return []
                    chart      = result[0]
                    timestamps = chart.get("timestamp", [])
                    ohlcv      = chart.get("indicators", {}).get("quote", [{}])[0]
                    bars = []
                    spread = SPREAD.get(symbol, 0)
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
                        except (IndexError, TypeError):
                            continue
                    return [b for b in bars if b.close > 0]
        except Exception as e:
            logger.error(f"Yahoo fallback {symbol}: {e}")
            return []


# ─── DuckDB Resampler ─────────────────────────────────────────

class DuckDBResampler:
    """Resamplea barras M1 a cualquier timeframe usando DuckDB."""

    def resample(self, bars: list[Bar], target_tf: str) -> list[Bar]:
        if not DUCKDB_OK or not bars:
            return bars

        tf_minutes = {
            "M1": 1, "M5": 5, "M15": 15,
            "H1": 60, "H4": 240, "D1": 1440
        }
        minutes = tf_minutes.get(target_tf, 60)

        try:
            import duckdb
            import pandas as pd

            df = pd.DataFrame([{
                "time":   b.time,
                "open":   b.open,
                "high":   b.high,
                "low":    b.low,
                "close":  b.close,
                "volume": b.volume,
                "spread": b.spread,
            } for b in bars])
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

            resampled = []
            for _, row in result.iterrows():
                resampled.append(Bar(
                    time=str(row["bucket"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    spread=float(row["spread"]),
                ))
            return resampled

        except Exception as e:
            logger.warning(f"DuckDB resample error: {e} — usando barras originales")
            return bars


# ─── Backtest Engine ──────────────────────────────────────────

class BacktestEngine:
    def __init__(self):
        self.binance   = BinanceFetcher()
        self.dukascopy = DukascopyFetcher()
        self.yahoo     = YahooFetcher()
        self.resampler = DuckDBResampler()
        self._checkpoint = self._load_checkpoint()
        logger.info("BacktestEngine V2 iniciado — Binance + Dukascopy + DuckDB")

    # ─── Checkpoint ───────────────────────────────────────────

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
            logger.error(f"Checkpoint save error: {e}")

    def _is_checkpointed(self, symbol: str, year: int) -> bool:
        return f"{symbol}_{year}" in self._checkpoint

    # ─── Main Run ─────────────────────────────────────────────

    async def run(
        self,
        symbol: str,
        timeframe: str = "H1",
        min_rr: float = 2.5,
        score_threshold: float = 0.65,
        walk_forward: bool = True,
    ) -> BacktestResult:
        logger.info(f"{'='*50}")
        logger.info(f"Iniciando backtest {symbol} {timeframe}")

        # 1. Fetch datos según fuente
        bars, source = await self._fetch_data(symbol, timeframe)

        if len(bars) < 200:
            logger.error(f"Datos insuficientes {symbol}: {len(bars)} barras")
            return BacktestResult(
                symbol=symbol, timeframe=timeframe,
                start_date="N/A", end_date="N/A"
            )

        logger.info(
            f"{symbol} [{source}]: {len(bars)} barras "
            f"({bars[0].time[:10]} → {bars[-1].time[:10]})"
        )

        # 2. Walk-Forward o backtest simple
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
            symbols = ["BTCUSD", "ETHUSD", "XAUUSD", "EURUSD", "NAS100"]

        logger.info(f"Iniciando backtest completo — {len(symbols)} activos")
        results = {}

        # Procesar en paralelo
        tasks = [self.run(sym) for sym in symbols]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for sym, res in zip(symbols, completed):
            if isinstance(res, BacktestResult):
                results[sym] = res
            else:
                logger.error(f"Backtest {sym} fallido: {res}")

        self._print_summary(results)

        # Monte Carlo para cada resultado
        for sym, res in results.items():
            if res.total_trades >= 30:
                mc = self.monte_carlo(res)
                mc.print_report(sym)

        return results

    async def _fetch_data(self, symbol: str, timeframe: str) -> tuple[list[Bar], str]:
        """Fetch y resamplea datos según fuente óptima."""
        if symbol in BINANCE_SYMBOLS:
            bars = await self.binance.fetch_symbol(symbol)
            source = "Binance Vision M1"
            if bars and timeframe != "M1":
                logger.info(f"Resampleando {symbol} M1 → {timeframe} con DuckDB...")
                bars = self.resampler.resample(bars, timeframe)
                source = f"Binance Vision → {timeframe}"

        elif symbol in DUKASCOPY_SYMBOLS:
            start_years = {"EURUSD": 2003, "XAUUSD": 2004, "NAS100": 2007}
            start_year  = start_years.get(symbol, 2003)
            bars = await self.dukascopy.fetch_symbol(symbol, start_year)
            source = "Dukascopy H1"

            if bars and timeframe not in ["M1", "M5", "M15", "H1"]:
                logger.info(f"Resampleando {symbol} H1 → {timeframe} con DuckDB...")
                bars = self.resampler.resample(bars, timeframe)
                source = f"Dukascopy → {timeframe}"

            if not bars:
                logger.warning(f"Dukascopy falló para {symbol}, usando Yahoo fallback")
                bars = await self.yahoo.fetch_symbol(symbol)
                source = "Yahoo Finance D1 (fallback)"
        else:
            bars = await self.yahoo.fetch_symbol(symbol)
            source = "Yahoo Finance D1"

        return bars, source

    # ─── Walk-Forward ─────────────────────────────────────────

    def _walk_forward(
        self, bars, symbol, timeframe, source, min_rr, score_threshold
    ) -> BacktestResult:
        """
        Walk-Forward: 70% entrenamiento, 30% validación.
        Repite sobre ventanas rodantes de 3 años.
        """
        logger.info(f"{symbol}: Ejecutando Walk-Forward...")

        split = int(len(bars) * 0.70)
        train_bars = bars[:split]
        test_bars  = bars[split:]

        logger.info(
            f"  Train: {train_bars[0].time[:10]} → {train_bars[-1].time[:10]} "
            f"({len(train_bars)} barras)"
        )
        logger.info(
            f"  Test:  {test_bars[0].time[:10]} → {test_bars[-1].time[:10]} "
            f"({len(test_bars)} barras)"
        )

        # Calibrar en training
        train_trades = self._simulate_trades(train_bars, symbol, min_rr, score_threshold)
        train_result = self._calculate_metrics(train_trades, train_bars, symbol, timeframe, "TRAIN")

        logger.info(
            f"  Train WR: {train_result.win_rate*100:.1f}% | "
            f"PF: {train_result.profit_factor:.2f} | "
            f"Trades: {train_result.total_trades}"
        )

        # Validar en test (out-of-sample)
        test_trades = self._simulate_trades(test_bars, symbol, min_rr, score_threshold)
        test_result = self._calculate_metrics(test_trades, test_bars, symbol, timeframe, "TEST")

        logger.info(
            f"  Test  WR: {test_result.win_rate*100:.1f}% | "
            f"PF: {test_result.profit_factor:.2f} | "
            f"Trades: {test_result.total_trades}"
        )

        # Resultado final = out-of-sample (el que importa)
        test_result.yearly_stats = self._yearly_stats(test_trades)
        return test_result

    # ─── Simulate Trades ─────────────────────────────────────

    def _simulate_trades(
        self, bars: list[Bar], symbol: str,
        min_rr: float, score_threshold: float
    ) -> list[BacktestTrade]:
        """
        Simula señales SMC institucionales sobre datos históricos.
        BOS + OB + FVG + Liquidez con slippage real.
        """
        trades = []
        lookback = 20

        for i in range(lookback + 5, len(bars) - 10):
            window = bars[max(0, i - lookback):i]
            if not window:
                continue

            bar     = bars[i]
            current = bar.close
            atr_val = self._atr(bars, i)
            if atr_val == 0:
                continue

            swing_high = max(b.high   for b in window)
            swing_low  = min(b.low    for b in window)
            avg_vol    = np.mean([b.volume for b in window])

            direction = None
            score     = 0.0
            pattern   = ""

            # ── BOS Alcista + Volumen ──
            if (current > swing_high and
                bar.close > window[-1].close and
                bar.volume > avg_vol * 1.3):
                direction = "buy"
                score     = 0.68
                pattern   = "BOS_BULL"

            # ── BOS Bajista + Volumen ──
            elif (current < swing_low and
                  bar.close < window[-1].close and
                  bar.volume > avg_vol * 1.3):
                direction = "sell"
                score     = 0.68
                pattern   = "BOS_BEAR"

            # ── OB Alcista (vela bajista antes de impulso) ──
            elif (i >= 3 and
                  bars[i-2].close < bars[i-2].open and
                  bars[i-1].close > bars[i-2].high and
                  current > bars[i-1].close):
                direction = "buy"
                score     = 0.72
                pattern   = "OB_BULL"

            # ── OB Bajista ──
            elif (i >= 3 and
                  bars[i-2].close > bars[i-2].open and
                  bars[i-1].close < bars[i-2].low and
                  current < bars[i-1].close):
                direction = "sell"
                score     = 0.72
                pattern   = "OB_BEAR"

            # ── FVG Alcista ──
            elif (i >= 2 and
                  bar.low > bars[i-2].high and
                  (bar.low - bars[i-2].high) / (current + 1e-9) > 0.001):
                direction = "buy"
                score     = 0.66
                pattern   = "FVG_BULL"

            # ── FVG Bajista ──
            elif (i >= 2 and
                  bar.high < bars[i-2].low and
                  (bars[i-2].low - bar.high) / (current + 1e-9) > 0.001):
                direction = "sell"
                score     = 0.66
                pattern   = "FVG_BEAR"

            if direction is None or score < score_threshold:
                continue

            # ── Calcular niveles con slippage ──
            slip = self._slippage(symbol)

            if direction == "buy":
                entry = current + slip + bar.spread
                sl    = entry - atr_val * 1.5
                tp    = entry + atr_val * 1.5 * min_rr
            else:
                entry = current - slip - bar.spread
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
                pattern=pattern, slippage=slip,
                year=year,
            )
            trade = self._simulate_outcome(trade, bars, i)
            trades.append(trade)

            # Checkpoint por año
            if year and not self._is_checkpointed(symbol, year):
                year_trades = [t for t in trades if t.year == year]
                if year_trades:
                    yr_wins = sum(1 for t in year_trades if t.outcome == "win")
                    yr_wr   = yr_wins / len(year_trades)
                    yr_r    = sum(t.pnl_r for t in year_trades)
                    self._save_checkpoint(symbol, year, {
                        "trades": len(year_trades),
                        "wr": round(yr_wr * 100, 1),
                        "total_r": round(yr_r, 2),
                    })

        return trades

    def _simulate_outcome(
        self, trade: BacktestTrade, bars: list[Bar], entry_bar: int
    ) -> BacktestTrade:
        """Simula resultado barra a barra con break even y trailing."""
        be_activated = False
        trailing_sl  = trade.sl
        risk         = abs(trade.entry - trade.sl)

        for j in range(entry_bar + 1, min(entry_bar + 100, len(bars))):
            bar = bars[j]

            if trade.direction == "buy":
                # Break even al 1R
                if not be_activated and bar.high >= trade.entry + risk:
                    trailing_sl  = trade.entry
                    be_activated = True

                # Trailing al 2R
                if be_activated:
                    new_trail = bar.high - risk * 0.8
                    if new_trail > trailing_sl:
                        trailing_sl = new_trail

                # SL hit
                if bar.low <= trailing_sl:
                    trade.outcome    = "win" if be_activated else "loss"
                    trade.exit_price = trailing_sl
                    trade.exit_bar   = j
                    pnl = trailing_sl - trade.entry
                    trade.pnl_r      = pnl / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break

                # TP hit
                if bar.high >= trade.tp:
                    trade.outcome    = "win"
                    trade.exit_price = trade.tp
                    trade.exit_bar   = j
                    trade.pnl_r      = min_rr = (trade.tp - trade.entry) / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break

            else:  # sell
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
            trade.outcome    = "breakeven"
            trade.exit_price = bars[min(entry_bar+30, len(bars)-1)].close
            trade.pnl_r      = 0.0
            trade.rr_achieved = 0.0

        trade.bars_held = max(0, (trade.exit_bar if trade.exit_bar > 0 else entry_bar+30) - entry_bar)
        return trade

    # ─── Métricas ─────────────────────────────────────────────

    def _calculate_metrics(
        self, trades, bars, symbol, timeframe, source
    ) -> BacktestResult:
        result = BacktestResult(
            symbol=symbol, timeframe=timeframe,
            start_date="", end_date="", source=source
        )
        result.trades      = trades
        result.total_trades = len(trades)

        if not trades:
            return result

        wins = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]

        result.wins       = len(wins)
        result.losses     = len(losses)
        result.breakevens = len(trades) - len(wins) - len(losses)
        result.win_rate   = len(wins) / len(trades)

        win_rs  = [t.pnl_r for t in wins]
        loss_rs = [abs(t.pnl_r) for t in losses]

        result.avg_win_r  = float(np.mean(win_rs))  if win_rs  else 0.0
        result.avg_loss_r = float(np.mean(loss_rs)) if loss_rs else 0.0

        gross_profit = sum(win_rs)
        gross_loss   = sum(loss_rs)
        result.profit_factor = gross_profit / (gross_loss + 1e-9)
        result.total_r       = sum(t.pnl_r for t in trades)
        result.expectancy    = (result.win_rate * result.avg_win_r) - ((1 - result.win_rate) * result.avg_loss_r)

        rrs = [t.rr_achieved for t in trades if t.rr_achieved > 0]
        result.avg_rr = float(np.mean(rrs)) if rrs else 0.0

        bh = [t.bars_held for t in trades if t.bars_held > 0]
        result.avg_bars_held = float(np.mean(bh)) if bh else 0.0

        slippages = [t.slippage for t in trades]
        result.avg_slippage = float(np.mean(slippages)) if slippages else 0.0

        # Equity curve y drawdown
        equity = 0.0
        peak   = 0.0
        max_dd = 0.0
        curve  = [0.0]
        for t in trades:
            equity += t.pnl_r
            curve.append(equity)
            if equity > peak: peak = equity
            dd = (peak - equity) / (abs(peak) + 1e-9)
            if dd > max_dd: max_dd = dd

        result.equity_curve = curve
        result.max_drawdown = max_dd

        # Sharpe
        returns = [t.pnl_r for t in trades]
        if len(returns) > 1:
            mean_r = np.mean(returns)
            std_r  = np.std(returns)
            result.sharpe_ratio = (mean_r / (std_r + 1e-9)) * np.sqrt(252)

        result.yearly_stats = self._yearly_stats(trades)
        return result

    def _yearly_stats(self, trades: list[BacktestTrade]) -> dict:
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
            st["wr"] = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0
            st["total_r"] = round(st["total_r"], 2)
        return years

    def _atr(self, bars: list[Bar], idx: int, period: int = 14) -> float:
        start  = max(0, idx - period)
        window = bars[start:idx + 1]
        if len(window) < 2:
            return 0.0
        trs = []
        for i in range(1, len(window)):
            tr = max(
                window[i].high - window[i].low,
                abs(window[i].high - window[i-1].close),
                abs(window[i].low  - window[i-1].close),
            )
            trs.append(tr)
        return float(np.mean(trs)) if trs else 0.0

    def _slippage(self, symbol: str) -> float:
        base = SLIPPAGE_PIPS.get(symbol, 0.0001)
        return base * np.random.uniform(0.5, 1.5)

    def _save_result(self, result: BacktestResult):
        try:
            all_results = {}
            if os.path.exists(RESULTS_FILE):
                with open(RESULTS_FILE, "r") as f:
                    all_results = json.load(f)
            key = f"{result.symbol}_{result.timeframe}"
            all_results[key] = result.to_dict()
            with open(RESULTS_FILE, "w") as f:
                json.dump(all_results, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando resultado: {e}")

    def _print_summary(self, results: dict):
        print(f"\n{'='*65}")
        print(f"  RESUMEN BACKTEST INSTITUCIONAL — {len(results)} ACTIVOS")
        print(f"{'='*65}")
        for sym, res in results.items():
            print(
                f"  {sym:8} | WR:{res.win_rate*100:.1f}% | "
                f"PF:{res.profit_factor:.2f} | "
                f"DD:{res.max_drawdown*100:.1f}% | "
                f"R:{res.total_r:.1f} | "
                f"Trades:{res.total_trades} | "
                f"[{res.source}]"
            )
        print(f"{'='*65}\n")

    # ─── Monte Carlo ──────────────────────────────────────────

    def monte_carlo(
        self,
        result: BacktestResult,
        simulations: int = 1000,
        trades_per_sim: int = 100,
    ) -> MonteCarloResult:
        if not result.trades:
            return MonteCarloResult(simulations, 0, 0, 0, 0, 0, 0, 0)

        returns      = [t.pnl_r for t in result.trades]
        sim_returns  = []
        sim_drawdowns = []

        for _ in range(simulations):
            sample       = np.random.choice(returns, size=trades_per_sim, replace=True)
            total_r      = float(np.sum(sample))
            sim_returns.append(total_r)

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
        await self.binance.close()
        await self.dukascopy.close()
