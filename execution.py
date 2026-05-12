import random
import math

def execute_trade(signal, price, risk_amount):

    if signal == "WAIT":
        return 0.0

    direction = 1 if signal == "BUY" else -1

    # simulación controlada
    volatility_move = random.uniform(0.2, 1.5)

    pnl = direction * volatility_move * risk_amount

    # anti corrupción numérica
    if pnl is None or math.isnan(pnl) or math.isinf(pnl):
        pnl = 0.0

    return pnl
