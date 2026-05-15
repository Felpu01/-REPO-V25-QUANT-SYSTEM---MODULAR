def calculate_score(
    price,
    trend,
    bos_up,
    bos_down,
    choch_up,
    choch_down,
    volatility
):

    score = 0.0

    # TREND
    if trend == "bullish":
        score += 0.30
    elif trend == "bearish":
        score += 0.30

    # STRUCTURE
    if bos_up or bos_down:
        score += 0.25

    if choch_up or choch_down:
        score += 0.20

    # VOLATILITY (más peso)
    if volatility > 0.4:
        score += 0.20
    if volatility > 0.7:
        score += 0.10

    # BASE BOOST (IMPORTANTE FIX)
    score += 0.10

    return round(min(score, 1.0), 2)
