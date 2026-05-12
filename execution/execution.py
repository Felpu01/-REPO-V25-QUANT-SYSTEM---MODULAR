import random

def execute(signal, price, risk):

    if signal == "WAIT":
        return 0

    move = random.uniform(-1, 1)

    return risk * move
