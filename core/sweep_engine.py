"""
SWEEP ENGINE INSTITUCIONAL
Detección real de Liquidity Sweeps, Stop Hunts,
BSL/SSL, Equal Highs/Lows y rechazo confirmado.
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from data.market_data import MarketData, atr

logger = logging.getLogger("SweepEngine")


@dataclass
class LiquidityPool:
    price: float
    type: str
    strength: float
    touch_count: int
    bar_index: int
    swept: bool = False
    sweep_bar: int = -1
    rejection_confirmed: bool = False
    sweep_size: float = 0.0
    rejection_size: float = 0.0

    @property
    def is_valid_sweep(self):
        return self.swept and self.rejection_confirmed


@dataclass
class SweepEvent:
    symbol: str
    timeframe: str
    type: str
    price_swept: float
    sweep_high: float
    sweep_low: float
    rejection_close: float
    direction: str
    strength: float
    bar_index: int
    atr_ratio: float
    institutional_signature: bool

    def to_dict(self):
        return {
            "type": self.type,
            "price": self.price_swept,
            "direction": self.direction,
            "strength": round(self.strength, 1),
            "atr_ratio": round(self.atr_ratio, 2),
            "institutional": self.institutional_signature,
        }


@dataclass
class SweepAnalysis:
    symbol: str
    timeframe: str
    liquidity_pools: list = field(default_factory=list)
    sweep_events: list = field(default_factory=list)
    recent_sweep: Optional[SweepEvent] = None
    bias_from_sweep: str = "neutral"
    sweep_score: float = 0.0
    manipulation_detected: bool = False

    def to_dict(self):
        return {
            "pools": len(self.liquidity_pools),
            "sweeps": len(self.sweep_events),
            "recent": self.recent_sweep.to_dict() if self.recent_sweep else None,
            "bias": self.bias_from_sweep,
            "score": round(self.sweep_score, 1),
            "manipulation": self.manipulation_detected,
        }


class SweepEngine:
    def __init__(self):
        self.eq_tolerance = 0.0003
        self.pool_lookback = 100
        logger.info("SweepEngine institucional iniciado")

    def analyze(self, data: MarketData) -> SweepAnalysis:
        bars = data.bars
        result = SweepAnalysis(symbol=data.symbol, timeframe=data.timeframe)
        if len(bars) < 50:
            return result
        atr_val = atr(bars, 14)
        if atr_val == 0:
            return result
        result.liquidity_pools = self._detect_liquidity_pools(bars)
        result.sweep_events = self._detect_sweeps(bars, result.liquidity_pools, atr_val)
        if result.sweep_events:
            result.recent_sweep = result.sweep_events[-1]
            result.bias_from_sweep = result.recent_sweep.direction
        result.manipulation_detected = self._detect_manipulation(bars, result.sweep_events, atr_val)
        result.sweep_score = self._calculate_score(result)
        return result

    def _detect_liquidity_pools(self, bars):
        pools = []
        recent = bars[-self.pool_lookback:]
        n = len(recent)
        highs = np.array([b.high for b in recent])
        lows  = np.array([b.low  for b in recent])

        for i in range(n - 1):
            for j in range(i + 3, n):
                if abs(highs[i] - highs[j]) / (highs[i] + 1e-9) <= self.eq_tolerance:
                    touches = sum(1 for k in range(i+1,j) if abs(highs[k]-highs[i])/(highs[i]+1e-9) <= self.eq_tolerance*2)
                    pools.append(LiquidityPool(price=(highs[i]+highs[j])/2, type="EQH", strength=min(100,50+touches*15+(j-i)*0.5), touch_count=touches+2, bar_index=len(bars)-n+j))
                    break

        for i in range(n - 1):
            for j in range(i + 3, n):
                if abs(lows[i] - lows[j]) / (lows[i] + 1e-9) <= self.eq_tolerance:
                    touches = sum(1 for k in range(i+1,j) if abs(lows[k]-lows[i])/(lows[i]+1e-9) <= self.eq_tolerance*2)
                    pools.append(LiquidityPool(price=(lows[i]+lows[j])/2, type="EQL", strength=min(100,50+touches*15+(j-i)*0.5), touch_count=touches+2, bar_index=len(bars)-n+j))
                    break

        for i in range(5, n - 5):
            if highs[i] > max(highs[i-5:i]) and highs[i] > max(highs[i+1:i+6]):
                pools.append(LiquidityPool(price=highs[i], type="BSL", strength=60.0, touch_count=1, bar_index=len(bars)-n+i))

        for i in range(5, n - 5):
            if lows[i] < min(lows[i-5:i]) and lows[i] < min(lows[i+1:i+6]):
                pools.append(LiquidityPool(price=lows[i], type="SSL", strength=60.0, touch_count=1, bar_index=len(bars)-n+i))

        return sorted(pools, key=lambda x: x.strength, reverse=True)[:20]

    def _detect_sweeps(self, bars, pools, atr_val):
        sweeps = []
        for pool in pools:
            if pool.bar_index >= len(bars) - 1:
                continue
            subsequent = bars[pool.bar_index + 1:]
            for i, bar in enumerate(subsequent):
                if pool.type in ["BSL", "EQH"] and bar.high > pool.price and bar.close < pool.price:
                    sweep_size = bar.high - pool.price
                    atr_ratio = sweep_size / (atr_val + 1e-9)
                    rejection_size = pool.price - bar.close
                    institutional = atr_ratio >= 0.3 and (rejection_size / (sweep_size + 1e-9)) >= 0.5
                    strength = min(100, pool.strength * 0.4 + atr_ratio * 30 + (rejection_size/(sweep_size+1e-9)) * 30)
                    pool.swept = True
                    pool.rejection_confirmed = True
                    pool.sweep_size = sweep_size
                    pool.rejection_size = rejection_size
                    sweeps.append(SweepEvent(
                        symbol="", timeframe="",
                        type=f"{pool.type}_SWEEP",
                        price_swept=pool.price,
                        sweep_high=bar.high, sweep_low=bar.low,
                        rejection_close=bar.close,
                        direction="bearish",
                        strength=round(strength, 1),
                        bar_index=pool.bar_index + 1 + i,
                        atr_ratio=round(atr_ratio, 3),
                        institutional_signature=institutional,
                    ))
                    break

                elif pool.type in ["SSL", "EQL"] and bar.low < pool.price and bar.close > pool.price:
                    sweep_size = pool.price - bar.low
                    atr_ratio = sweep_size / (atr_val + 1e-9)
                    rejection_size = bar.close - pool.price
                    institutional = atr_ratio >= 0.3 and (rejection_size / (sweep_size + 1e-9)) >= 0.5
                    strength = min(100, pool.strength * 0.4 + atr_ratio * 30 + (rejection_size/(sweep_size+1e-9)) * 30)
                    pool.swept = True
                    pool.rejection_confirmed = True
                    pool.sweep_size = sweep_size
                    pool.rejection_size = rejection_size
                    sweeps.append(SweepEvent(
                        symbol="", timeframe="",
                        type=f"{pool.type}_SWEEP",
                        price_swept=pool.price,
                        sweep_high=bar.high, sweep_low=bar.low,
                        rejection_close=bar.close,
                        direction="bullish",
                        strength=round(strength, 1),
                        bar_index=pool.bar_index + 1 + i,
                        atr_ratio=round(atr_ratio, 3),
                        institutional_signature=institutional,
                    ))
                    break

        sweeps.sort(key=lambda x: x.bar_index)
        return [s for s in sweeps if s.bar_index >= len(bars) - 50]

    def _detect_manipulation(self, bars, sweeps, atr_val):
        if not sweeps:
            return False
        recent = sweeps[-1]
        idx = recent.bar_index
        if idx + 3 >= len(bars):
            return False
        post = bars[idx:idx+5]
        if len(post) < 3:
            return False
        post_move = abs(post[-1].close - post[0].open)
        sweep_size = abs(recent.sweep_high - recent.sweep_low)
        if post_move > sweep_size * 1.5:
            return True
        if len(post) >= 2 and post[1].body > post[0].body * 1.5 and post[1].body > atr_val * 0.5:
            return True
        return False

    def _calculate_score(self, result):
        score = 0.0
        score += min(20, len(result.liquidity_pools) * 2)
        if result.sweep_events:
            score += min(30, len(result.sweep_events) * 10)
        if result.recent_sweep:
            score += 30 if result.recent_sweep.institutional_signature else 15
        if result.manipulation_detected:
            score += 20
        return min(100.0, score)


@dataclass
class InstitutionalFVG:
    index: int
    type: str
    high: float
    low: float
    size: float
    size_pct: float
    atr_ratio: float
    filled: bool = False
    fill_pct: float = 0.0
    mitigated: bool = False
    mitigation_bar: int = -1
    institutional_grade: str = "C"
    volume_at_creation: float = 0.0

    @property
    def midpoint(self):
        return (self.high + self.low) / 2

    @property
    def is_valid(self):
        return not self.filled and not self.mitigated

    @property
    def is_premium(self):
        return self.institutional_grade in ["A+", "A"]


class FVGEngine:
    def __init__(self):
        self.min_size_pct = 0.0005
        self.min_atr_ratio = 0.15
        logger.info("FVGEngine institucional iniciado")

    def analyze(self, data: MarketData) -> list:
        bars = data.bars
        if len(bars) < 10:
            return []
        atr_val = atr(bars, 14)
        avg_vol = np.mean([b.volume for b in bars[-30:]])
        fvgs = []

        for i in range(1, len(bars) - 1):
            prev = bars[i-1]
            curr = bars[i]
            nxt  = bars[i+1]
            ref  = curr.close + 1e-9

            if nxt.low > prev.high:
                size = nxt.low - prev.high
                if size/ref >= self.min_size_pct and size/(atr_val+1e-9) >= self.min_atr_ratio:
                    fvg = InstitutionalFVG(index=i, type="bullish", high=nxt.low, low=prev.high, size=size, size_pct=round(size/ref*100,4), atr_ratio=round(size/(atr_val+1e-9),3), volume_at_creation=curr.volume)
                    fvg.institutional_grade = self._grade(fvg, curr, avg_vol, atr_val)
                    fvg = self._check_mitigation(fvg, bars[i+1:], i+1)
                    fvgs.append(fvg)

            elif nxt.high < prev.low:
                size = prev.low - nxt.high
                if size/ref >= self.min_size_pct and size/(atr_val+1e-9) >= self.min_atr_ratio:
                    fvg = InstitutionalFVG(index=i, type="bearish", high=prev.low, low=nxt.high, size=size, size_pct=round(size/ref*100,4), atr_ratio=round(size/(atr_val+1e-9),3), volume_at_creation=curr.volume)
                    fvg.institutional_grade = self._grade(fvg, curr, avg_vol, atr_val)
                    fvg = self._check_mitigation(fvg, bars[i+1:], i+1)
                    fvgs.append(fvg)

        valid = [f for f in fvgs if f.is_valid]
        grade_order = {"A+":0,"A":1,"B":2,"C":3}
        return sorted(valid, key=lambda x: grade_order.get(x.institutional_grade,3))[:10]

    def _grade(self, fvg, bar, avg_vol, atr_val):
        pts = 0
        if fvg.atr_ratio >= 0.5: pts += 3
        elif fvg.atr_ratio >= 0.3: pts += 2
        elif fvg.atr_ratio >= 0.15: pts += 1
        vol_r = bar.volume / (avg_vol + 1e-9)
        if vol_r >= 2.0: pts += 3
        elif vol_r >= 1.5: pts += 2
        elif vol_r >= 1.0: pts += 1
        if bar.body > 0:
            wr = (bar.upper_wick + bar.lower_wick) / (bar.body + 1e-9)
            if wr < 0.3: pts += 2
            elif wr < 0.7: pts += 1
        if pts >= 7: return "A+"
        elif pts >= 5: return "A"
        elif pts >= 3: return "B"
        return "C"

    def _check_mitigation(self, fvg, subsequent, start_idx):
        for i, bar in enumerate(subsequent):
            if fvg.type == "bullish":
                if bar.low <= fvg.high and bar.low >= fvg.low:
                    fvg.fill_pct = min(100, (fvg.high - bar.low) / fvg.size * 100)
                if bar.close < fvg.low:
                    fvg.filled = True
                    fvg.mitigation_bar = start_idx + i
                    break
                if fvg.fill_pct >= 50 and not fvg.mitigated:
                    fvg.mitigated = True
                    fvg.mitigation_bar = start_idx + i
            elif fvg.type == "bearish":
                if bar.high >= fvg.low and bar.high <= fvg.high:
                    fvg.fill_pct = min(100, (bar.high - fvg.low) / fvg.size * 100)
                if bar.close > fvg.high:
                    fvg.filled = True
                    fvg.mitigation_bar = start_idx + i
                    break
                if fvg.fill_pct >= 50 and not fvg.mitigated:
                    fvg.mitigated = True
                    fvg.mitigation_bar = start_idx + i
        return fvg


class MitigationEngine:
    def __init__(self):
        logger.info("MitigationEngine iniciado")

    def detect_breaker_blocks(self, bars, order_blocks):
        breakers = []
        current = bars[-1].close
        for ob in order_blocks:
            if not ob.mitigated:
                continue
            if ob.type == "bullish" and current <= ob.high and current >= ob.low:
                breakers.append({"type":"BB_BEARISH","price":ob.mid,"direction":"bearish","strength":ob.strength*0.8})
            elif ob.type == "bearish" and current <= ob.high and current >= ob.low:
                breakers.append({"type":"BB_BULLISH","price":ob.mid,"direction":"bullish","strength":ob.strength*0.8})
        return breakers
