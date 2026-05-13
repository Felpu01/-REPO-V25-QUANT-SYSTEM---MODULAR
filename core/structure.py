def get_swings(prices, lookback=5):
    """
    Detecta swings highs y lows simples.
    """

    swing_highs = []
    swing_lows = []

    for i in range(lookback, len(prices) - lookback):

        left = prices[i - lookback:i]
        right = prices[i + 1:i + lookback + 1]

        current = prices[i]

        # SWING HIGH
        if current > max(left) and current > max(right):
            swing_highs.append(i)

        # SWING LOW
        if current < min(left) and current < min(right):
            swing_lows.append(i)

    return swing_highs, swing_lows


def bos(price, prev_high, prev_low):
    """
    Break of Structure (BOS)
    """

    bos_up = price > prev_high
    bos_down = price < prev_low

    return bos_up, bos_down


def choch(trend, bos_up, bos_down):
    """
    Change of Character (CHOCH)
    """

    choch_up = False
    choch_down = False

    if trend == "bearish" and bos_up:
        choch_up = True

    if trend == "bullish" and bos_down:
        choch_down = True

    return choch_up, choch_down


# =========================
# 🔥 LIQUIDITY ENGINE (NUEVO)
# =========================

def liquidity_sweep(prices, i, lookback=10):
    """
    Detecta sweep de liquidez:
    - rompe high/low previo
    - típico stop hunt institucional
    """

    if i < lookback:
        return False, False

    window = prices[i - lookback:i]
    current = prices[i]

    prev_high = max(window)
    prev_low = min(window)

    sweep_up = False
    sweep_down = False

    # 🔺 Sweep arriba (toma liquidez y rechaza)
    if current > prev_high:
        sweep_up = True

    # 🔻 Sweep abajo (toma liquidez y rechaza)
    if current < prev_low:
        sweep_down = True

    return sweep_up, sweep_down
