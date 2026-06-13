"""
RETEST ENGINE — Validación de retests institucionales
Detecta retests a niveles clave (OB, FVG, BOS) con tolerancia
basada en ATR en vez de pips fijos.

V2: tolerancia = ATR * factor (default 0.3)
    Devuelve score 0-1 + flag booleano.
"""

import logging

logger = logging.getLogger("Retest")


def retest_score(
    bars: list,
    i: int,
    level: float,
    level_type: str,   # "BULLISH" | "BEARISH"
    atr_val: float = 0.0,
    atr_factor: float = 0.35,
) -> tuple:
    """
    Puntúa un retest a un nivel clave.

    Args:
        bars:       lista de Bar
        i:          índice de la barra actual
        level:      precio del nivel a retestear
        level_type: "BULLISH" (soporte) o "BEARISH" (resistencia)
        atr_val:    ATR pre-calculado
        atr_factor: tolerancia = ATR * factor

    Returns:
        (score: float 0-1, is_retest: bool)
    """
    if i < 3 or not bars:
        return 0.0, False

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

    if atr_val <= 0 or level <= 0:
        return 0.0, False

    tolerance = atr_val * atr_factor
    bar = bars[i]
    rng = bar.high - bar.low if bar.high > bar.low else atr_val

    if level_type == "BULLISH":
        # Retest de soporte: precio baja hasta el nivel y rebota
        distance = bar.low - level  # positivo si está por encima
        in_zone  = abs(distance) <= tolerance or (distance < 0 and abs(distance) <= tolerance * 0.5)

        if not in_zone:
            return 0.0, False

        # Calidad del rechazo
        rejection    = bar.close > level                             # cerró por encima
        wick_size    = bar.close - bar.low                           # wick inferior
        wick_ratio   = wick_size / rng                               # % del rango
        proximity    = 1.0 - (abs(distance) / (tolerance + 1e-9))   # más cerca = mejor
        body_above   = bar.close > bar.open                          # vela alcista

        score = (
            proximity              * 0.35 +
            (wick_ratio            * 0.30) +
            (0.25 if rejection     else 0.0) +
            (0.10 if body_above    else 0.0)
        )
        return round(min(1.0, score), 3), True

    elif level_type == "BEARISH":
        # Retest de resistencia: precio sube hasta el nivel y cae
        distance = level - bar.high  # positivo si está por debajo
        in_zone  = abs(distance) <= tolerance or (distance < 0 and abs(distance) <= tolerance * 0.5)

        if not in_zone:
            return 0.0, False

        rejection    = bar.close < level
        wick_size    = bar.high - bar.close
        wick_ratio   = wick_size / rng
        proximity    = 1.0 - (abs(distance) / (tolerance + 1e-9))
        body_below   = bar.close < bar.open

        score = (
            proximity              * 0.35 +
            (wick_ratio            * 0.30) +
            (0.25 if rejection     else 0.0) +
            (0.10 if body_below    else 0.0)
        )
        return round(min(1.0, score), 3), True

    return 0.0, False


def find_retest_level(bars: list, i: int, direction: str, lookback: int = 30) -> tuple:
    """
    Encuentra el nivel más relevante para retestear en ventana de lookback.
    Retorna (nivel: float, tipo: str) o (0.0, "")
    """
    if i < lookback or not bars:
        return 0.0, ""

    window = bars[max(0, i - lookback):i]

    if direction == "buy":
        # Buscar swing high reciente que se haya roto (ahora soporte)
        swing_high = max(b.high for b in window)
        # El nivel de soporte más reciente
        recent_lows = sorted(
            [b.low for b in window[-10:]],
        )
        if recent_lows:
            level = recent_lows[0]  # low más bajo reciente
            return level, "BULLISH"

    elif direction == "sell":
        # Buscar swing low reciente que se haya roto (ahora resistencia)
        recent_highs = sorted(
            [b.high for b in window[-10:]],
            reverse=True
        )
        if recent_highs:
            level = recent_highs[0]  # high más alto reciente
            return level, "BEARISH"

    return 0.0, ""
