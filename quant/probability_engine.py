"""
PROBABILITY ENGINE — Motor Cuantitativo Estadístico
Score probabilístico real basado en confluencias,
contexto histórico y validación estadística institucional.
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from data.market_data import MarketData, Bar, atr, ema, rsi

logger = logging.getLogger("ProbabilityEngine")


@dataclass
class ProbabilityResult:
    symbol: str
    timeframe: str
    direction: str
    probability: float      # 0.0 - 1.0
    confidence: str         # LOW | MEDIUM | HIGH | VERY_HIGH
    expected_value: float   # EV del trade
    win_rate_estimate: float
    rr_needed: float        # RR mínimo para EV positivo
    regime: str             # TRENDING | RANGING | VOLATILE | COMPRESSION
    regime_confidence: float
    momentum_strength: float
    mean_reversion_score: float
    trend_alignment: float
    volatility_percentile: float
    sample_size: int
    factors: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "probability": round(self.probability * 100, 1),
            "confidence": self.confidence,
            "ev": round(self.expected_value, 3),
            "win_rate_est": round(self.win_rate_estimate * 100, 1),
            "rr_needed": round(self.rr_needed, 2),
            "regime": self.regime,
            "momentum": round(self.momentum_strength, 2),
            "trend_align": round(self.trend_alignment, 2),
            "vol_pct": round(self.volatility_percentile, 1),
        }


class ProbabilityEngine:
    def __init__(self):
        logger.info("ProbabilityEngine cuantitativo iniciado")

    def analyze(self, data: MarketData, direction: str) -> ProbabilityResult:
        bars = data.bars
        if len(bars) < 50:
            return self._default_result(data.symbol, data.timeframe, direction)

        closes  = np.array([b.close  for b in bars])
        highs   = np.array([b.high   for b in bars])
        lows    = np.array([b.low    for b in bars])
        volumes = np.array([b.volume for b in bars])

        atr_val = atr(bars, 14)

        # ── 1. Regime Detection ──────────────────────────────
        regime, regime_conf = self._detect_regime(closes, atr_val)

        # ── 2. Momentum ──────────────────────────────────────
        momentum = self._momentum_strength(closes, direction)

        # ── 3. Mean Reversion ────────────────────────────────
        mean_rev = self._mean_reversion_score(closes, direction)

        # ── 4. Trend Alignment ───────────────────────────────
        trend_align = self._trend_alignment(closes, direction)

        # ── 5. Volatility Percentile ─────────────────────────
        vol_pct = self._volatility_percentile(bars, atr_val)

        # ── 6. Historical Pattern Win Rate ───────────────────
        pattern_wr = self._historical_pattern_wr(bars, direction)

        # ── 7. Probability Compuesta ─────────────────────────
        # Pesos según régimen
        if regime == "TRENDING":
            weights = {"momentum": 0.35, "trend": 0.30, "pattern": 0.20, "vol": 0.10, "mean_rev": 0.05}
        elif regime == "RANGING":
            weights = {"momentum": 0.15, "trend": 0.10, "pattern": 0.25, "vol": 0.15, "mean_rev": 0.35}
        elif regime == "VOLATILE":
            weights = {"momentum": 0.20, "trend": 0.20, "pattern": 0.30, "vol": 0.20, "mean_rev": 0.10}
        else:  # COMPRESSION
            weights = {"momentum": 0.25, "trend": 0.25, "pattern": 0.25, "vol": 0.15, "mean_rev": 0.10}

        probability = (
            momentum    * weights["momentum"] +
            trend_align * weights["trend"] +
            pattern_wr  * weights["pattern"] +
            (1 - vol_pct/100) * weights["vol"] +  # alta volatilidad penaliza
            mean_rev    * weights["mean_rev"]
        )
        probability = max(0.0, min(1.0, probability))

        # ── 8. Expected Value ─────────────────────────────────
        win_rate_est = probability
        avg_rr = 2.5  # asume RR mínimo del sistema
        ev = (win_rate_est * avg_rr) - (1 - win_rate_est)

        # RR mínimo para EV positivo
        if win_rate_est > 0:
            rr_needed = (1 - win_rate_est) / win_rate_est
        else:
            rr_needed = 999.0

        # ── 9. Confidence Level ───────────────────────────────
        if probability >= 0.75:   confidence = "VERY_HIGH"
        elif probability >= 0.60: confidence = "HIGH"
        elif probability >= 0.45: confidence = "MEDIUM"
        else:                     confidence = "LOW"

        return ProbabilityResult(
            symbol=data.symbol,
            timeframe=data.timeframe,
            direction=direction,
            probability=round(probability, 4),
            confidence=confidence,
            expected_value=round(ev, 4),
            win_rate_estimate=round(win_rate_est, 4),
            rr_needed=round(rr_needed, 2),
            regime=regime,
            regime_confidence=round(regime_conf, 1),
            momentum_strength=round(momentum, 3),
            mean_reversion_score=round(mean_rev, 3),
            trend_alignment=round(trend_align, 3),
            volatility_percentile=round(vol_pct, 1),
            sample_size=len(bars),
            factors={
                "momentum": round(momentum, 3),
                "trend": round(trend_align, 3),
                "pattern": round(pattern_wr, 3),
                "mean_rev": round(mean_rev, 3),
                "vol_pct": round(vol_pct, 1),
                "regime": regime,
            }
        )

    # ─── Regime Detection ─────────────────────────────────────

    def _detect_regime(self, closes, atr_val) -> tuple:
        if len(closes) < 30:
            return "UNKNOWN", 0.0

        # ADX aproximado
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50) if len(closes) >= 50 else ema20

        # Pendiente de EMA
        slope20 = (ema20[-1] - ema20[-10]) / (abs(ema20[-10]) + 1e-9) * 100
        slope50 = (ema50[-1] - ema50[-10]) / (abs(ema50[-10]) + 1e-9) * 100

        # Volatilidad reciente vs histórica
        recent_atr  = np.mean([abs(closes[i] - closes[i-1]) for i in range(-5, 0)])
        hist_atr    = np.mean([abs(closes[i] - closes[i-1]) for i in range(-30, -5)])
        vol_ratio   = recent_atr / (hist_atr + 1e-9)

        # Rango de precio reciente
        recent_range = (np.max(closes[-20:]) - np.min(closes[-20:])) / (abs(closes[-20]) + 1e-9) * 100

        # Clasificar
        abs_slope = abs(slope20)

        if abs_slope > 0.5 and abs(slope50) > 0.3:
            regime = "TRENDING"
            conf = min(100, abs_slope * 50 + 30)
        elif vol_ratio > 1.8:
            regime = "VOLATILE"
            conf = min(100, vol_ratio * 30 + 20)
        elif recent_range < 0.5 and abs_slope < 0.1:
            regime = "COMPRESSION"
            conf = min(100, (0.5 - recent_range) * 100 + 30)
        else:
            regime = "RANGING"
            conf = 50.0

        return regime, conf

    # ─── Momentum Strength ────────────────────────────────────

    def _momentum_strength(self, closes, direction) -> float:
        if len(closes) < 20:
            return 0.5

        rsi_val = rsi(closes, 14)
        ema9    = ema(closes, 9)[-1]
        ema21   = ema(closes, 21)[-1] if len(closes) >= 21 else ema9
        current = closes[-1]

        # Rate of change
        roc5  = (closes[-1] - closes[-5])  / (abs(closes[-5])  + 1e-9)
        roc10 = (closes[-1] - closes[-10]) / (abs(closes[-10]) + 1e-9)

        if direction == "buy":
            rsi_score  = 1.0 if 45 <= rsi_val <= 70 else (0.6 if 35 <= rsi_val < 45 else 0.2)
            ema_score  = 1.0 if current > ema9 > ema21 else (0.5 if current > ema21 else 0.0)
            roc_score  = min(1.0, max(0.0, roc5 * 20 + 0.5))
        else:
            rsi_score  = 1.0 if 30 <= rsi_val <= 55 else (0.6 if 55 < rsi_val <= 65 else 0.2)
            ema_score  = 1.0 if current < ema9 < ema21 else (0.5 if current < ema21 else 0.0)
            roc_score  = min(1.0, max(0.0, -roc5 * 20 + 0.5))

        return rsi_score * 0.40 + ema_score * 0.35 + roc_score * 0.25

    # ─── Mean Reversion Score ─────────────────────────────────

    def _mean_reversion_score(self, closes, direction) -> float:
        if len(closes) < 20:
            return 0.5

        mean = np.mean(closes[-20:])
        std  = np.std(closes[-20:])
        current = closes[-1]

        if std == 0:
            return 0.5

        z_score = (current - mean) / std

        # Score de reversión: cuanto más lejos de la media, más probable reversión
        if direction == "buy":
            # Queremos precio en zona de descuento (z_score negativo)
            if z_score < -1.5:   return 0.90
            elif z_score < -1.0: return 0.75
            elif z_score < -0.5: return 0.60
            elif z_score < 0:    return 0.50
            else:                return 0.25  # precio en premium, no ideal para buy
        else:
            # Queremos precio en zona de premium (z_score positivo)
            if z_score > 1.5:   return 0.90
            elif z_score > 1.0: return 0.75
            elif z_score > 0.5: return 0.60
            elif z_score > 0:   return 0.50
            else:               return 0.25

    # ─── Trend Alignment ──────────────────────────────────────

    def _trend_alignment(self, closes, direction) -> float:
        if len(closes) < 50:
            return 0.5

        ema20 = ema(closes, 20)[-1]
        ema50 = ema(closes, 50)[-1]
        ema200 = ema(closes, 200)[-1] if len(closes) >= 200 else ema50
        current = closes[-1]

        if direction == "buy":
            score = 0.0
            if current > ema20:  score += 0.30
            if ema20 > ema50:    score += 0.25
            if ema50 > ema200:   score += 0.25
            if current > ema200: score += 0.20
            return score
        else:
            score = 0.0
            if current < ema20:  score += 0.30
            if ema20 < ema50:    score += 0.25
            if ema50 < ema200:   score += 0.25
            if current < ema200: score += 0.20
            return score

    # ─── Volatility Percentile ────────────────────────────────

    def _volatility_percentile(self, bars, atr_val) -> float:
        if len(bars) < 20:
            return 50.0

        # ATR histórico de las últimas 100 velas
        hist_atrs = []
        for i in range(1, min(100, len(bars))):
            tr = max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i-1].close),
                abs(bars[i].low  - bars[i-1].close),
            )
            hist_atrs.append(tr)

        if not hist_atrs:
            return 50.0

        # Percentil del ATR actual
        below = sum(1 for x in hist_atrs if x <= atr_val)
        return (below / len(hist_atrs)) * 100

    # ─── Historical Pattern Win Rate ──────────────────────────

    def _historical_pattern_wr(self, bars, direction) -> float:
        """
        Calcula win rate histórico para la dirección dada
        basado en patrones similares en el historial de precios.
        """
        if len(bars) < 50:
            return 0.50

        closes = np.array([b.close for b in bars])
        wins = 0
        total = 0

        # Buscar patrones similares en el historial
        current_rsi = rsi(closes, 14)
        ema20_val   = ema(closes, 20)[-1]
        current     = closes[-1]

        # Simular cuántas veces un setup similar resultó en ganancia
        for i in range(20, len(bars) - 5):
            historical_rsi = rsi(closes[:i+1], 14)
            historical_ema = ema(closes[:i+1], 20)[-1]
            hist_price     = closes[i]

            # Condición similar: RSI y posición respecto a EMA similares
            rsi_similar = abs(historical_rsi - current_rsi) < 10
            ema_similar = abs((hist_price - historical_ema) / (historical_ema + 1e-9)) < 0.01

            if rsi_similar and ema_similar:
                total += 1
                future_price = closes[min(i+5, len(closes)-1)]
                if direction == "buy" and future_price > hist_price:
                    wins += 1
                elif direction == "sell" and future_price < hist_price:
                    wins += 1

        if total < 5:
            return 0.50

        return wins / total

    def _default_result(self, symbol, timeframe, direction):
        return ProbabilityResult(
            symbol=symbol, timeframe=timeframe, direction=direction,
            probability=0.50, confidence="LOW", expected_value=0.0,
            win_rate_estimate=0.50, rr_needed=1.0,
            regime="UNKNOWN", regime_confidence=0.0,
            momentum_strength=0.5, mean_reversion_score=0.5,
            trend_alignment=0.5, volatility_percentile=50.0,
            sample_size=0,
        )
