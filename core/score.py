"""
QUANT SCORING ENGINE
Score probabilístico multi-timeframe para setups sniper.
Combina SMC + momentum + volatilidad + sesión + confluencias.
"""

import logging
import numpy as np
from dataclasses import dataclass, field
from data.market_data import MultiTimeframeData, ema, atr, rsi
from smc.structure import MultiTFSMC, SMCAnalysis
from config import TF_WEIGHTS, SCORE_THRESHOLD, TRADE_SESSIONS

logger = logging.getLogger("QuantScore")


@dataclass
class QuantResult:
    symbol: str
    direction: str          # buy | sell | none
    final_score: float      # 0.0 - 1.0
    smc_score: float
    momentum_score: float
    volatility_score: float
    session_score: float
    confluence_score: float
    htf_bias: str
    entry_tf_score: float
    pattern: str
    entry: float
    stop_loss: float
    take_profit: float
    rr: float
    atr_h1: float
    valid: bool = False
    reject_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "score": round(self.final_score * 100, 1),
            "smc": round(self.smc_score * 100, 1),
            "momentum": round(self.momentum_score * 100, 1),
            "volatility": round(self.volatility_score * 100, 1),
            "session": round(self.session_score * 100, 1),
            "confluence": round(self.confluence_score * 100, 1),
            "htf_bias": self.htf_bias,
            "pattern": self.pattern,
            "entry": self.entry,
            "sl": self.stop_loss,
            "tp": self.take_profit,
            "rr": round(self.rr, 2),
            "valid": self.valid,
            "reject": self.reject_reason,
        }


