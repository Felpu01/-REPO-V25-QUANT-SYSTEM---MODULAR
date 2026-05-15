def entry_quality(
    displacement_strength,
    sweep_up,
    sweep_down,
    bos_up,
    bos_down,
    choch_up,
    choch_down,
    volatility,
    bias,
    score
):

    quality = 0.0

    # =====================
    # STRUCTURE QUALITY
    # =====================
    if bos_up or bos_down:
        quality += 0.2

    if choch_up or choch_down:
        quality += 0.2

    # =====================
    # LIQUIDITY QUALITY
    # =====================
    if sweep_up or sweep_down:
        quality += 0.25

    # =====================
    # DISPLACEMENT QUALITY
    # =====================
    if displacement_strength > 1.5:
        quality += 0.25
    elif displacement_strength > 1.0:
        quality += 0.15

    # =====================
    # SCORE CONFIRMATION
    # =====================
    if score > 0.80:
        quality += 0.15
    elif score > 0.70:
        quality += 0.10

    # =====================
    # BIAS CONFIRMATION
    # =====================
    if bias != "NEUTRAL":
        quality += 0.10

    # =====================
    # VOLATILITY FILTER (EVITA RUIDO)
    # =====================
    if volatility < 0.35:
        quality -= 0.15

    if volatility > 1.2:
        quality -= 0.10

    return max(0.0, min(1.0, quality))
