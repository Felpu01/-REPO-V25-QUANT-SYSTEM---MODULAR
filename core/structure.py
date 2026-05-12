def bos(price, prev_high, prev_low):

    return price > prev_high, price < prev_low


def choch(trend, bos_up, bos_down):

    if trend == "DOWN" and bos_up:
        return True, False

    if trend == "UP" and bos_down:
        return False, True

    return False, False
