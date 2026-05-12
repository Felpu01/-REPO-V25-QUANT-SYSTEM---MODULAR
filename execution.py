random

def execute_trade(signal, price, risk_amount):
    if signal == "WAIT":
        return 0.0

    direction = 1 if signal == "BUY" else -1

    # movimiento realista acotado (evita explosión infinita)
    volatility_move = random.uniform(0.2, 1.5)

    pnl = direction * volatility_move * risk_amount

    # seguridad anti NaN
    if pnl != pnl:  # check NaN
        pnl = 0.0

    return pnlh
