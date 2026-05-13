from data.market_data import get_market_data

from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep

from core.score import calculate_score

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

    # =========================
    # ENGINE
    # =========================
    tr = trend(prices, i)
    vol = volatility(prices, i)

    prev_high = max(prices[i-10:i])
    prev_low = min(prices[i-10:i])

    bos_up, bos_down = bos(price, prev_high, prev_low)
    choch_up, choch_down = choch(tr, bos_up, bos_down)

    # =========================
    # LIQUIDITY
    # =========================
    sweep_up, sweep_down = liquidity_sweep(prices, i)

    # =========================
    # SCORE BASE
    # =========================
    sc = calculate_score(
        price=price,
        trend=tr,
        bos_up=bos_up,
        bos_down=bos_down,
        choch_up=choch_up,
        choch_down=choch_down,
        volatility=vol
    )

    # =========================
    # LIQUIDITY BOOST
    # =========================
    if sweep_up or sweep_down:
        sc += 0.25

    if choch_up or choch_down:
        sc += 0.10

    if bos_up or bos_down:
        sc += 0.05

    if sc > 1:
        sc = 1.0

    # =========================
    # SIGNAL LOGIC (SMC REAL FIX)
    # =========================
    signal = "WAIT"

    # BUY SETUP (LIQUIDITY SWEEP DOWN + STRUCTURE CONFIRMATION)
    if sc >= config.SCORE_THRESHOLD:
        if sweep_down and (bos_up or choch_up):
            signal = "BUY"

        # SELL SETUP (LIQUIDITY SWEEP UP + STRUCTURE CONFIRMATION)
        elif sweep_up and (bos_down or choch_down):
            signal = "SELL"

    # =========================
    # EXECUTION
    # =========================
    pnl = execute(signal, price, balance * config.RISK_PER_TRADE)

    balance += pnl

    # =========================
    # RISK
    # =========================
    if balance > peak:
        peak = balance

    drawdown = (peak - balance) / peak if peak > 0 else 0

    if not risk_control(drawdown, config.MAX_DRAWDOWN):
        print("🛑 MAX DRAWDOWN HIT - STOP")
        break

    # =========================
    # LOGS
    # =========================
    if i % 20 == 0:
        print(
            f"PRICE:{price:.2f} SCORE:{sc:.2f} TREND:{tr} "
            f"BOS_UP:{bos_up} BOS_DN:{bos_down} "
            f"SWEEP_U:{sweep_up} SWEEP_D:{sweep_down} "
            f"SIGNAL:{signal} BAL:{balance:.2f} DD:{drawdown:.2f}"
        )
