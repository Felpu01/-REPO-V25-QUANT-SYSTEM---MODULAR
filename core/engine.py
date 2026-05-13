def trend(prices, i):

    if i < 5:
        return "neutral"

    if prices[i] > prices[i - 5]:
        return "bullish"
    else:
        return "bearish"


def volatility(prices, i):

    window = prices[max(0, i - 10):i]

    if len(window) < 2:
        return 0.0  # baja volatilidad numérica

    amp = max(window) - min(window)

    # normalización simple (para score)
    if amp > 60:
        return 0.9
    elif amp > 25:
        return 0.5

    return 0.2
