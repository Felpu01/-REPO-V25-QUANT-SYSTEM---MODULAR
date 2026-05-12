# engine.py

import random


def detect_trend():

    trend = random.choice([
        "TREND_UP",
        "TREND_DOWN"
    ])

    return trend


def detect_volatility():

    volatility = random.choice([
        "LOW",
        "MEDIUM",
        "HIGH"
    ])

    return volatility


def detect_liquidity_sweep():

    probabilities = [True, False, False]

    return random.choice(probabilities)
