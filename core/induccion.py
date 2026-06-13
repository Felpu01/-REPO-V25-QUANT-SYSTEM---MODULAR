"""
INDUCCIÓN — Acumulación de liquidez antes del movimiento real
El mercado crea equal highs/lows (EQH/EQL) para inducir posiciones retail
en la dirección incorrecta antes de barrer esos stops y moverse en la real.

Señal institucional clásica:
  1. Equal Highs (EQH): dos o más toques del mismo nivel de resistencia
     → Retail se pone LONG esperando rotura
     → Institucional barre esos longs y cae
  2. Equal Lows (EQL): dos o más toques del mismo soporte
     → Retail se pone SHORT esperando ruptura
     → Institucional barre esos shorts y sube

Score de inducción: mide qué tan limpia es la trampa
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger("Induccion")


@dataclass
class InduccionResult:
    detected: bool     = False
    type: str          = "none"          # "EQH" | "EQL"
    level: float       = 0.0
    touches: int       = 0
    swept: bool        = False
    real_direction: str = "none"         # "buy" | "sell"
    trap_strength: float = 0.0           # 0-1
    score: float        = 0.0


def analyze_induccion(
    bars: list,
    i: int,
    atr_val: float = 0.0,
    lookback: int = 30,
    tolerance_factor: float = 0.15,
) -> InduccionResult:
    """
    Detecta patrón de inducción en ventana de lookback barras.

    Args:
        bars:             lista de Bar
        i:                índice actual
        atr_val:          ATR pre-calculado
        lookback:         ventana de análisis
        tolerance_factor: tolerancia = ATR * factor para agrupar niveles

    Returns:
        InduccionResult
    """
    result = InduccionResult()

    if i < lookback or not bars:
        return result

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

    tolerance = atr_val * tolerance_factor
    window    = bars[max(0, i - lookback):i + 1]
    current   = bars[i]

    # ── Detectar Equal Highs (EQH) ───────────────────────────
    highs = [(j, bars[max(0, i - lookback) + j].high) for j in range(len(window))]

    # Agrupar highs cercanos
    eqh_groups: list = []
    for _, h in highs:
        placed = False
        for grp in eqh_groups:
            if abs(h - grp["level"]) <= tolerance:
                grp["touches"] += 1
                grp["level"]    = (grp["level"] * (grp["touches"] - 1) + h) / grp["touches"]
                placed = True
                break
        if not placed:
            eqh_groups.append({"level": h, "touches": 1})

    # EQH válido: 2+ toques
    valid_eqh = [g for g in eqh_groups if g["touches"] >= 2]

    # ── Detectar Equal Lows (EQL) ─────────────────────────────
    lows = [(j, bars[max(0, i - lookback) + j].low) for j in range(len(window))]

    eql_groups: list = []
    for _, l in lows:
        placed = False
        for grp in eql_groups:
            if abs(l - grp["level"]) <= tolerance:
                grp["touches"] += 1
                grp["level"]    = (grp["level"] * (grp["touches"] - 1) + l) / grp["touches"]
                placed = True
                break
        if not placed:
            eql_groups.append({"level": l, "touches": 1})

    valid_eql = [g for g in eql_groups if g["touches"] >= 2]

    # ── EQH barrido → señal SELL ──────────────────────────────
    best_result = InduccionResult()
    best_score  = 0.0

    for grp in valid_eqh:
        level = grp["level"]
        # ¿El precio actual barió el EQH y rechazó?
        if current.high >= level - tolerance:  # llegó al nivel
            swept  = current.high > level       # lo perforó
            reject = current.close < level      # cerró por debajo (rechazo)

            if reject:
                touches_score  = min(1.0, grp["touches"] / 4.0)
                sweep_bonus    = 0.20 if swept else 0.0
                wick_ratio     = (current.high - current.close) / (atr_val + 1e-9)
                wick_score     = min(0.30, wick_ratio * 0.15)
                score = touches_score * 0.50 + sweep_bonus + wick_score

                if score > best_score:
                    best_score = score
                    best_result = InduccionResult(
                        detected       = True,
                        type           = "EQH",
                        level          = round(level, 5),
                        touches        = grp["touches"],
                        swept          = swept,
                        real_direction = "sell",
                        trap_strength  = touches_score,
                        score          = round(score, 3),
                    )

    # ── EQL barrido → señal BUY ───────────────────────────────
    for grp in valid_eql:
        level = grp["level"]
        if current.low <= level + tolerance:  # llegó al nivel
            swept  = current.low < level
            reject = current.close > level

            if reject:
                touches_score  = min(1.0, grp["touches"] / 4.0)
                sweep_bonus    = 0.20 if swept else 0.0
                wick_ratio     = (current.close - current.low) / (atr_val + 1e-9)
                wick_score     = min(0.30, wick_ratio * 0.15)
                score = touches_score * 0.50 + sweep_bonus + wick_score

                if score > best_score:
                    best_score = score
                    best_result = InduccionResult(
                        detected       = True,
                        type           = "EQL",
                        level          = round(level, 5),
                        touches        = grp["touches"],
                        swept          = swept,
                        real_direction = "buy",
                        trap_strength  = touches_score,
                        score          = round(score, 3),
                    )

    if best_result.detected:
        logger.debug(
            f"Inducción {best_result.type} | nivel:{best_result.level} | "
            f"toques:{best_result.touches} | real:{best_result.real_direction} | "
            f"score:{best_result.score}"
        )

    return best_result


def get_induccion_score(bars: list, i: int, trade_direction: str, atr_val: float = 0.0) -> float:
    """
    Score de inducción relativo a la dirección del trade.

    - Inducción alineada con trade → bonus (estamos con la trampa correcta)
    - Inducción contra el trade → penalización
    - Sin inducción → neutro (0.5)
    """
    result = analyze_induccion(bars, i, atr_val)

    if not result.detected:
        return 0.5

    if trade_direction == result.real_direction:
        return min(1.0, 0.55 + result.score * 0.45)

    elif trade_direction != result.real_direction:
        return max(0.0, 0.35 - result.score * 0.25)

    return 0.5
