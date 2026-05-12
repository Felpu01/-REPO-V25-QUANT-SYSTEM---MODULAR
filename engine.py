# engine.py

def detect_trend(prices, i):

    if i < 5:
        return "NEUTRAL"

    recent = prices[i-5:i]

    if recent[-1] > recent[0]:
        return "UP"
    elif recent[-1] < recent[0]:
        return "DOWN"
    else:
        return "NEUTRAL"


def detect_volatility(prices, i):

    if i < 10:
        return "LOW"

    recent = prices[i-10:i]

    amplitude = max(recent) - min(recent)

    if amplitude > 80:
        return "HIGH"
    elif amplitude > 30:
        return "MEDIUM"
    else:
        return "LOW"


def detect_liquidity_sweep(price, prev_high, prev_low):

    return price > prev_high or price < prev_low
