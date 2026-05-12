from engine import detect_trend, detect_volatility, detect_liquidity_sweep
from structure import detect_bos, detect_choch
from strategy import regime_logic, institutional_score
from execution import execute_trade

import random

print("🚀 V24.4 INSTITUTIONAL REAL ENGINE STARTED")


def generate_prices():
    prices = []
    price = 10000

    for _ in range(300):
        price += random.uniform(-20, 20)
        prices.append(price)

    return prices


prices = generate_prices()

balance = 10000
win = 0
loss = 0
peak = balance


for i in range(10, len(prices)):

    price = prices[i]
    prev_high = max(prices[i-10:i])
    prev_low = min(prices[i-10:i])

    trend = detect_trend(prices, i)
    vol = detect_volatility(prices, i)
    sweep = detect_liquidity_sweep(price, prev_high, prev_low)

    bos_up, bos_down = detect_bos(price, prev_high, prev_low)
    choch_up, choch_down = detect_choch(trend, bos_up, bos_down)

    regime = regime_logic(trend, sweep, vol)
    score = institutional_score(trend, vol, sweep, regime)

    signal = "WAIT"

    if score >= 0.85 and (bos_up or choch_up or sweep) and trend == "UP":
        signal = "BUY"

    elif score >= 0.85 and (bos_down or choch_down or sweep) and trend == "DOWN":
        signal = "SELL"

    pnl = execute_trade(signal, price, balance * 0.01)

    balance += pnl

    if pnl > 0:
        win += 1
    elif pnl < 0:
        loss += 1

    if balance > peak:
        peak = balance

    dd = (peak - balance) / peak * 100

    if i % 20 == 0:
        print(f"PRICE:{price:.2f} SIGNAL:{signal} SCORE:{score} BAL:{balance:.2f} DD:{dd:.2f}")
