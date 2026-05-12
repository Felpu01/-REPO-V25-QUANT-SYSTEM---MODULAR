# execution.py

import random


def execute_trade(signal):

    if signal == "BUY":

        result = random.choice([
            random.uniform(5, 15),
            random.uniform(-10, -3)
        ])

        return round(result, 2)

    elif signal == "SELL":

        result = random.choice([
            random.uniform(5, 15),
            random.uniform(-10, -3)
        ])

        return round(result, 2)

    return 0
