import random


# =========================
# TREND ENGINE
# =========================

def detect_trend(prices):

    if len(prices) < 10:
        return "RANGE"

    sma_fast = sum(prices[-5:]) / 5
    sma_slow = sum(prices[-10:]) / 10

    if sma_fast > sma_slow:
        return "TREND_UP"

    elif sma_fast < sma_slow:
        return "TREND_DOWN"

    return "RANGE"


# =========================
# VOLATILITY ENGINE
# =========================

def detect_volatility():

    vol = random.uniform(0, 1)

    if vol > 0.7:
        return "HIGH"

    elif vol > 0.4:
        return "MEDIUM"

    return "LOW"


# =========================
# LIQUIDITY SWEEP
# =========================

def detect_liquidity_sweep():

    return random.random() > 0.8


# =========================
# V24.1 SWING ENGINE
# =========================

def detect_swings(prices):

    if len(prices) < 5:
        return None, None

    swing_high = None
    swing_low = None

    i = len(prices) - 3

    # SWING HIGH
    if (
        prices[i] > prices[i - 1]
        and prices[i] > prices[i - 2]
        and prices[i] > prices[i + 1]
        and prices[i] > prices[i + 2]
    ):
        swing_high = prices[i]

    # SWING LOW
    if (
        prices[i] < prices[i - 1]
        and prices[i] < prices[i - 2]
        and prices[i] < prices[i + 1]
        and prices[i] < prices[i + 2]
    ):
        swing_low = prices[i]

    return swing_high, swing_low


# =========================
# REGIME LOGIC
# =========================

def regime_logic(trend, volatility):

    if trend == "RANGE":
        return "NO_TRADE"

    if volatility == "LOW":
        return "LOW_VOLATILITY"

    return "ACTIVE"


# =========================
# INSTITUTIONAL SCORE
# =========================

def institutional_score(trend, volatility, sweep):

    score = 0

    # TREND
    if trend != "RANGE":
        score += 0.3

    # VOLATILITY
    if volatility == "HIGH":
        score += 0.25

    elif volatility == "MEDIUM":
        score += 0.15

    # SWEEP
    if sweep:
        score += 0.35

    # RANDOM FACTOR
    score += random.uniform(0, 0.1)

    return round(score, 3)


# =========================
# SIGNAL ENGINE
# =========================

def generate_signal(score, trend, sweep):

    if score >= 0.85:

        if trend == "TREND_UP" and sweep:
            return "BUY"

        elif trend == "TREND_DOWN" and sweep:
            return "SELL"

    return "WAIT"
