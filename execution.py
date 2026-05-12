import random

def execute_trade(signal, price, risk_amount):

    if signal == "WAIT":
        return 0.0

    direction = 1 if signal == "BUY" else -1

    # movimiento simulado del mercado (volatilidad institucional)
    volatility_move = random.uniform(0.5, 2.5)

    # PnL basado en riesgo (NO balance completo)
    pnl = direction * volatility_move * risk_amount

    return float(pnl)
