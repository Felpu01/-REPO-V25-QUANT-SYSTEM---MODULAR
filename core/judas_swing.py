"""
JUDAS SWING DETECTOR — Movimiento falso de sesión
Detecta el sweep de liquidez al inicio de London/NY Open
antes de la reversión institucional real.

Concepto SMC:
  1. London Open (07:00-09:00 UTC) o NY Open (12:00-14:00 UTC)
  2. Precio hace un movimiento inicial en una dirección
  3. Barre el swing high/low de Asia/London respectivamente
  4. Cierra de vuelta dentro del rango previo (rechazo)
  5. El movimiento REAL es en dirección OPUESTA al Judas Swing

Cómo se usa en score.py:
  - Si se detecta Judas Swing BUY → dirección válida = SELL y viceversa
  - Bonus de score cuando la dirección del trade coincide con el anti-Judas
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger("JudasSwing")

# Horas UTC de apertura de sesiones
LONDON_OPEN_HOURS  = [7, 8]      # 07:00-09:00 UTC
NY_OPEN_HOURS      = [12, 13]    # 12:00-14:00 UTC
ASIA_CLOSE_HOURS   = [6, 7]      # cierre Asia / apertura London


@dataclass
class JudasSwingResult:
    detected: bool = False
    judas_direction: str = "none"   # dirección del movimiento FALSO
    real_direction: str  = "none"   # dirección del movimiento REAL (opuesta)
    session: str         = "none"
    sweep_level: float   = 0.0
    rejection_strength: float = 0.0
    score: float         = 0.0


def analyze_judas_swing(bars: list, i: int, atr_val: float = 0.0) -> JudasSwingResult:
    """
    Detecta Judas Swing en la barra i.

    Args:
        bars:    lista de Bar con .time .open .high .low .close
        i:       índice de la barra actual
        atr_val: ATR pre-calculado (se calcula si 0)

    Returns:
        JudasSwingResult con detected=True si se detectó
    """
    result = JudasSwingResult()

    if i < 20 or not bars:
        return result

    bar = bars[i]

    # Obtener hora UTC de la barra actual
    try:
        hour_utc = int(bar.time[11:13])
    except Exception:
        return result

    # Determinar sesión
    if hour_utc in LONDON_OPEN_HOURS:
        session = "london_open"
        lookback_session = 12   # barras de sesión Asia a revisar
    elif hour_utc in NY_OPEN_HOURS:
        session = "ny_open"
        lookback_session = 10   # barras de sesión London a revisar
    else:
        return result  # no es apertura de sesión

    # ATR interno
    if atr_val <= 0:
        atr_window = bars[max(0, i - 14):i]
        if len(atr_window) < 2:
            return result
        trs = []
        for k in range(1, len(atr_window)):
            tr = max(
                atr_window[k].high - atr_window[k].low,
                abs(atr_window[k].high - atr_window[k - 1].close),
                abs(atr_window[k].low  - atr_window[k - 1].close),
            )
            trs.append(tr)
        atr_val = sum(trs) / len(trs) if trs else 0.0

    if atr_val <= 0:
        return result

    # Rango de la sesión previa (Asia para London, London para NY)
    prev_session = bars[max(0, i - lookback_session):i]
    if not prev_session:
        return result

    prev_high = max(b.high for b in prev_session)
    prev_low  = min(b.low  for b in prev_session)
    prev_mid  = (prev_high + prev_low) / 2

    # Verificar en barras recientes (últimas 3) si hubo sweep de nivel
    recent_bars = bars[max(0, i - 3):i + 1]

    # ── Judas Swing ALCISTA (falso movimiento up → real down) ──
    for rb in recent_bars:
        if rb.high > prev_high + atr_val * 0.1:  # superó el high de sesión previa
            # ¿Cerró de vuelta DENTRO del rango previo? (rechazo)
            if rb.close < prev_high:
                sweep_excess = rb.high - prev_high
                wick_down    = rb.high - rb.close
                wick_ratio   = wick_down / (rb.high - rb.low + 1e-9)
                rejection_pct= wick_down / atr_val

                if wick_ratio >= 0.5 and rejection_pct >= 0.3:
                    # Judas Swing confirmado — el REAL movimiento es DOWN
                    strength = min(1.0, wick_ratio * 0.5 + rejection_pct * 0.3 + 0.2)
                    result.detected          = True
                    result.judas_direction   = "buy"
                    result.real_direction    = "sell"
                    result.session           = session
                    result.sweep_level       = prev_high
                    result.rejection_strength = rejection_pct
                    result.score             = round(strength, 3)
                    logger.debug(
                        f"Judas Swing BULL detectado | {session} | "
                        f"sweep: {prev_high:.5f} | score: {strength:.2f}"
                    )
                    return result

    # ── Judas Swing BAJISTA (falso movimiento down → real up) ──
    for rb in recent_bars:
        if rb.low < prev_low - atr_val * 0.1:  # perforó el low de sesión previa
            if rb.close > prev_low:
                sweep_excess = prev_low - rb.low
                wick_up      = rb.close - rb.low
                wick_ratio   = wick_up / (rb.high - rb.low + 1e-9)
                rejection_pct= wick_up / atr_val

                if wick_ratio >= 0.5 and rejection_pct >= 0.3:
                    strength = min(1.0, wick_ratio * 0.5 + rejection_pct * 0.3 + 0.2)
                    result.detected          = True
                    result.judas_direction   = "sell"
                    result.real_direction    = "buy"
                    result.session           = session
                    result.sweep_level       = prev_low
                    result.rejection_strength = rejection_pct
                    result.score             = round(strength, 3)
                    logger.debug(
                        f"Judas Swing BEAR detectado | {session} | "
                        f"sweep: {prev_low:.5f} | score: {strength:.2f}"
                    )
                    return result

    return result


def get_judas_score(bars: list, i: int, trade_direction: str, atr_val: float = 0.0) -> float:
    """
    Retorna score de Judas Swing relativo a la dirección del trade.

    - Si el trade va en la dirección REAL (opuesta al Judas) → score alto (confirmación)
    - Si el trade va en la dirección del JUDAS → score negativo (penalización)
    - Si no hay Judas Swing detectado → score neutro (0.5)
    """
    result = analyze_judas_swing(bars, i, atr_val)

    if not result.detected:
        return 0.5  # neutro

    if trade_direction == result.real_direction:
        # Operando con el movimiento real — bonus
        return min(1.0, 0.6 + result.score * 0.4)

    elif trade_direction == result.judas_direction:
        # Operando contra el movimiento real — penalización fuerte
        return max(0.0, 0.3 - result.score * 0.3)

    return 0.5
