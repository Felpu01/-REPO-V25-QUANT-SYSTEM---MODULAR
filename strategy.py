def regime_logic(trend, sweep, volatility):

    if trend in ["UP", "DOWN"] and volatility == "HIGH":
        return "TRENDING"

    if sweep and volatility in ["MEDIUM", "HIGH"]:
        return "LIQUIDITY"

    if volatility == "LOW":
        return "RANGE"

    return "NEUTRAL"


def institutional_score(trend, volatility, sweep, regime):

    score = 0

    if trend in ["UP", "DOWN"]:
        score += 0.3

    if volatility == "HIGH":
        score += 0.3
    elif volatility == "MEDIUM":
        score += 0.15

    if sweep:
        score += 0.3

    if regime == "TRENDING":
        score += 0.15
    elif regime == "LIQUIDITY":
        score += 0.1
    elif regime == "RANGE":
        score -= 0.1

    return max(0, min(1, round(score, 2)))
