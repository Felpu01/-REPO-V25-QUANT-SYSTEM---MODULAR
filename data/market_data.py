"""
MARKET DATA ENGINE — MetaAPI
Conexion cloud a MT5/Exness desde Railway Linux
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import aiohttp

from config import (
    META_API_TOKEN, MT5_ACCOUNT_ID,
    SYMBOLS, TIMEFRAMES, BARS, SYMBOL_CONFIG, TRADE_SESSIONS
)

logger = logging.getLogger("MarketData")

TF_META = {
    "M1": "1m", "M5": "5m", "M15": "15m",
    "H1": "1h", "H4": "4h", "D1": "1d"
}

BASE_URL = "https://mt-client-api-v1.london.agiliumtrade.ai"


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
        self._session: Optional[aiohttp.ClientSession] = None
        # Lee desde env primero, luego config
        self._token      = os.getenv("META_API_TOKEN", "") or META_API_TOKEN
        self._account_id = os.getenv("MT5_ACCOUNT_ID", "") or MT5_ACCOUNT_ID
        self._headers = {
            "auth-token": self._token,
            "Content-Type": "application/json",
        }
        has_token = bool(self._token)
        logger.info(f"MarketDataEngine iniciado | Token: {'OK' if has_token else 'MISSING'}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # SSL desactivado para compatibilidad con MetaAPI en Railway
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                connector=connector
            )
        return self._session

    async def fetch_bars(self, symbol: str, timeframe: str, count: int):
        if not self._token or not self._account_id:
            return self._sim_bars(symbol, count)
        try:
            sess = await self._get_session()
            tf = TF_META[timeframe]
            url = (
                f"{BASE_URL}/users/current/accounts/{self._account_id}"
                f"/historical-market-data/symbols/{symbol}"
                f"/timeframes/{tf}/candles?limit={count}"
            )
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return self._sim_bars(symbol, count)
                data = await r.json()
                candles = data.get("candles", data) if isinstance(data, dict) else data
                bars = []
                for c in candles:
                    bars.append(Bar(
                        time=datetime.fromisoformat(
                            c.get("time", "2024-01-01T00:00:00").replace("Z", "+00:00")
                        ),
                        open=float(c.get("open", 0)),
                        high=float(c.get("high", 0)),
                        low=float(c.get("low", 0)),
                        close=float(c.get("close", 0)),
                        volume=float(c.get("tickVolume", c.get("volume", 1))),
                    ))
                return bars if bars else self._sim_bars(symbol, count)
        except Exception as e:
            logger.error(f"fetch_bars {symbol}/{timeframe}: {e}")
            return self._sim_bars(symbol, count)

    async def get_price(self, symbol: str) -> float:
        if not self._token or not self._account_id:
            return 0.0
        try:
            sess = await self._get_session()
            url = (
                f"{BASE_URL}/users/current/accounts/{self._account_id}"
                f"/symbols/{symbol}/current-price"
            )
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json()
                    return float(data.get("bid", data.get("ask", 0)))
        except Exception as e:
            logger.error(f"get_price {symbol}: {e}")
        return 0.0

    async def get_multi_tf(self, symbol: str) -> MultiTF:
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
            "XAUUSD": 2350.0,  "EURUSD": 1.0850, "NAS100": 19500.0,
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
        if self._session and not self._session.closed:
            await self._session.close()
