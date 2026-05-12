import random


def detect_trend(price, prev_price):

    if price > prev_price:
        return "TREND_UP"

    elif price < prev_price:
        return "TREND_DOWN"

    return "RANGE"


def detect_sweep():

    return random.random() < 0.08


def detect_volatility(price, prev_price):

    move = abs(price - prev_price)

    if move > 2:
        return "HIGH"

    elif move > 1:
        return "MEDIUM"

    return "LOW"


def market_engine(price, prev_price):

    trend = detect_trend(price, prev_price)

    sweep = detect_sweep()

    volatility = detect_volatility(price, prev_price)

    return trend, sweep, volatility
