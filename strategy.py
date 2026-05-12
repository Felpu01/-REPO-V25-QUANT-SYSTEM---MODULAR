def score_engine(trend, sweep, volatility):

    score = 0

    # Liquidity sweep
    if sweep:
        score += 0.35

    # Trend strength
    if trend == "TREND_UP":
        score += 0.30

    if trend == "TREND_DOWN":
        score += 0.30

    # Volatility filter
    if volatility == "HIGH":
        score += 0.25

    elif volatility == "MEDIUM":
        score += 0.15

    # Range penalty
    if trend == "RANGE":
        score -= 0.20

    score = max(0, min(score, 1))

    return round(score, 3)


def regime_logic(trend, sweep, volatility):

    if volatility == "LOW":
        return "NO_TRADE"

    if sweep and trend == "TREND_UP":
        return "LONG_SETUP"

    if sweep and trend == "TREND_DOWN":
        return "SHORT_SETUP"

    if trend == "RANGE":
        return "MEAN_REVERSION"

    return "NO_TRADE"
