"""
QUANT SCORING ENGINE — V2 — Fase 2 + Macro + Nuevas herramientas
Score probabilístico multi-timeframe institucional.

V2 agrega:
  - Displacement (desplazamiento institucional)
  - Retest ATR-based (calidad de entrada)
  - Judas Swing (movimiento falso de sesión)
  - Inducción (trampa de liquidez EQH/EQL)

Pesos redistribuidos para sumar 1.0 con los 4 componentes nuevos.
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from data.market_data import MultiTF, ema, atr, rsi
from core.structure import MultiTFSMC, SMCAnalysis
from core.sweep_engine import SweepEngine, FVGEngine, MitigationEngine
from core.news_engine import NewsEngine
from core.learning_engine import LearningEngine
from core.manipulation import ManipulationEngine
from macro.macro_engine import MacroEngine
from quant.probability_engine import ProbabilityEngine
from core.displacement import displacement_score, get_displacement_direction
from core.retest import retest_score, find_retest_level
from core.judas_swing import get_judas_score, analyze_judas_swing
from core.induccion import get_induccion_score, analyze_induccion
from config import TF_WEIGHTS, SCORE_THRESHOLD, TRADE_SESSIONS, SYMBOLS

logger = logging.getLogger("QuantScore")

# Instancias compartidas
_sweep_engine        = SweepEngine()
_fvg_engine          = FVGEngine()
_mitigation_engine   = MitigationEngine()
_news_engine         = NewsEngine()
_learning_engine     = LearningEngine()
_manipulation_engine = ManipulationEngine()
_macro_engine        = MacroEngine()
_probability_engine  = ProbabilityEngine()


@dataclass
class QuantResult:
    symbol: str
    direction: str
    final_score: float
    smc_score: float
    momentum_score: float
    volatility_score: float
    session_score: float
    confluence_score: float
    sweep_score: float
    fvg_score: float
    news_score: float
    learning_score: float
    macro_score: float
    manipulation_score: float
    probability_score: float
    displacement_score: float = 0.0
    retest_score: float       = 0.0
    judas_score: float        = 0.5
    induccion_score: float    = 0.5
    htf_bias: str             = "neutral"
    entry_tf_score: float     = 0.0
    pattern: str              = "none"
    entry: float              = 0.0
    stop_loss: float          = 0.0
    take_profit: float        = 0.0
    rr: float                 = 0.0
    atr_h1: float             = 0.0
    sweep_detected: bool      = False
    manipulation_detected: bool = False
    displacement_valid: bool  = False
    judas_detected: bool      = False
    induccion_detected: bool  = False
    wyckoff_phase: str        = "unknown"
    news_warning: str         = "none"
    macro_bias: str           = "neutral"
    risk_sentiment: str       = "neutral"
    market_regime: str        = "unknown"
    valid: bool               = False
    reject_reason: str        = ""

    def to_dict(self) -> dict:
        return {
            "symbol":          self.symbol,
            "direction":       self.direction,
            "score":           round(self.final_score * 100, 1),
            "smc":             round(self.smc_score * 100, 1),
            "momentum":        round(self.momentum_score * 100, 1),
            "volatility":      round(self.volatility_score * 100, 1),
            "session":         round(self.session_score * 100, 1),
            "confluence":      round(self.confluence_score * 100, 1),
            "sweep":           round(self.sweep_score * 100, 1),
            "fvg":             round(self.fvg_score * 100, 1),
            "macro":           round(self.macro_score * 100, 1),
            "manipulation":    round(self.manipulation_score * 100, 1),
            "probability":     round(self.probability_score * 100, 1),
            "displacement":    round(self.displacement_score * 100, 1),
            "retest":          round(self.retest_score * 100, 1),
            "judas_swing":     round(self.judas_score * 100, 1),
            "induccion":       round(self.induccion_score * 100, 1),
            "news":            self.news_warning,
            "learning":        round(self.learning_score * 100, 1),
            "htf_bias":        self.htf_bias,
            "pattern":         self.pattern,
            "entry":           self.entry,
            "sl":              self.stop_loss,
            "tp":              self.take_profit,
            "rr":              round(self.rr, 2),
            "sweep_detected":  self.sweep_detected,
            "manipulation_detected": self.manipulation_detected,
            "displacement_valid":    self.displacement_valid,
            "judas_detected":        self.judas_detected,
            "induccion_detected":    self.induccion_detected,
            "wyckoff":         self.wyckoff_phase,
            "macro_bias":      self.macro_bias,
            "risk_sentiment":  self.risk_sentiment,
            "regime":          self.market_regime,
            "valid":           self.valid,
            "reject":          self.reject_reason,
        }


class QuantScoringEngine:
    def __init__(self):
        self.smc = MultiTFSMC()
        logger.info("QuantScoringEngine Fase 2 + Macro iniciado")

    async def analyze(self, mtf: MultiTF) -> QuantResult:
        symbol = mtf.symbol
        result = QuantResult(
            symbol=symbol, direction="none", final_score=0.0,
            smc_score=0.0, momentum_score=0.0, volatility_score=0.0,
            session_score=0.0, confluence_score=0.0,
            sweep_score=0.0, fvg_score=0.0,
            news_score=1.0, learning_score=0.5,
            macro_score=0.5, manipulation_score=0.0,
            probability_score=0.5,
            displacement_score=0.0, retest_score=0.0,
            judas_score=0.5, induccion_score=0.5,
            htf_bias="neutral", entry_tf_score=0.0,
            pattern="none", entry=0.0, stop_loss=0.0,
            take_profit=0.0, rr=0.0, atr_h1=0.0,
        )

        if not mtf.is_complete():
            result.reject_reason = "datos insuficientes"
            return result

        # ── 1. Noticias ───────────────────────────────────────
        news = await _news_engine.analyze([symbol])
        result.news_warning = news.volatility_warning
        if news.should_pause:
            result.reject_reason = f"noticia alto impacto: {news.volatility_warning}"
            return result
        if news.volatility_warning == "medium":
            result.news_score = 0.7
        elif news.volatility_warning == "high":
            result.news_score = 0.4
        else:
            result.news_score = 1.0

        # ── 2. Macro Engine ───────────────────────────────────
        try:
            macro = await _macro_engine.analyze(SYMBOLS)
            result.macro_score    = macro.get_symbol_score(symbol)
            result.macro_bias     = macro.macro_bias.get(symbol, "neutral")
            result.risk_sentiment = macro.risk_sentiment
        except Exception as e:
            logger.debug(f"Macro engine error: {e}")
            result.macro_score = 0.5

        # ── 3. SMC Multi-TF ───────────────────────────────────
        analyses = self.smc.analyze_all(mtf)
        htf_bias = self.smc.get_htf_bias(analyses)
        result.htf_bias = htf_bias

        if htf_bias == "neutral":
            result.reject_reason = "bias HTF neutral"
            return result

        direction = "buy" if htf_bias == "bullish" else "sell"
        result.direction = direction

        if result.macro_bias != "neutral" and result.macro_bias != direction:
            result.macro_score *= 0.5

        smc_weighted = smc_weight = 0.0
        for tf, w in TF_WEIGHTS.items():
            if tf in analyses:
                a = analyses[tf]
                align = 1.0 if a.bias == htf_bias else (0.5 if a.bias == "neutral" else 0.0)
                smc_weighted += (a.score / 100) * w * align
                smc_weight   += w
        result.smc_score      = smc_weighted / smc_weight if smc_weight > 0 else 0.0
        result.entry_tf_score = self.smc.get_entry_tf_score(analyses, htf_bias) / 100

        # ── 4. Sweep Engine ───────────────────────────────────
        h1_data = mtf.H1
        h1_bars = h1_data.bars if h1_data else []

        if h1_data:
            sweep_analysis = _sweep_engine.analyze(h1_data)
            result.sweep_score         = sweep_analysis.sweep_score / 100
            result.sweep_detected      = bool(sweep_analysis.sweep_events)
            result.manipulation_detected = sweep_analysis.manipulation_detected
            if sweep_analysis.recent_sweep and sweep_analysis.recent_sweep.direction != direction:
                result.sweep_score *= 0.5

        # ── 5. Manipulation Engine ────────────────────────────
        if h1_data:
            manip = _manipulation_engine.analyze(h1_data)
            result.manipulation_score = manip.manipulation_score / 100
            result.wyckoff_phase      = manip.wyckoff.phase if manip.wyckoff else "unknown"
            if manip.most_recent:
                if manip.most_recent.direction == direction:
                    result.manipulation_score = min(1.0, result.manipulation_score * 1.2)
                else:
                    result.manipulation_score *= 0.6

        # ── 6. FVG Institucional ──────────────────────────────
        if h1_data:
            institutional_fvgs = _fvg_engine.analyze(h1_data)
            premium_fvgs = [f for f in institutional_fvgs if f.is_premium]
            if premium_fvgs:
                fvg_aligned = [
                    f for f in premium_fvgs
                    if (direction == "buy"  and f.type == "bullish") or
                       (direction == "sell" and f.type == "bearish")
                ]
                result.fvg_score = min(1.0, len(fvg_aligned) * 0.4 + 0.2)
            elif institutional_fvgs:
                result.fvg_score = 0.3

        # ── 7. Probability Engine ─────────────────────────────
        if h1_data:
            try:
                prob = _probability_engine.analyze(h1_data, direction)
                result.probability_score = prob.probability
                result.market_regime     = prob.regime
            except Exception as e:
                logger.debug(f"Probability engine error: {e}")
                result.probability_score = 0.5

        # ── 8. Momentum ───────────────────────────────────────
        result.momentum_score = self._momentum_score(mtf, direction)

        # ── 9. Volatilidad ────────────────────────────────────
        result.volatility_score, result.atr_h1 = self._volatility_score(mtf, symbol)
        h1_atr = result.atr_h1

        # ── 10. Displacement (NUEVO) ──────────────────────────
        if h1_bars and len(h1_bars) >= 10:
            d_score, d_valid = displacement_score(h1_bars, len(h1_bars) - 1, h1_atr)
            d_dir = get_displacement_direction(h1_bars, len(h1_bars) - 1)
            result.displacement_valid = d_valid
            if d_valid and d_dir == direction:
                result.displacement_score = d_score
            elif d_valid and d_dir != direction:
                result.displacement_score = d_score * 0.3  # penalizar contra-direccional
            else:
                result.displacement_score = d_score * 0.6  # desplazamiento parcial

        # ── 11. Retest ATR-based (NUEVO) ─────────────────────
        if h1_bars and len(h1_bars) >= 10:
            rt_level, rt_type = find_retest_level(h1_bars, len(h1_bars) - 1, direction)
            if rt_level > 0:
                rt_score, rt_valid = retest_score(
                    h1_bars, len(h1_bars) - 1, rt_level, rt_type, h1_atr
                )
                result.retest_score = rt_score

        # ── 12. Judas Swing (NUEVO) ───────────────────────────
        if h1_bars and len(h1_bars) >= 20:
            j_score = get_judas_score(h1_bars, len(h1_bars) - 1, direction, h1_atr)
            result.judas_score = j_score
            judas_result = analyze_judas_swing(h1_bars, len(h1_bars) - 1, h1_atr)
            result.judas_detected = judas_result.detected

        # ── 13. Inducción (NUEVO) ─────────────────────────────
        if h1_bars and len(h1_bars) >= 30:
            i_score = get_induccion_score(h1_bars, len(h1_bars) - 1, direction, h1_atr)
            result.induccion_score = i_score
            ind_result = analyze_induccion(h1_bars, len(h1_bars) - 1, h1_atr)
            result.induccion_detected = ind_result.detected

        # ── 14. Sesión ────────────────────────────────────────
        base_session = self._session_score(mtf.session)
        session_mult = _learning_engine.get_session_multiplier(mtf.session)
        result.session_score = min(1.0, base_session * session_mult)

        # ── 15. Confluencia ───────────────────────────────────
        result.confluence_score = self._confluence_score(analyses, direction)

        # ── 16. Learning Score ────────────────────────────────
        pattern_candidate = self._identify_pattern(analyses, direction)
        if _learning_engine.should_avoid_pattern(pattern_candidate):
            result.reject_reason = f"patron {pattern_candidate} con bajo win rate"
            return result
        pattern_wr = _learning_engine.get_pattern_win_rate(pattern_candidate)
        symbol_mult = _learning_engine.get_symbol_multiplier(symbol)
        result.learning_score = min(1.0, (0.5 + pattern_wr * 0.5) * symbol_mult)

        # ── 17. Score Final Institucional V2 ──────────────────
        # Pesos redistribuidos: suma = 1.0
        result.final_score = (
            result.smc_score          * 0.18 +  # -0.02
            result.entry_tf_score     * 0.10 +  # -0.02
            result.sweep_score        * 0.10 +  # -0.02
            result.probability_score  * 0.10 +  # -0.02
            result.fvg_score          * 0.07 +  # -0.01
            result.manipulation_score * 0.07 +  # -0.01
            result.momentum_score     * 0.08 +  # -0.02
            result.confluence_score   * 0.07 +  # -0.01
            result.macro_score        * 0.04 +  # -0.01
            result.volatility_score   * 0.03 +
            result.session_score      * 0.01 +
            result.learning_score     * 0.01 +
            result.displacement_score * 0.05 +  # NUEVO
            result.retest_score       * 0.04 +  # NUEVO
            result.judas_score        * 0.03 +  # NUEVO
            result.induccion_score    * 0.02    # NUEVO
        ) * result.news_score

        # ── 18. Setup ─────────────────────────────────────────
        threshold = _learning_engine.get_recommended_threshold()
        if result.final_score >= threshold * 0.85:
            self._generate_setup(result, analyses, mtf)

        # ── 19. Validación ────────────────────────────────────
        min_rr = _learning_engine._insights.recommended_min_rr
        if result.final_score >= threshold and result.rr >= min_rr:
            result.valid   = True
            result.pattern = pattern_candidate
        else:
            if result.final_score < threshold:
                result.reject_reason = f"score {result.final_score:.2f} < {threshold:.2f}"
            elif result.rr < min_rr:
                result.reject_reason = f"RR {result.rr:.2f} < {min_rr:.1f}"

        logger.info(
            f"📊 {symbol} | {direction.upper()} | "
            f"Score:{result.final_score*100:.1f}% | "
            f"Disp:{result.displacement_valid} | "
            f"Judas:{result.judas_detected} | "
            f"Ind:{result.induccion_detected} | "
            f"Sweep:{result.sweep_detected} | "
            f"Wyckoff:{result.wyckoff_phase} | "
            f"Regime:{result.market_regime} | "
            f"Valid:{result.valid}"
        )
        return result

    # ── Helpers internos ──────────────────────────────────────

    def _momentum_score(self, mtf: MultiTF, direction: str) -> float:
        scores = []
        for tf_name in ["H4", "H1", "M15"]:
            data = mtf.get(tf_name)
            if data is None or len(data.bars) < 20:
                continue
            closes  = data.closes
            rsi_val = rsi(closes, 14)
            if direction == "buy":
                rsi_score = 1.0 if 40 <= rsi_val <= 65 else (0.6 if rsi_val < 40 else 0.3)
            else:
                rsi_score = 1.0 if 35 <= rsi_val <= 60 else (0.6 if rsi_val > 60 else 0.3)
            ema20 = ema(closes, 20)[-1]
            ema50 = ema(closes, 50)[-1] if len(closes) >= 50 else ema20
            current = closes[-1]
            if direction == "buy":
                ema_score = 1.0 if current > ema20 > ema50 else (0.5 if current > ema50 else 0.0)
            else:
                ema_score = 1.0 if current < ema20 < ema50 else (0.5 if current < ema50 else 0.0)
            dist     = abs(current - ema20) / (ema20 + 1e-9)
            mean_rev = max(0, 1 - dist * 50)
            scores.append(rsi_score * 0.4 + ema_score * 0.4 + mean_rev * 0.2)
        return float(np.mean(scores)) if scores else 0.5

    def _volatility_score(self, mtf: MultiTF, symbol: str) -> tuple:
        from config import SYMBOL_CONFIG
        h1 = mtf.H1
        if h1 is None:
            return 0.5, 0.0
        atr_val = atr(h1.bars, 14)
        cfg     = SYMBOL_CONFIG.get(symbol, {})
        atr_min = cfg.get("atr_min", 0)
        atr_max = cfg.get("atr_max", 1e9)
        if atr_val < atr_min:
            score = 0.3
        elif atr_val > atr_max:
            score = 0.2
        else:
            norm = (atr_val - atr_min) / (atr_max - atr_min + 1e-9)
            score = 1.0 if 0.3 <= norm <= 0.6 else (0.5 + norm if norm < 0.3 else max(0.4, 1.0 - (norm - 0.6) * 2))
        return round(score, 3), atr_val

    def _session_score(self, session: str) -> float:
        return {
            "ln_ny_overlap": 1.00,
            "london":        0.85,
            "new_york":      0.80,
            "tokyo":         0.50,
            "sydney":        0.30,
            "unknown":       0.20,
        }.get(session, 0.20)

    def _confluence_score(self, analyses: dict, direction: str) -> float:
        confirmations = total = 0
        for tf in ["H4", "H1", "M15", "M5"]:
            if tf not in analyses:
                continue
            a = analyses[tf]
            total += 1
            points = 0
            if a.bias == direction:                              points += 1
            if a.bos:                                            points += 1
            if any(not o.mitigated for o in a.order_blocks):    points += 1
            if any(not f.filled for f in a.fvgs):               points += 1
            if any(l.swept for l in a.liquidity):               points += 1
            confirmations += points / 5
        return confirmations / total if total > 0 else 0.0

    def _generate_setup(self, result, analyses: dict, mtf: MultiTF):
        direction = result.direction
        h1_atr    = result.atr_h1
        current   = mtf.current_price
        ob = None
        for tf in ["M15", "H1"]:
            a = analyses.get(tf)
            if a:
                valid_obs = [o for o in a.order_blocks if not o.mitigated]
                if valid_obs:
                    ob = valid_obs[0]
                    break
        if ob is None and mtf.H1:
            fvgs    = _fvg_engine.analyze(mtf.H1)
            premium = [f for f in fvgs if f.is_premium and
                       f.type == ("bullish" if direction == "buy" else "bearish")]
            if premium:
                class FVGasOB:
                    high = premium[0].high
                    low  = premium[0].low
                    mitigated = False
                ob = FVGasOB()
        if ob and h1_atr > 0:
            if direction == "buy":
                entry = ob.high
                sl    = ob.low - h1_atr * 0.1
                risk  = entry - sl
                if risk > 0:
                    tp = entry + risk * 3.0
                    result.entry = round(entry, 5)
                    result.stop_loss = round(sl, 5)
                    result.take_profit = round(tp, 5)
                    result.rr = round((tp - entry) / risk, 2)
            else:
                entry = ob.low
                sl    = ob.high + h1_atr * 0.1
                risk  = sl - entry
                if risk > 0:
                    tp = entry - risk * 3.0
                    result.entry = round(entry, 5)
                    result.stop_loss = round(sl, 5)
                    result.take_profit = round(tp, 5)
                    result.rr = round((entry - tp) / risk, 2)
        elif h1_atr > 0:
            if direction == "buy":
                result.entry = current
                result.stop_loss = round(current - h1_atr * 1.5, 5)
                result.take_profit = round(current + h1_atr * 4.0, 5)
                risk = current - result.stop_loss
                result.rr = round((result.take_profit - current) / risk, 2) if risk > 0 else 0
            else:
                result.entry = current
                result.stop_loss = round(current + h1_atr * 1.5, 5)
                result.take_profit = round(current - h1_atr * 4.0, 5)
                risk = result.stop_loss - current
                result.rr = round((current - result.take_profit) / risk, 2) if risk > 0 else 0

    def _identify_pattern(self, analyses: dict, direction: str) -> str:
        h4 = analyses.get("H4")
        h1 = analyses.get("H1")
        m15 = analyses.get("M15")
        has_bos   = any(a.bos   for a in [h4, h1, m15] if a)
        has_choch = any(a.choch for a in [h4, h1, m15] if a)
        has_ob    = any(any(not o.mitigated for o in a.order_blocks) for a in [h4, h1, m15] if a)
        has_fvg   = any(any(not f.filled for f in a.fvgs) for a in [h4, h1, m15] if a)
        has_liq   = any(any(l.swept for l in a.liquidity) for a in [h4, h1, m15] if a)
        if has_bos and has_choch and has_ob and has_liq: return "SNIPER_A+"
        if has_bos and has_ob and has_fvg:               return "BOS_OB_FVG"
        if has_choch and has_ob:                         return "CHOCH_OB_Reversal"
        if has_bos and has_liq:                          return "BOS_Liquidity_Sweep"
        if has_ob and has_fvg:                           return "OB_FVG_Confluence"
        if has_bos:                                      return "BOS_Continuation"
        return "Structure_Setup"


def get_learning_engine() -> LearningEngine:
    return _learning_engine
