# structure.py

def detect_swings(prices, i):
    if i < 2 or i >= len(prices) - 2:
        return None, None

    high = prices[i]
    low = prices[i]

    left = prices[i - 2:i]
    right = prices[i + 1:i + 3]

    if high > max(left + right):
        return "SWING_HIGH", None

    if low < min(left + right):
        return None, "SWING_LOW"

    return None, None


def detect_bos(price, prev_high, prev_low):

    bos_up = price > prev_high
    bos_down = price < prev_low

    return bos_up, bos_down


def detect_choch(trend, bos_up,  bos_down):

    if trend == "DOWN" and bos_up:
        return True, False

    if trend == "UP" and bos_down:
        return False, True

    return False, False
