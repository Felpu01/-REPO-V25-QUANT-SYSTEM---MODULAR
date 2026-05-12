def score(trend, vol, bos_up, bos_down):

    s = 0

    if trend in ["UP", "DOWN"]:
        s += 0.3

    if vol == "HIGH":
        s += 0.3

    if bos_up or bos_down:
        s += 0.25

    return round(min(1, s), 2)
