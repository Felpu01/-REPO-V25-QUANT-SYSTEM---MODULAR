# strategy.py

def regime_logic(trend, volatility):

    if trend in ["TREND_UP", "TREND_DOWN"] and volatility == "HIGH":
        return "TRENDING"

    elif volatility == "LOW":
        return "RANGING"

    else:
        return "NEUTRAL"


def institutional_score(trend, volatility, sweep, regime):

    score = 0

    # TREND
    if trend in ["TREND_UP", "TREND_DOWN"]:
        score += 0.25

    # VOLATILITY
    if volatility == "HIGH":
        score += 0.25

    elif volatility == "MEDIUM":
        score += 0.15

    # LIQUIDITY SWEEP
    if sweep:
        score += 0.35

    # REGIME FILTER
    if regime == "TRENDING":
        score += 0.15

    elif regime == "RANGING":
        score -= 0.10

    # LIMITS
    if score > 1:
        score = 1.0

    if score < 0:
        score = 0

    return round(score, 2)
