"""
SMC ENGINE INSTITUCIONAL
BOS, CHOCH, Order Blocks, FVG, Liquidity — multi-timeframe
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from data.market_data import MarketData, Bar, MultiTF, ema, atr
from config import SWING_LOOKBACK, BOS_LOOKBACK, FVG_MIN_PCT, OB_LOOKBACK, LIQ_TOLERANCE

logger = logging.getLogger("SMC")


# ─── Estructuras ─────────────────────────────────────────────

@dataclass
class SwingPoint:
    index: int
    price: float
    type: str       # HH | HL | LH | LL

@dataclass
class OrderBlock:
    index: int
    type: str       # bullish | bearish
    high: float
    low: float
    volume: float
    strength: float
    mitigated: bool = False

    @property
    def mid(self): return (self.high + self.low) / 2
    @property
    def size(self): return self.high - self.low

@dataclass
class FVG:
    index: int
    type: str       # bullish | bearish
    high: float
    low: float
    size: float
    filled: bool = False
    fill_pct: float = 0.0

@dataclass
class LiquidityLevel:
    price: float
    type: str       # equal_highs | equal_lows | bsl | ssl
    strength: float
    swept: bool = False

@dataclass
class SMCAnalysis:
    symbol: str
    timeframe: str
    bias: str               # bullish | bearish | neutral
    bos: bool = False
    choch: bool = False
    bos_price: float = 0.0
    choch_price: float = 0.0
    order_blocks: list[OrderBlock] = field(default_factory=list)
    fvgs: list[FVG] = field(default_factory=list)
    liquidity: list[LiquidityLevel] = field(default_factory=list)
    swings: list[SwingPoint] = field(default_factory=list)
    premium_discount: str = "equilibrium"  # premium | discount | equilibrium
    eq_level: float = 0.0
    atr_value: float = 0.0
    score: float = 0.0      # 0-100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "tf": self.timeframe,
            "bias": self.bias,
            "bos": self.bos,
            "choch": self.choch,
            "ob_count": len([o for o in self.order_blocks if not o.mitigated]),
            "fvg_count": len([f for f in self.fvgs if not f.filled]),
            "liq_count": len(self.liquidity),
            "pd_zone": self.premium_discount,
            "score": round(self.score, 1),
        }


# ─── SMC Engine ──────────────────────────────────────────────

class SMCEngine:
    def __init__(self):
        logger.info("SMCEngine institucional iniciado")

    def analyze(self, data: MarketData) -> SMCAnalysis:
        bars = data.bars
        result = SMCAnalysis(symbol=data.symbol, timeframe=data.timeframe)

        if len(bars) < 60:
            return result

        result.atr_value = atr(bars, 14)

        # 1. Swing points
        result.swings = self._detect_swings(bars)

        # 2. Bias desde estructura
        result.bias = self._structure_bias(result.swings)

        # 3. BOS con confirmación de cierre de vela
        result.bos, result.bos_price = self._detect_bos(bars, result.swings, result.bias)

        # 4. CHOCH — cambio de carácter
        result.choch, result.choch_price = self._detect_choch(bars, result.swings, result.bias)

        # 5. Order Blocks institucionales
        result.order_blocks = self._detect_ob(bars, result.bias)

        # 6. Fair Value Gaps
        result.fvgs = self._detect_fvg(bars)

        # 7. Liquidez (equal highs/lows)
        result.liquidity = self._detect_liquidity(bars)

        # 8. Premium / Discount
        result.premium_discount, result.eq_level = self._premium_discount(bars)

        # 9. Score
        result.score = self._score(result)

        return result

    # ─── Swing Points ─────────────────────────────────────────

    def _detect_swings(self, bars: list[Bar]) -> list[SwingPoint]:
        n = SWING_LOOKBACK
        points: list[SwingPoint] = []

        for i in range(n, len(bars) - n):
            b = bars[i]
            left_h  = [bars[j].high for j in range(i - n, i)]
            right_h = [bars[j].high for j in range(i + 1, i + n + 1)]
            left_l  = [bars[j].low  for j in range(i - n, i)]
            right_l = [bars[j].low  for j in range(i + 1, i + n + 1)]

            if b.high >= max(left_h) and b.high >= max(right_h):
                prev_sh = next((p for p in reversed(points) if p.type in ["HH", "LH"]), None)
                t = "HH" if prev_sh and b.high > prev_sh.price else "LH"
                points.append(SwingPoint(i, b.high, t))

            elif b.low <= min(left_l) and b.low <= min(right_l):
                prev_sl = next((p for p in reversed(points) if p.type in ["HL", "LL"]), None)
                t = "HL" if prev_sl and b.low > prev_sl.price else "LL"
                points.append(SwingPoint(i, b.low, t))

        return points[-30:]

    def _structure_bias(self, swings: list[SwingPoint]) -> str:
        if len(swings) < 4:
            return "neutral"
        last = swings[-6:]
        bull = sum(1 for s in last if s.type in ["HH", "HL"])
        bear = sum(1 for s in last if s.type in ["LH", "LL"])
        if bull >= 4: return "bullish"
        if bear >= 4: return "bearish"
        if bull > bear: return "bullish"
        if bear > bull: return "bearish"
        return "neutral"

    # ─── BOS ──────────────────────────────────────────────────

    def _detect_bos(
        self, bars: list[Bar], swings: list[SwingPoint], bias: str
    ) -> tuple[bool, float]:
        """BOS real: close de vela supera el swing relevante."""
        if not swings or len(bars) < BOS_LOOKBACK:
            return False, 0.0

        recent_bars = bars[-BOS_LOOKBACK:]

        if bias == "bullish":
            # Busca swing high previo y verifica close encima
            sh = next((s for s in reversed(swings) if s.type in ["LH", "HH"]), None)
            if sh:
                # Necesita al menos 2 cierres consecutivos arriba
                closes_above = [b for b in recent_bars[-5:] if b.close > sh.price]
                if len(closes_above) >= 1:
                    return True, sh.price

        elif bias == "bearish":
            sl = next((s for s in reversed(swings) if s.type in ["HL", "LL"]), None)
            if sl:
                closes_below = [b for b in recent_bars[-5:] if b.close < sl.price]
                if len(closes_below) >= 1:
                    return True, sl.price

        return False, 0.0

    # ─── CHOCH ────────────────────────────────────────────────

    def _detect_choch(
        self, bars: list[Bar], swings: list[SwingPoint], bias: str
    ) -> tuple[bool, float]:
        """CHOCH: primer signo de reversión en contra del bias HTF."""
        if len(swings) < 4:
            return False, 0.0

        last_4 = swings[-4:]

        # En tendencia bajista: aparece un HL (Higher Low) = CHOCH bullish
        if bias == "bearish":
            if last_4[-1].type == "HL":
                return True, last_4[-1].price

        # En tendencia alcista: aparece un LH (Lower High) = CHOCH bearish
        elif bias == "bullish":
            if last_4[-1].type == "LH":
                return True, last_4[-1].price

        return False, 0.0

    # ─── Order Blocks ─────────────────────────────────────────

    def _detect_ob(self, bars: list[Bar], bias: str) -> list[OrderBlock]:
        obs: list[OrderBlock] = []
        lookback = min(OB_LOOKBACK, len(bars) - 3)
        current = bars[-1].close
        avg_vol = np.mean([b.volume for b in bars[-30:]])

        for i in range(2, lookback):
            b    = bars[-(i + 2)]
            next1= bars[-(i + 1)]
            next2= bars[-i]

            # Bullish OB: vela bajista seguida de impulso alcista fuerte
            if b.is_bearish and bias == "bullish":
                move_up = (next2.close - b.close) / (b.close + 1e-9)
                if move_up > 0.002:
                    vol_factor = b.volume / (avg_vol + 1e-9)
                    strength = min(100, move_up * 5000 + vol_factor * 15)
                    ob = OrderBlock(
                        index=len(bars) - i - 2,
                        type="bullish",
                        high=b.high, low=b.low,
                        volume=b.volume, strength=round(strength, 1)
                    )
                    # Verificar mitigación
                    subsequent = bars[-(i):]
                    for sb in subsequent:
                        if sb.low < ob.low:
                            ob.mitigated = True
                            break
                    if not ob.mitigated and current > ob.low:
                        obs.append(ob)

            # Bearish OB: vela alcista seguida de impulso bajista fuerte
            elif b.is_bullish and bias == "bearish":
                move_dn = (b.close - next2.close) / (b.close + 1e-9)
                if move_dn > 0.002:
                    vol_factor = b.volume / (avg_vol + 1e-9)
                    strength = min(100, move_dn * 5000 + vol_factor * 15)
                    ob = OrderBlock(
                        index=len(bars) - i - 2,
                        type="bearish",
                        high=b.high, low=b.low,
                        volume=b.volume, strength=round(strength, 1)
                    )
                    subsequent = bars[-(i):]
                    for sb in subsequent:
                        if sb.high > ob.high:
                            ob.mitigated = True
                            break
                    if not ob.mitigated and current < ob.high:
                        obs.append(ob)

        return sorted(obs, key=lambda x: x.strength, reverse=True)[:5]

    # ─── FVG ──────────────────────────────────────────────────

    def _detect_fvg(self, bars: list[Bar]) -> list[FVG]:
        fvgs: list[FVG] = []

        for i in range(1, len(bars) - 1):
            prev = bars[i - 1]
            curr = bars[i]
            nxt  = bars[i + 1]
            price_ref = curr.close

            # Bullish FVG: gap entre high anterior y low siguiente
            if nxt.low > prev.high:
                size = nxt.low - prev.high
                if size / price_ref >= FVG_MIN_PCT:
                    fvg = FVG(
                        index=i, type="bullish",
                        high=nxt.low, low=prev.high, size=size
                    )
                    for sb in bars[i + 1:]:
                        if sb.low <= fvg.high:
                            fvg.fill_pct = min(100, (fvg.high - sb.low) / fvg.size * 100)
                        if sb.close < fvg.low:
                            fvg.filled = True
                            break
                    fvgs.append(fvg)

            # Bearish FVG
            elif nxt.high < prev.low:
                size = prev.low - nxt.high
                if size / price_ref >= FVG_MIN_PCT:
                    fvg = FVG(
                        index=i, type="bearish",
                        high=prev.low, low=nxt.high, size=size
                    )
                    for sb in bars[i + 1:]:
                        if sb.high >= fvg.low:
                            fvg.fill_pct = min(100, (sb.high - fvg.low) / fvg.size * 100)
                        if sb.close > fvg.high:
                            fvg.filled = True
                            break
                    fvgs.append(fvg)

        return [f for f in fvgs if not f.filled][-8:]

    # ─── Liquidez ─────────────────────────────────────────────

    def _detect_liquidity(self, bars: list[Bar]) -> list[LiquidityLevel]:
        levels: list[LiquidityLevel] = []
        recent = bars[-80:]
        current = bars[-1].close

        highs = [(i, b.high) for i, b in enumerate(recent)]
        lows  = [(i, b.low)  for i, b in enumerate(recent)]

        # Equal highs
        for i in range(len(highs)):
            for j in range(i + 4, len(highs)):
                diff = abs(highs[i][1] - highs[j][1]) / (highs[i][1] + 1e-9)
                if diff <= LIQ_TOLERANCE:
                    price = (highs[i][1] + highs[j][1]) / 2
                    swept = current > price * 1.001
                    levels.append(LiquidityLevel(
                        price=price, type="equal_highs",
                        strength=min(100, 100 - (j - i) * 1.5),
                        swept=swept
                    ))
                    break

        # Equal lows
        for i in range(len(lows)):
            for j in range(i + 4, len(lows)):
                diff = abs(lows[i][1] - lows[j][1]) / (lows[i][1] + 1e-9)
                if diff <= LIQ_TOLERANCE:
                    price = (lows[i][1] + lows[j][1]) / 2
                    swept = current < price * 0.999
                    levels.append(LiquidityLevel(
                        price=price, type="equal_lows",
                        strength=min(100, 100 - (j - i) * 1.5),
                        swept=swept
                    ))
                    break

        return sorted(levels, key=lambda x: x.strength, reverse=True)[:6]

    # ─── Premium / Discount ───────────────────────────────────

    def _premium_discount(self, bars: list[Bar]) -> tuple[str, float]:
        recent = bars[-50:]
        sh = max(b.high for b in recent)
        sl = min(b.low  for b in recent)
        eq = (sh + sl) / 2
        current = bars[-1].close
        rng = sh - sl
        if rng == 0:
            return "equilibrium", eq
        pos = (current - sl) / rng
        if pos > 0.62:
            return "premium", eq
        elif pos < 0.38:
            return "discount", eq
        return "equilibrium", eq

    # ─── Score ────────────────────────────────────────────────

    def _score(self, r: SMCAnalysis) -> float:
        score = 0.0

        # Estructura (25 pts)
        if r.bias != "neutral":     score += 25
        # BOS (20 pts)
        if r.bos:                   score += 20
        # CHOCH (15 pts)
        if r.choch:                 score += 15
        # OBs válidos (15 pts)
        valid_obs = [o for o in r.order_blocks if not o.mitigated]
        score += min(15, len(valid_obs) * 5)
        # FVGs (10 pts)
        valid_fvg = [f for f in r.fvgs if not f.filled]
        score += min(10, len(valid_fvg) * 3)
        # Liquidez barrida (10 pts)
        swept = [l for l in r.liquidity if l.swept]
        if swept: score += 10
        # Premium/Discount alineado (5 pts)
        if r.bias == "bullish" and r.premium_discount == "discount": score += 5
        if r.bias == "bearish" and r.premium_discount == "premium":  score += 5

        return min(100.0, score)


# ─── Multi-TF SMC Aggregator ─────────────────────────────────

class MultiTFSMC:
    def __init__(self):
        self.engine = SMCEngine()

    def analyze_all(self, mtf: MultiTF) -> dict[str, SMCAnalysis]:
        """Analiza todos los timeframes disponibles."""
        results = {}
        for tf in ["D1", "H4", "H1", "M15", "M5", "M1"]:
            data = mtf.get(tf)
            if data and len(data.bars) >= 60:
                results[tf] = self.engine.analyze(data)
        return results

    def get_htf_bias(self, analyses: dict[str, SMCAnalysis]) -> str:
        """Bias consolidado D1 + H4."""
        biases = []
        for tf in ["D1", "H4"]:
            if tf in analyses:
                biases.append(analyses[tf].bias)
        bull = biases.count("bullish")
        bear = biases.count("bearish")
        if bull > bear: return "bullish"
        if bear > bull: return "bearish"
        return "neutral"

    def get_entry_tf_score(self, analyses: dict[str, SMCAnalysis], direction: str) -> float:
        """Score de los TF de entrada (H1 + M15 + M5)."""
        total, weight = 0.0, 0.0
        weights = {"H1": 0.50, "M15": 0.35, "M5": 0.15}
        for tf, w in weights.items():
            if tf in analyses:
                a = analyses[tf]
                if a.bias == direction or a.bias == "neutral":
                    total += a.score * w
                    weight += w
        return (total / weight) if weight > 0 else 0.0
