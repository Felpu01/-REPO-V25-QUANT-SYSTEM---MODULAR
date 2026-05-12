def trend(prices, i):

    if i < 5:
        return "NEUTRAL"

    return "UP" if prices[i] > prices[i-5] else "DOWN"


def volatility(prices, i):

    window = prices[max(0, i-10):i]

    if len(window) < 2:
        return "LOW"

    amp = max(window) - min(window)

    if amp > 60:
        return "HIGH"
    elif amp > 25:
        return "MEDIUM"

    return "LOW"
