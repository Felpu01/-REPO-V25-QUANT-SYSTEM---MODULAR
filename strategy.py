# strategy.py

def score_engine(trend, sweep, volatility):

    score = 0.0

    # TREND
    if trend == "TREND_UP" or trend == "TREND_DOWN":
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


def regime_logic(trend, sweep, volatility):

    # STRONG TREND
    if (
        (trend == "TREND_UP" or trend == "TREND_DOWN")
        and volatility == "HIGH"
    ):
        return "EXPANSION"

    # SWEEP + VOL
    if sweep and volatility != "LOW":
        return "LIQUIDITY_EVENT"

    # RANGE
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
