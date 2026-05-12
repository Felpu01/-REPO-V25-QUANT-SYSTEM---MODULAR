# strategy.py

def regime_logic(trend, sweep, volatility):

    # TRENDING MARKET
    if trend in ["TREND_UP", "TREND_DOWN"] and volatility == "HIGH":
        return "TRENDING"

    # LIQUIDITY ENVIRONMENT
    elif sweep and volatility in ["MEDIUM", "HIGH"]:
        return "LIQUIDITY_EVENT"

    # LOW VOLATILITY RANGE
    elif volatility == "LOW":
        return "RANGING"

    # DEFAULT
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

    # REGIME BONUS
    if regime == "TRENDING":
        score += 0.15

    elif regime == "LIQUIDITY_EVENT":
        score += 0.10

    elif regime == "RANGING":
        score -= 0.10

    # LIMITS
    if score > 1:
        score = 1.0

    if score < 0:
        score = 0

    return round(score, 2)
