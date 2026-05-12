# structure.py

def detect_bos(price, previous_price):
    
    bos_up = False
    bos_down = False

    if price > previous_price + 1:
        bos_up = True

    if price < previous_price - 1:
        bos_down = True

    return bos_up, bos_down


def detect_choch(trend, bos_up, bos_down):

    choch_up = False
    choch_down = False

    if trend == "TREND_DOWN" and bos_up:
        choch_up = True

    if trend == "TREND_UP" and bos_down:
        choch_down = True

    return choch_up, choch_down