class QuantScoringEngine:
    def __init__(self):
        self.smc = MultiTFSMC()
        logger.info("QuantScoringEngine iniciado")

    async def analyze(self, mtf: MultiTF) -> QuantResult:
        symbol = mtf.symbol
        result = QuantResult(
            symbol=symbol, direction="none", final_score=0.0,
            smc_score=0.0, momentum_score=0.0, volatility_score=0.0,
            session_score=0.0, confluence_score=0.0,
            htf_bias="neutral", entry_tf_score=0.0,
            pattern="none", entry=0.0, stop_loss=0.0,
            take_profit=0.0, rr=0.0, atr_h1=0.0,
        )

        if not mtf.is_complete():
            result.reject_reason = "datos insuficientes"
            return result

        # ─── 1. SMC Multi-TF ─────────────────────────────────
        analyses = self.smc.analyze_all(mtf)
        htf_bias = self.smc.get_htf_bias(analyses)
        result.htf_bias = htf_bias

        if htf_bias == "neutral":
            result.reject_reason = "bias HTF neutral"
            return result

        direction = "buy" if htf_bias == "bullish" else "sell"
        result.direction = direction

        # SMC score ponderado por TF
        smc_weighted = 0.0
        smc_weight   = 0.0
        for tf, w in TF_WEIGHTS.items():
            if tf in analyses:
                a = analyses[tf]
                align = 1.0 if a.bias == htf_bias else (0.5 if a.bias == "neutral" else 0.0)
                smc_weighted += (a.score / 100) * w * align
                smc_weight   += w
        result.smc_score = smc_weighted / smc_weight if smc_weight > 0 else 0.0

        # Entry TF score (H1 + M15 + M5)
        result.entry_tf_score = self.smc.get_entry_tf_score(analyses, htf_bias) / 100

        # ─── 2. Momentum Score ────────────────────────────────
        result.momentum_score = self._momentum_score(mtf, direction)

        # ─── 3. Volatility Score ──────────────────────────────
        result.volatility_score, result.atr_h1 = self._volatility_score(mtf, symbol)

        # ─── 4. Session Score ─────────────────────────────────
        result.session_score = self._session_score(mtf.session)

        # ─── 5. Confluence Score ──────────────────────────────
        result.confluence_score = self._confluence_score(analyses, direction)

        # ─── 6. Final Score ───────────────────────────────────
        result.final_score = (
            result.smc_score        * 0.35 +
            result.entry_tf_score   * 0.20 +
            result.momentum_score   * 0.15 +
            result.confluence_score * 0.15 +
            result.volatility_score * 0.10 +
            result.session_score    * 0.05
        )

        # ─── 7. Setup Generation ─────────────────────────────
        if result.final_score >= SCORE_THRESHOLD * 0.85:
            self._generate_setup(result, analyses, mtf)

        # ─── 8. Validación final ─────────────────────────────
        if result.final_score >= SCORE_THRESHOLD and result.rr >= 2.5:
            result.valid = True
            result.pattern = self._identify_pattern(analyses, direction)
        else:
            if result.final_score < SCORE_THRESHOLD:
                result.reject_reason = f"score {result.final_score:.2f} < {SCORE_THRESHOLD}"
            elif result.rr < 2.5:
                result.reject_reason = f"RR {result.rr:.2f} < 2.5"

        logger.info(
            f"📊 {symbol} | {direction.upper()} | "
            f"Score: {result.final_score*100:.1f}% | "
            f"SMC: {result.smc_score*100:.1f}% | "
            f"Valid: {result.valid}"
        )

        return result

    # ─── Momentum Score ───────────────────────────────────────

    def _momentum_score(self, mtf: MultiTF, direction: str) -> float:
        scores = []

        for tf_name in ["H4", "H1", "M15"]:
            data = mtf.get(tf_name)
            if data is None or len(data.bars) < 20:
                continue

            closes = data.closes

            # RSI
            rsi_val = rsi(closes, 14)
            if direction == "buy":
                # RSI entre 40-65 = momentum alcista sano
                rsi_score = 1.0 if 40 <= rsi_val <= 65 else (0.6 if rsi_val < 40 else 0.3)
            else:
                rsi_score = 1.0 if 35 <= rsi_val <= 60 else (0.6 if rsi_val > 60 else 0.3)

            # EMA alignment
            ema20 = ema(closes, 20)[-1]
            ema50 = ema(closes, 50)[-1] if len(closes) >= 50 else ema20
            current = closes[-1]

            if direction == "buy":
                ema_score = 1.0 if current > ema20 > ema50 else (0.5 if current > ema50 else 0.0)
            else:
                ema_score = 1.0 if current < ema20 < ema50 else (0.5 if current < ema50 else 0.0)

            # Precio vs EMA20
            dist = abs(current - ema20) / (ema20 + 1e-9)
            mean_reversion = max(0, 1 - dist * 50)  # penaliza si está muy lejos

            tf_score = (rsi_score * 0.4 + ema_score * 0.4 + mean_reversion * 0.2)
            scores.append(tf_score)

        return float(np.mean(scores)) if scores else 0.5

    # ─── Volatility Score ─────────────────────────────────────

    def _volatility_score(self, mtf: MultiTF, symbol: str) -> tuple[float, float]:
        """Volatilidad ideal: ni demasiado alta ni demasiado baja."""
        from config import SYMBOL_CONFIG
        h1 = mtf.H1
        if h1 is None:
            return 0.5, 0.0

        atr_val = atr(h1.bars, 14)
        cfg = SYMBOL_CONFIG.get(symbol, {})
        atr_min = cfg.get("atr_min", 0)
        atr_max = cfg.get("atr_max", 1e9)

        if atr_val < atr_min:
            score = 0.3  # muy baja volatilidad, spread mata el trade
        elif atr_val > atr_max:
            score = 0.2  # volatilidad extrema, riesgo incontrolable
        else:
            # Normalizar entre min y max
            norm = (atr_val - atr_min) / (atr_max - atr_min)
            # Óptimo en el 30-60% del rango
            if 0.3 <= norm <= 0.6:
                score = 1.0
            elif norm < 0.3:
                score = 0.5 + norm
            else:
                score = max(0.4, 1.0 - (norm - 0.6) * 2)

        return round(score, 3), atr_val

    # ─── Session Score ────────────────────────────────────────

    def _session_score(self, session: str) -> float:
        scores = {
            "ln_ny_overlap": 1.00,  # mejor liquidez del día
            "london":        0.85,
            "new_york":      0.80,
            "tokyo":         0.50,
            "sydney":        0.30,
            "unknown":       0.20,
        }
        return scores.get(session, 0.20)

    # ─── Confluence Score ─────────────────────────────────────

    def _confluence_score(
        self, analyses: dict[str, SMCAnalysis], direction: str
    ) -> float:
        """Cuántos TF confirman la dirección con elementos SMC sólidos."""
        confirmations = 0
        total = 0

        for tf in ["H4", "H1", "M15", "M5"]:
            if tf not in analyses:
                continue
            a = analyses[tf]
            total += 1

            points = 0
            if a.bias == direction:                    points += 1
            if a.bos:                                  points += 1
            if any(not o.mitigated for o in a.order_blocks): points += 1
            if any(not f.filled for f in a.fvgs):     points += 1
            if any(l.swept for l in a.liquidity):     points += 1

            confirmations += points / 5  # normalizado 0-1

        return confirmations / total if total > 0 else 0.0

    # ─── Setup Generation ─────────────────────────────────────

    def _generate_setup(
        self, result: QuantResult,
        analyses: dict[str, SMCAnalysis],
        mtf: MultiTF
    ):
        """Genera entry, SL y TP institucional desde M15."""
        direction = result.direction
        m15 = analyses.get("M15") or analyses.get("H1")
        if m15 is None:
            return

        h1_atr = result.atr_h1
        current = mtf.current_price

        # Busca OB válido en M15 o H1
        ob = None
        for tf in ["M15", "H1"]:
            a = analyses.get(tf)
            if a:
                valid_obs = [o for o in a.order_blocks if not o.mitigated]
                if valid_obs:
                    ob = valid_obs[0]
                    break

        if ob and h1_atr > 0:
            if direction == "buy":
                entry = ob.high
                sl    = ob.low - h1_atr * 0.1
                risk  = entry - sl
                if risk > 0:
                    tp   = entry + risk * 3.0
                    rr   = (tp - entry) / risk
                    result.entry      = round(entry, 5)
                    result.stop_loss  = round(sl, 5)
                    result.take_profit= round(tp, 5)
                    result.rr         = round(rr, 2)

            elif direction == "sell":
                entry = ob.low
                sl    = ob.high + h1_atr * 0.1
                risk  = sl - entry
                if risk > 0:
                    tp   = entry - risk * 3.0
                    rr   = (entry - tp) / risk
                    result.entry      = round(entry, 5)
                    result.stop_loss  = round(sl, 5)
                    result.take_profit= round(tp, 5)
                    result.rr         = round(rr, 2)

        # Si no hay OB, usa ATR
        elif h1_atr > 0:
            if direction == "buy":
                result.entry       = current
                result.stop_loss   = round(current - h1_atr * 1.5, 5)
                result.take_profit = round(current + h1_atr * 4.0, 5)
                result.rr          = round((result.take_profit - current) / (current - result.stop_loss), 2)
            else:
                result.entry       = current
                result.stop_loss   = round(current + h1_atr * 1.5, 5)
                result.take_profit = round(current - h1_atr * 4.0, 5)
                result.rr          = round((current - result.take_profit) / (result.stop_loss - current), 2)

    def _identify_pattern(
        self, analyses: dict[str, SMCAnalysis], direction: str
    ) -> str:
        h4 = analyses.get("H4")
        h1 = analyses.get("H1")
        m15= analyses.get("M15")

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
