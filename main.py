from data.market_data import get_market_data

from core.engine import trend, volatility
from core.structure import bos, choch
from core.strategy import score

from execution.execution import execute
from risk.risk import risk_control

import config

print("🚀 V25 QUANT SYSTEM STARTED")

data = get_market_data()

prices = [x["price"] for x in data]

balance = config.BALANCE_START
peak = balance

for i in range(10, len(data)):

    price = prices[i]

    tr = trend(prices, i)
    vol = volatility(prices, i)

    prev_high = max(prices[i-10:i])
    prev_low = min(prices[i-10:i])

    bos_up, bos_down = bos(price, prev_high, prev_low)
    choch_up, choch_down = choch(tr, bos_up, bos_down)

    sc = score(tr, vol, bos_up, bos_down)

    signal = "BUY" if sc >= config.SCORE_THRESHOLD else "WAIT"

    pnl = execute(signal, price, balance * config.RISK_PER_TRADE)

    balance += pnl

    if balance > peak:
        peak = balance

    drawdown = (peak - balance) / peak if peak > 0 else 0

    if not risk_control(drawdown, config.MAX_DRAWDOWN):
        print("🛑 MAX DRAWDOWN HIT - STOP")
        break

    if i % 20 == 0:
        print(f"PRICE:{price:.2f} SCORE:{sc} BAL:{balance:.2f} DD:{drawdown:.2f}")
