# strategy.py

def institutional_score(trend, sweep, volatility):

    score = 0.0

    # TREND
    if trend in ["TREND_UP", "TREND_DOWN"]:
        score += 0.35

    # SWEEP
    if sweep:
        score += 0.35

    # VOLATILITY
    if volatility == "HIGH":
        score += 0.30

    elif volatility == "MEDIUM":
        score += 0.15

    return round(score, 2)


def score_engine(trend, sweep, volatility):

    return institutional_score(
        trend,
        sweep,
        volatility
    )


def regime_logic(trend, sweep, volatility):

    # EXPANSION
    if (
        trend in ["TREND_UP", "TREND_DOWN"]
        and volatility == "HIGH"
    ):
        return "EXPANSION"

    # LIQUIDITY EVENT
    if sweep and volatility != "LOW":
        return "LIQUIDITY_EVENT"

    # CONSOLIDATION
    if volatility == "LOW":
        return "CONSOLIDATION"

    return "NEUTRAL"


def signal_engine(score, trend, sweep):

    if score >= 0.85:

        if trend == "TREND_UP":
            return "BUY"

        if trend == "TREND_DOWN":
            return "SELL"

    return "WAIT"
