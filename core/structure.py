def get_swings(prices, lookback=5):
    """
    Detecta swings highs y lows simples.
    Devuelve listas de índices importantes.
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
    Break of Structure REAL simplificado.
    """

    bos_up = price > prev_high
    bos_down = price < prev_low

    return bos_up, bos_down


def choch(trend, bos_up, bos_down):
    """
    CHOCH real básico: cambio de intención estructural.
    """

    choch_up = False
    choch_down = False

    if trend == "bearish" and bos_up:
        choch_up = True

    if trend == "bullish" and bos_down:
        choch_down = True

    return choch_up, choch_down
