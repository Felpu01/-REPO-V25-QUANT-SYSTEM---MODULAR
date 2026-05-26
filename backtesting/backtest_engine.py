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
