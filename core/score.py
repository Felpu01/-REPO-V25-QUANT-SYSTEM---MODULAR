import random

def calculate_score(price, trend, bos_up, bos_down, choch_up, choch_down, volatility):

    score = 0.0

    # =========================
    # TREND FILTER
    # =========================
    if trend == "bullish":
        score += 0.25
    elif trend == "bearish":
        score += 0.25

    # =========================
    # STRUCTURE (SMC CORE)
    # =========================
    if bos_up:
        score += 0.25
    if bos_down:
        score += 0.25

    if choch_up:
        score += 0.15
    if choch_down:
        score += 0.15

    # =========================
    # VOLATILITY FILTER
    # =========================
    if volatility > 0.5:
        score += 0.10

    # =========================
    # NOISE CONTROL (PENALTY)
    # =========================
    noise = random.uniform(0, 0.05)
    score -= noise

    # =========================
    # NORMALIZATION
    # =========================
    if score > 1:
        score = 1.0
    if score < 0:
        score = 0.0

    return round(score, 2)
