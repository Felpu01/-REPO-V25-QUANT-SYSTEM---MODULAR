"""
DISPLACEMENT ENGINE — Desplazamiento institucional
Detecta movimientos impulsivos que confirman intención institucional.
Un desplazamiento válido: vela > 1.5x ATR, cuerpo >= 60% del rango,
cierre en el 70% superior/inferior del rango.

V2: función principal devuelve score 0-1 y flag booleano.
    Compatible con score.py y simulator.py.
"""

import logging

logger = logging.getLogger("Displacement")


def displacement_score(bars: list, i: int, atr_val: float = 0.0) -> tuple:
    """
    Calcula el score de desplazamiento institucional para la barra en índice i.

    Args:
        bars:    lista de Bar con .open .high .low .close .volume
        i:       índice de la barra a evaluar
        atr_val: ATR pre-calculado (si 0, se calcula internamente)

    Returns:
        (score: float 0-1, is_valid: bool)
    """
    if i < 10 or not bars:
        return 0.0, False

    bar = bars[i]

    # ATR interno si no se provee
    if atr_val <= 0:
        window = bars[max(0, i - 14):i]
        if len(window) < 2:
            return 0.0, False
        trs = []
        for k in range(1, len(window)):
            tr = max(
                window[k].high - window[k].low,
                abs(window[k].high - window[k - 1].close),
                abs(window[k].low  - window[k - 1].close),
            )
            trs.append(tr)
        atr_val = sum(trs) / len(trs) if trs else 0.0

    if atr_val <= 0:
        return 0.0, False

    rng  = bar.high - bar.low
    body = abs(bar.close - bar.open)

    if rng <= 0:
        return 0.0, False

    # Métricas de desplazamiento
    range_ratio  = rng / atr_val
    body_ratio   = body / rng

    # Posición del cierre dentro del rango (0=inferior, 1=superior)
    close_location = (bar.close - bar.low) / rng

    # Dirección del desplazamiento
    is_bullish = bar.close > bar.open
    if not is_bullish:
        close_location = 1.0 - close_location  # invertir para bajista

    # Score de volumen (si disponible)
    vol_score = 0.5
    if i >= 5:
        vol_window = bars[max(0, i - 5):i]
        avg_vol = sum(b.volume for b in vol_window) / len(vol_window) if vol_window else 0
        if avg_vol > 0:
            vol_score = min(1.0, (bar.volume / avg_vol) / 2.0)

    # Validez: range >= 1.5x ATR + body >= 60% + cierre >= 70% del rango
    is_valid = (
        range_ratio  >= 1.5 and
        body_ratio   >= 0.60 and
        close_location >= 0.70
    )

    if is_valid:
        # Score graduado por intensidad del desplazamiento
        range_score = min(1.0, (range_ratio - 1.5) / 2.0 + 0.5)
        score = (
            range_score     * 0.40 +
            body_ratio      * 0.30 +
            close_location  * 0.20 +
            vol_score       * 0.10
        )
        return round(min(1.0, score), 3), True

    # Desplazamiento parcial (>= 1.0x ATR pero no completo)
    if range_ratio >= 1.0 and body_ratio >= 0.45:
        partial = min(0.45, range_ratio * 0.15 + body_ratio * 0.10)
        return round(partial, 3), False

    return 0.0, False


def get_displacement_direction(bars: list, i: int) -> str:
    """Retorna 'buy', 'sell' o 'neutral' según la dirección del desplazamiento."""
    if i < 0 or i >= len(bars):
        return "neutral"
    bar = bars[i]
    if bar.close > bar.open:
        return "buy"
    elif bar.close < bar.open:
        return "sell"
    return "neutral"
