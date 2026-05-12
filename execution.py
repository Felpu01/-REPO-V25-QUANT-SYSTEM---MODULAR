import random

def execute_trade(signal, price, balance):

    risk_per_trade = 0.01
    risk_amount = balance * risk_per_trade

    rr = random.uniform(1.0, 3.0)

    if signal == "BUY":
        result = random.choice(["WIN", "LOSS"])

    elif signal == "SELL":
        result = random.choice(["WIN", "LOSS"])

    else:
        return balance, 0, None

    if result == "WIN":
        pnl = risk_amount * rr
        balance += pnl

    else:
        pnl = -risk_amount
        balance += pnl

    return round(balance, 2), round(pnl, 2), result
