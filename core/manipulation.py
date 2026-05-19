"""
MANIPULATION ENGINE — Deteccion Institucional
Stop Hunt, Wyckoff, Trampas de Mercado,
Spring/Upthrust, Accumulation/Distribution.
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from data.market_data import MarketData, Bar, atr, ema

logger = logging.getLogger("ManipulationEngine")


# ─── Estructuras ─────────────────────────────────────────────

@dataclass
class ManipulationEvent:
    type: str           # STOP_HUNT | SPRING | UPTHRUST | FAKE_BREAK | WYCKOFF_ACCUM | WYCKOFF_DIST
    direction: str      # bullish | bearish (direccion POST manipulacion)
    price: float
    bar_index: int
    strength: float     # 0-100
    description: str
    tradeable: bool = False  # True si es accionable ahora

    def to_dict(self):
        return {
            "type": self.type,
            "direction": self.direction,
            "price": self.price,
            "strength": round(self.strength, 1),
            "tradeable": self.tradeable,
            "description": self.description,
        }


@dataclass
class WyckoffPhase:
    phase: str          # ACCUMULATION | DISTRIBUTION | MARKUP | MARKDOWN | REACCUMULATION
    confidence: float   # 0-100
    support: float
    resistance: float
    direction_bias: str # bullish | bearish | neutral


@dataclass
class ManipulationAnalysis:
    symbol: str
    timeframe: str
    events: list = field(default_factory=list)
    wyckoff: Optional[WyckoffPhase] = None
    stop_hunt_detected: bool = False
    fake_break_detected: bool = False
    spring_detected: bool = False
    upthrust_detected: bool = False
    manipulation_score: float = 0.0
    bias_after_manipulation: str = "neutral"
    most_recent: Optional[ManipulationEvent] = None

    def to_dict(self):
        return {
            "events": len(self.events),
            "stop_hunt": self.stop_hunt_detected,
            "fake_break": self.fake_break_detected,
            "spring": self.spring_detected,
            "upthrust": self.upthrust_detected,
            "score": round(self.manipulation_score, 1),
            "bias": self.bias_after_manipulation,
            "wyckoff": self.wyckoff.phase if self.wyckoff else "unknown",
            "recent": self.most_recent.to_dict() if self.most_recent else None,
        }


# ─── Manipulation Engine ──────────────────────────────────────

class ManipulationEngine:
    def __init__(self):
        self.lookback = 50
        logger.info("ManipulationEngine institucional iniciado")

    def analyze(self, data: MarketData) -> ManipulationAnalysis:
        bars = data.bars
        result = ManipulationAnalysis(symbol=data.symbol, timeframe=data.timeframe)

        if len(bars) < 30:
            return result

        atr_val = atr(bars, 14)
        if atr_val == 0:
            return result

        events = []

        # 1. Stop Hunt
        sh_events = self._detect_stop_hunt(bars, atr_val)
        events.extend(sh_events)
        result.stop_hunt_detected = bool(sh_events)

        # 2. Spring y Upthrust (Wyckoff)
        spring_events = self._detect_spring(bars, atr_val)
        events.extend(spring_events)
        result.spring_detected = bool(spring_events)

        upthrust_events = self._detect_upthrust(bars, atr_val)
        events.extend(upthrust_events)
        result.upthrust_detected = bool(upthrust_events)

        # 3. Fake Break
        fb_events = self._detect_fake_break(bars, atr_val)
        events.extend(fb_events)
        result.fake_break_detected = bool(fb_events)

        # 4. Wyckoff Phase
        result.wyckoff = self._detect_wyckoff_phase(bars)

        # 5. Ordenar y filtrar eventos recientes
        events.sort(key=lambda x: x.bar_index)
        recent_cutoff = len(bars) - 30
        result.events = [e for e in events if e.bar_index >= recent_cutoff]

        if result.events:
            result.most_recent = result.events[-1]
            result.bias_after_manipulation = result.most_recent.direction

        # 6. Score
        result.manipulation_score = self._calculate_score(result)

        return result

    # ─── Stop Hunt ────────────────────────────────────────────

    def _detect_stop_hunt(self, bars, atr_val):
        events = []
        recent = bars[-self.lookback:]

        for i in range(5, len(recent) - 2):
            bar = recent[i]
            prev_bars = recent[max(0, i-10):i]
            next_bars = recent[i+1:min(len(recent), i+4)]

            if not prev_bars or not next_bars:
                continue

            prev_low  = min(b.low for b in prev_bars)
            prev_high = max(b.high for b in prev_bars)

            # Stop Hunt bajista: spike debajo del mínimo previo + recuperación
            if (bar.low < prev_low - atr_val * 0.1 and
                bar.close > prev_low and
                bar.lower_wick > bar.body * 1.5):

                recovery = next_bars[0].close - bar.close if next_bars else 0
                if recovery > 0:
                    strength = min(100,
                        (bar.lower_wick / atr_val) * 40 +
                        (recovery / atr_val) * 30 +
                        20
                    )
                    tradeable = (
                        bar.lower_wick > atr_val * 0.3 and
                        recovery > atr_val * 0.2
                    )
                    events.append(ManipulationEvent(
                        type="STOP_HUNT",
                        direction="bullish",
                        price=bar.low,
                        bar_index=len(bars) - self.lookback + i,
                        strength=round(strength, 1),
                        description=f"Stop hunt bajista: barrió ${bar.low:.5f}, recuperó ${bar.close:.5f}",
                        tradeable=tradeable,
                    ))

            # Stop Hunt alcista: spike encima del máximo previo + caída
            elif (bar.high > prev_high + atr_val * 0.1 and
                  bar.close < prev_high and
                  bar.upper_wick > bar.body * 1.5):

                drop = bar.close - next_bars[0].close if next_bars else 0
                if drop > 0:
                    strength = min(100,
                        (bar.upper_wick / atr_val) * 40 +
                        (drop / atr_val) * 30 +
                        20
                    )
                    tradeable = (
                        bar.upper_wick > atr_val * 0.3 and
                        drop > atr_val * 0.2
                    )
                    events.append(ManipulationEvent(
                        type="STOP_HUNT",
                        direction="bearish",
                        price=bar.high,
                        bar_index=len(bars) - self.lookback + i,
                        strength=round(strength, 1),
                        description=f"Stop hunt alcista: barrió ${bar.high:.5f}, cayó ${bar.close:.5f}",
                        tradeable=tradeable,
                    ))

        return events

    # ─── Spring (Wyckoff) ─────────────────────────────────────

    def _detect_spring(self, bars, atr_val):
        """
        Spring: en zona de soporte, precio rompe brevemente abajo
        y recupera con fuerza → señal de acumulación institucional.
        """
        events = []
        recent = bars[-self.lookback:]

        support_zone = min(b.low for b in recent[:20])

        for i in range(20, len(recent) - 3):
            bar = recent[i]
            next1 = recent[i+1]
            next2 = recent[i+2]

            # Spring: toca o rompe el soporte + cierra arriba + confirmación
            if (bar.low <= support_zone * 1.001 and
                bar.close > support_zone and
                next1.close > bar.close and
                next1.is_bullish):

                move_up = next2.close - bar.low
                strength = min(100,
                    (move_up / atr_val) * 50 +
                    (bar.lower_wick / atr_val) * 30 +
                    20
                )
                if strength >= 40:
                    events.append(ManipulationEvent(
                        type="SPRING",
                        direction="bullish",
                        price=bar.low,
                        bar_index=len(bars) - self.lookback + i,
                        strength=round(strength, 1),
                        description=f"Spring Wyckoff en soporte ${support_zone:.5f}",
                        tradeable=True,
                    ))

        return events[-2:]  # Máximo 2 springs recientes

    # ─── Upthrust (Wyckoff) ───────────────────────────────────

    def _detect_upthrust(self, bars, atr_val):
        """
        Upthrust: en zona de resistencia, precio rompe brevemente arriba
        y cae con fuerza → señal de distribución institucional.
        """
        events = []
        recent = bars[-self.lookback:]

        resistance_zone = max(b.high for b in recent[:20])

        for i in range(20, len(recent) - 3):
            bar = recent[i]
            next1 = recent[i+1]
            next2 = recent[i+2]

            if (bar.high >= resistance_zone * 0.999 and
                bar.close < resistance_zone and
                next1.close < bar.close and
                next1.is_bearish):

                move_dn = bar.high - next2.close
                strength = min(100,
                    (move_dn / atr_val) * 50 +
                    (bar.upper_wick / atr_val) * 30 +
                    20
                )
                if strength >= 40:
                    events.append(ManipulationEvent(
                        type="UPTHRUST",
                        direction="bearish",
                        price=bar.high,
                        bar_index=len(bars) - self.lookback + i,
                        strength=round(strength, 1),
                        description=f"Upthrust Wyckoff en resistencia ${resistance_zone:.5f}",
                        tradeable=True,
                    ))

        return events[-2:]

    # ─── Fake Break ───────────────────────────────────────────

    def _detect_fake_break(self, bars, atr_val):
        """
        Fake Break: rompe un nivel importante pero no mantiene el cierre.
        Trampa clásica institucional para liquidar retail.
        """
        events = []
        recent = bars[-self.lookback:]

        for i in range(10, len(recent) - 2):
            bar = recent[i]
            prev_20 = recent[max(0,i-20):i]
            if not prev_20:
                continue

            prev_high = max(b.high for b in prev_20)
            prev_low  = min(b.low  for b in prev_20)
            next_bar  = recent[i+1]

            # Fake break alcista: rompe el high pero cierra debajo
            if (bar.high > prev_high and
                bar.close < prev_high and
                bar.upper_wick > bar.body * 2 and
                next_bar.is_bearish):

                strength = min(100,
                    (bar.upper_wick / atr_val) * 50 +
                    ((bar.high - prev_high) / atr_val) * 30 +
                    20
                )
                events.append(ManipulationEvent(
                    type="FAKE_BREAK",
                    direction="bearish",
                    price=bar.high,
                    bar_index=len(bars) - self.lookback + i,
                    strength=round(strength, 1),
                    description=f"Fake break alcista en ${prev_high:.5f}",
                    tradeable=True,
                ))

            # Fake break bajista: rompe el low pero cierra arriba
            elif (bar.low < prev_low and
                  bar.close > prev_low and
                  bar.lower_wick > bar.body * 2 and
                  next_bar.is_bullish):

                strength = min(100,
                    (bar.lower_wick / atr_val) * 50 +
                    ((prev_low - bar.low) / atr_val) * 30 +
                    20
                )
                events.append(ManipulationEvent(
                    type="FAKE_BREAK",
                    direction="bullish",
                    price=bar.low,
                    bar_index=len(bars) - self.lookback + i,
                    strength=round(strength, 1),
                    description=f"Fake break bajista en ${prev_low:.5f}",
                    tradeable=True,
                ))

        return events[-3:]

    # ─── Wyckoff Phase ────────────────────────────────────────

    def _detect_wyckoff_phase(self, bars) -> WyckoffPhase:
        """
        Detecta la fase Wyckoff actual basada en estructura de precio y volumen.
        """
        recent = bars[-60:]
        if len(recent) < 30:
            return WyckoffPhase("UNKNOWN", 0, 0, 0, "neutral")

        highs   = np.array([b.high   for b in recent])
        lows    = np.array([b.low    for b in recent])
        closes  = np.array([b.close  for b in recent])
        volumes = np.array([b.volume for b in recent])

        support    = np.min(lows)
        resistance = np.max(highs)
        mid        = (support + resistance) / 2
        current    = closes[-1]

        # Rango de precio
        price_range = resistance - support
        if price_range == 0:
            return WyckoffPhase("RANGING", 50, support, resistance, "neutral")

        # Posicion relativa
        position = (current - support) / price_range

        # Volumen promedio primera mitad vs segunda mitad
        vol_first  = np.mean(volumes[:len(volumes)//2])
        vol_second = np.mean(volumes[len(volumes)//2:])
        vol_trend  = vol_second / (vol_first + 1e-9)

        # Precio tendencia
        ema20 = ema(closes, 20)
        price_trend = (ema20[-1] - ema20[0]) / (abs(ema20[0]) + 1e-9)

        # Clasificar fase
        if position < 0.35 and vol_trend > 1.2:
            return WyckoffPhase("ACCUMULATION", 70, support, resistance, "bullish")
        elif position > 0.65 and vol_trend > 1.2:
            return WyckoffPhase("DISTRIBUTION", 70, support, resistance, "bearish")
        elif price_trend > 0.02 and position > 0.5:
            return WyckoffPhase("MARKUP", 65, support, resistance, "bullish")
        elif price_trend < -0.02 and position < 0.5:
            return WyckoffPhase("MARKDOWN", 65, support, resistance, "bearish")
        elif 0.35 <= position <= 0.65 and vol_trend < 0.9:
            return WyckoffPhase("REACCUMULATION", 55, support, resistance, "bullish")
        else:
            return WyckoffPhase("RANGING", 40, support, resistance, "neutral")

    # ─── Score ────────────────────────────────────────────────

    def _calculate_score(self, result: ManipulationAnalysis) -> float:
        score = 0.0
        if result.stop_hunt_detected:  score += 25
        if result.spring_detected:     score += 25
        if result.upthrust_detected:   score += 25
        if result.fake_break_detected: score += 20
        if result.wyckoff:
            if result.wyckoff.confidence >= 60:
                score += 15
            elif result.wyckoff.confidence >= 40:
                score += 8
        tradeable = [e for e in result.events if e.tradeable]
        score += min(20, len(tradeable) * 10)
        return min(100.0, score)
