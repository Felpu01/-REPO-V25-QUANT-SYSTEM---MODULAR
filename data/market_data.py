"""
MARKET DATA ENGINE — MetaAPI SDK
Conexion cloud a MT5/Exness desde Railway Linux
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import numpy as np

from config import (
    META_API_TOKEN, MT5_ACCOUNT_ID,
    SYMBOLS, TIMEFRAMES, BARS, SYMBOL_CONFIG, TRADE_SESSIONS
)

logger = logging.getLogger("MarketData")

TF_META = {
    "M1": "1m", "M5": "5m", "M15": "15m",
    "H1": "1h", "H4": "4h", "D1": "1d"
}

TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15,
    "H1": 60, "H4": 240, "D1": 1440
}


@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_bullish(self): return self.close > self.open
    @property
    def is_bearish(self): return self.close < self.open
    @property
    def body(self): return abs(self.close - self.open)
    @property
    def upper_wick(self): return self.high - max(self.open, self.close)
    @property
    def lower_wick(self): return min(self.open, self.close) - self.low
    @property
    def range(self): return self.high - self.low


@dataclass
class MarketData:
    symbol: str
    timeframe: str
    bars: list
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def closes(self): return np.array([b.close for b in self.bars])
    @property
    def highs(self): return np.array([b.high for b in self.bars])
    @property
    def lows(self): return np.array([b.low for b in self.bars])
    @property
    def opens(self): return np.array([b.open for b in self.bars])
    @property
    def volumes(self): return np.array([b.volume for b in self.bars])
    @property
    def current(self): return self.bars[-1]
    @property
    def prev(self): return self.bars[-2]


@dataclass
class MultiTF:
    symbol: str
    M1:  Optional[MarketData] = None
    M5:  Optional[MarketData] = None
    M15: Optional[MarketData] = None
    H1:  Optional[MarketData] = None
    H4:  Optional[MarketData] = None
    D1:  Optional[MarketData] = None
    session: str = "unknown"
    spread: float = 0.0
    current_price: float = 0.0

    def get(self, tf: str) -> Optional[MarketData]:
        return getattr(self, tf, None)

    def is_complete(self) -> bool:
        return all(self.get(tf) is not None for tf in ["H4", "H1", "M15"])


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    out = np.zeros_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out

def atr(bars: list, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i-1].close),
            abs(bars[i].low  - bars[i-1].close),
        )
        trs.append(tr)
    return float(np.mean(trs[-period:]))

def rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = np.diff(closes)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.mean(gain[-period:])
    avg_l = np.mean(loss[-period:])
    if avg_l == 0:
        return 100.0
    return float(100 - (100 / (1 + avg_g / avg_l)))

def detect_session() -> str:
    hour = datetime.now(timezone.utc).hour
    if 13 <= hour < 16: return "ln_ny_overlap"
    if 7  <= hour < 16: return "london"
    if 13 <= hour < 22: return "new_york"
    if 0  <= hour <  9: return "tokyo"
    return "sydney"


class MarketDataEngine:
    def __init__(self):
        self._cache: dict = {}
        self._token      = os.getenv("META_API_TOKEN", "") or META_API_TOKEN
        self._account_id = os.getenv("MT5_ACCOUNT_ID", "") or MT5_ACCOUNT_ID
        self._api        = None
        self._account    = None
        self._connection = None
        self._sdk_ready  = False
        logger.info(f"MarketDataEngine iniciado | Token: {'OK' if self._token else 'MISSING'}")

    def set_connection(self, connection):
        """Recibe la conexión compartida desde main.py"""
        self._connection = connection
        self._sdk_ready  = True
        logger.info("✅ MarketData: conexión MetaAPI recibida")

    async def _init_sdk(self):
        """Inicializar SDK de MetaAPI si no está listo."""
        if self._sdk_ready or not self._token or not self._account_id:
            return
        try:
            from metaapi_cloud_sdk import MetaApi
            self._api     = MetaApi(self._token)
            self._account = await self._api.metatrader_account_api.get_account(self._account_id)
            if self._account.state not in ['DEPLOYING', 'DEPLOYED']:
                await self._account.deploy()
            await self._account.wait_connected()
            self._connection = self._account.get_rpc_connection()
            await self._connection.connect()
            await self._connection.wait_synchronized()
            self._sdk_ready = True
            logger.info("✅ MarketData SDK listo")
        except Exception as e:
            logger.error(f"MarketData SDK init error: {e}")
            self._sdk_ready = False

    async def fetch_bars(self, symbol: str, timeframe: str, count: int) -> list:
        """Fetch barras via SDK o simulación."""
        if not self._token or not self._account_id:
            return self._sim_bars(symbol, count)

        # Intentar SDK primero
        if self._sdk_ready and self._connection:
            try:
                from datetime import timedelta
                end_time   = datetime.now(timezone.utc)
                minutes    = TF_MINUTES.get(timeframe, 60)
                start_time = end_time - timedelta(minutes=minutes * count * 2)

                candles = await self._connection.get_historical_candles(
                    symbol, timeframe, start_time, end_time, count
                )
                if candles:
                    bars = []
                    for c in candles[-count:]:
                        bars.append(Bar(
                            time=c.get("time", datetime.now(timezone.utc)),
                            open=float(c.get("open", 0)),
                            high=float(c.get("high", 0)),
                            low=float(c.get("low", 0)),
                            close=float(c.get("close", 0)),
                            volume=float(c.get("tickVolume", 1)),
                        ))
                    return bars if bars else self._sim_bars(symbol, count)
            except Exception as e:
                logger.debug(f"SDK fetch_bars {symbol}/{timeframe}: {e}")

        # Fallback simulación
        return self._sim_bars(symbol, count)

    async def get_price(self, symbol: str) -> float:
        """Obtener precio actual via SDK."""
        if not self._sdk_ready or not self._connection:
            return 0.0
        try:
            price = await self._connection.get_symbol_price(symbol)
            if price:
                return float(price.get("bid", price.get("ask", 0)))
        except Exception as e:
            logger.error(f"get_price {symbol}: {e}")
        return 0.0

    async def get_multi_tf(self, symbol: str) -> MultiTF:
        """Obtener datos multi-timeframe para un símbolo."""
        # Inicializar SDK si es necesario
        if not self._sdk_ready:
            await self._init_sdk()

        mtf = MultiTF(symbol=symbol, session=detect_session())
        tasks = {tf: self.fetch_bars(symbol, tf, BARS[tf]) for tf in TIMEFRAMES}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for tf, result in zip(tasks.keys(), results):
            if isinstance(result, list) and result:
                setattr(mtf, tf, MarketData(symbol=symbol, timeframe=tf, bars=result))

        mtf.current_price = await self.get_price(symbol)
        if mtf.current_price == 0 and mtf.M1:
            mtf.current_price = mtf.M1.current.close

        self._cache[symbol] = mtf
        return mtf

    async def refresh_all(self) -> dict:
        """Refresh datos de todos los símbolos."""
        tasks = [self.get_multi_tf(sym) for sym in SYMBOLS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for sym, res in zip(SYMBOLS, results):
            if isinstance(res, MultiTF):
                out[sym] = res
            else:
                logger.error(f"refresh_all {sym}: {res}")
        return out

    def get_cached(self, symbol: str):
        return self._cache.get(symbol)

    def _sim_bars(self, symbol: str, count: int) -> list:
        BASE = {
            "BTCUSD": 67000.0, "ETHUSD": 3500.0,
            "XAUUSD": 2350.0,  "EURUSD": 1.0850,
            "NAS100": 19500.0,
        }
        base  = BASE.get(symbol, 1.0)
        vol   = base * 0.0012
        bars  = []
        price = base
        for _ in range(count):
            d  = np.random.choice([-1, 1], p=[0.48, 0.52])
            mv = abs(np.random.normal(0, vol))
            o  = price
            c  = o + d * mv
            h  = max(o, c) + abs(np.random.normal(0, vol * 0.4))
            l  = min(o, c) - abs(np.random.normal(0, vol * 0.4))
            bars.append(Bar(
                time=datetime.now(timezone.utc),
                open=round(o, 5), high=round(h, 5),
                low=round(l, 5),  close=round(c, 5),
                volume=abs(np.random.normal(1000, 300)),
            ))
            price = c
        return bars

    async def close(self):
        try:
            if self._connection:
                await self._connection.close()
        except Exception:
            pass
