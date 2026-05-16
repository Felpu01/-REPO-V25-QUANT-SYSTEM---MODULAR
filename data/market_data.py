"""
MARKET DATA ENGINE
Streaming multi-asset, multi-timeframe OHLCV data.
Supports MT5 live data, WebSocket feeds, and macro news.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import numpy as np

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

logger = logging.getLogger("MarketDataEngine")

# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class OHLCV:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass
class MarketData:
    symbol: str
    timeframe: str
    bars: list[OHLCV]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def closes(self) -> np.ndarray:
        return np.array([b.close for b in self.bars])

    @property
    def highs(self) -> np.ndarray:
        return np.array([b.high for b in self.bars])

    @property
    def lows(self) -> np.ndarray:
        return np.array([b.low for b in self.bars])

    @property
    def volumes(self) -> np.ndarray:
        return np.array([b.volume for b in self.bars])

    @property
    def current_bar(self) -> OHLCV:
        return self.bars[-1]

    @property
    def prev_bar(self) -> OHLCV:
        return self.bars[-2]


@dataclass
class MultiTimeframeData:
    symbol: str
    D1: Optional[MarketData] = None
    H4: Optional[MarketData] = None
    H1: Optional[MarketData] = None
    M15: Optional[MarketData] = None
    M5: Optional[MarketData] = None
    M1: Optional[MarketData] = None
    session: str = "unknown"
    spread: float = 0.0

    def get(self, tf: str) -> Optional[MarketData]:
        return getattr(self, tf, None)


# ─── Market Sessions ──────────────────────────────────────────────────────────

SESSIONS = {
    "sydney":    (21, 6),    # UTC
    "tokyo":     (0, 9),
    "london":    (7, 16),
    "new_york":  (13, 22),
    "overlap_ln_ny": (13, 16),
}

def detect_session() -> str:
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour < 16:
        return "london_ny_overlap"
    elif 7 <= hour < 16:
        return "london"
    elif 13 <= hour < 22:
        return "new_york"
    elif 0 <= hour < 9:
        return "tokyo"
    elif 21 <= hour or hour < 6:
        return "sydney"
    return "off_hours"


# ─── MT5 Timeframe Mapping ────────────────────────────────────────────────────

TF_MAP = {
    "M1":  1,
    "M5":  5,
    "M15": 15,
    "H1":  16385,
    "H4":  16388,
    "D1":  16408,
} if not MT5_AVAILABLE else {
    "M1":  mt5.TIMEFRAME_M1  if MT5_AVAILABLE else 1,
    "M5":  mt5.TIMEFRAME_M5  if MT5_AVAILABLE else 5,
    "M15": mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 15,
    "H1":  mt5.TIMEFRAME_H1  if MT5_AVAILABLE else 16385,
    "H4":  mt5.TIMEFRAME_H4  if MT5_AVAILABLE else 16388,
    "D1":  mt5.TIMEFRAME_D1  if MT5_AVAILABLE else 16408,
}

BARS_PER_TF = {
    "M1": 200, "M5": 200, "M15": 200,
    "H1": 300, "H4": 300, "D1": 500,
}


# ─── Market Data Engine ───────────────────────────────────────────────────────

class MarketDataEngine:
    def __init__(self):
        self._cache: dict[str, MultiTimeframeData] = {}
        self._running = False
        logger.info("MarketDataEngine initialized")

    async def stream_loop(self):
        """Continuous data update loop."""
        self._running = True
        SYMBOLS = ["BTCUSD", "ETHUSD", "XAUUSD", "EURUSD", "NAS100"]
        TIMEFRAMES = ["D1", "H4", "H1", "M15", "M5", "M1"]

        while self._running:
            for symbol in SYMBOLS:
                try:
                    data = await self.get_multi_timeframe(symbol, TIMEFRAMES)
                    if data:
                        self._cache[symbol] = data
                except Exception as e:
                    logger.error(f"Stream error {symbol}: {e}")
            await asyncio.sleep(10)

    async def get_multi_timeframe(
        self, symbol: str, timeframes: list[str]
    ) -> Optional[MultiTimeframeData]:
        """Fetch all timeframes for a symbol."""
        mtf = MultiTimeframeData(symbol=symbol)
        mtf.session = detect_session()

        for tf in timeframes:
            bars = await self._fetch_bars(symbol, tf, BARS_PER_TF.get(tf, 200))
            if bars:
                setattr(mtf, tf, MarketData(symbol=symbol, timeframe=tf, bars=bars))

        if mtf.H1:
            mtf.spread = await self._get_spread(symbol)

        return mtf

    async def _fetch_bars(
        self, symbol: str, timeframe: str, count: int
    ) -> Optional[list[OHLCV]]:
        """Fetch OHLCV bars from MT5 or generate simulation data."""
        if MT5_AVAILABLE and mt5.terminal_info() is not None:
            return await self._fetch_mt5_bars(symbol, timeframe, count)
        else:
            return self._generate_sim_bars(symbol, timeframe, count)

    async def _fetch_mt5_bars(
        self, symbol: str, timeframe: str, count: int
    ) -> Optional[list[OHLCV]]:
        """Fetch real bars from MetaTrader 5."""
        try:
            tf_code = TF_MAP[timeframe]
            rates = mt5.copy_rates_from_pos(symbol, tf_code, 0, count)
            if rates is None:
                return None
            return [
                OHLCV(
                    time=datetime.fromtimestamp(r["time"], tz=timezone.utc),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["tick_volume"]),
                    spread=float(r.get("spread", 0)),
                )
                for r in rates
            ]
        except Exception as e:
            logger.error(f"MT5 bars error {symbol}/{timeframe}: {e}")
            return None

    def _generate_sim_bars(
        self, symbol: str, timeframe: str, count: int
    ) -> list[OHLCV]:
        """Generate realistic simulation bars for dev/testing."""
        BASE_PRICES = {
            "BTCUSD": 67000.0,
            "ETHUSD": 3500.0,
            "XAUUSD": 2350.0,
            "EURUSD": 1.085,
            "NAS100": 19500.0,
        }
        base = BASE_PRICES.get(symbol, 1.0)
        volatility = base * 0.001

        bars = []
        price = base
        for i in range(count):
            open_p = price
            direction = np.random.choice([-1, 1], p=[0.48, 0.52])
            move = abs(np.random.normal(0, volatility))
            close_p = open_p + direction * move
            high_p = max(open_p, close_p) + abs(np.random.normal(0, volatility * 0.5))
            low_p = min(open_p, close_p) - abs(np.random.normal(0, volatility * 0.5))
            vol = abs(np.random.normal(1000, 300))

            bars.append(OHLCV(
                time=datetime.now(timezone.utc),
                open=round(open_p, 5),
                high=round(high_p, 5),
                low=round(low_p, 5),
                close=round(close_p, 5),
                volume=round(vol, 2),
            ))
            price = close_p

        return bars

    async def _get_spread(self, symbol: str) -> float:
        if MT5_AVAILABLE and mt5.terminal_info() is not None:
            info = mt5.symbol_info(symbol)
            return info.spread if info else 0.0
        return np.random.uniform(1, 5)

    def get_cached(self, symbol: str) -> Optional[MultiTimeframeData]:
        return self._cache.get(symbol)

    # ─── Technical Indicators ─────────────────────────────────────────────────

    @staticmethod
    def ema(data: np.ndarray, period: int) -> np.ndarray:
        alpha = 2 / (period + 1)
        result = np.zeros(len(data))
        result[0] = data[0]
        for i in range(1, len(data)):
            result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
        return result

    @staticmethod
    def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
        delta = np.diff(closes)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = np.convolve(gain, np.ones(period) / period, mode='valid')
        avg_loss = np.convolve(loss, np.ones(period) / period, mode='valid')
        rs = np.where(avg_loss != 0, avg_gain / avg_loss, 100)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def atr(bars: list[OHLCV], period: int = 14) -> float:
        if len(bars) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
            trs.append(tr)
        return float(np.mean(trs[-period:]))

    @staticmethod
    def normalize_data(data: np.ndarray) -> np.ndarray:
        """Min-max normalization to [0, 1]."""
        rng = data.max() - data.min()
        return (data - data.min()) / rng if rng > 0 else np.zeros_like(data)

    @staticmethod
    def aggregate_timeframes(bars: list[OHLCV], factor: int) -> list[OHLCV]:
        """Combine N bars into higher timeframe bars."""
        result = []
        for i in range(0, len(bars) - factor + 1, factor):
            chunk = bars[i:i + factor]
            result.append(OHLCV(
                time=chunk[0].time,
                open=chunk[0].open,
                high=max(b.high for b in chunk),
                low=min(b.low)
