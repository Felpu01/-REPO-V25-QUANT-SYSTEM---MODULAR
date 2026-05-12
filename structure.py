# structure.py

def detect_structure(price_history):

    if len(price_history) < 6:
        return {
            "hh": False,
            "hl": False,
            "lh": False,
            "ll": False,
            "bos_bullish": False,
            "bos_bearish": False,
            "choch_bullish": False,
            "choch_bearish": False
        }

    p1 = price_history[-6]
    p2 = price_history[-5]
    p3 = price_history[-4]
    p4 = price_history[-3]
    p5 = price_history[-2]
    p6 = price_history[-1]

    hh = p6 > p5 and p5 > p4
    hl = p4 > p3 and p3 > p2

    lh = p6 < p5 and p5 < p4
    ll = p4 < p3 and p3 < p2

    bos_bullish = hh and hl
    bos_bearish = lh and ll

    choch_bullish = bos_bullish and p6 > p3
    choch_bearish = bos_bearish and p6 < p3

    return {
        "hh": hh,
        "hl": hl,
        "lh": lh,
        "ll": ll,
        "bos_bullish": bos_bullish,
        "bos_bearish": bos_bearish,
        "choch_bullish": choch_bullish,
        "choch_bearish": choch_bearish
    }
