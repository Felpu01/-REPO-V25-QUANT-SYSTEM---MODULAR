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

# =========================
# MEMORY STATE (SMC CONTEXT)
# =========================
active_sweep = None
sweep_index = None

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
    # MEMORY UPDATE
    # =========================

    if sweep_down:
        active_sweep = "BUY"
        sweep_index = i

    elif sweep_up:
        active_sweep = "SELL"
        sweep_index = i

    # reset memory after 8 candles
    if active_sweep is not None and sweep_index is not None:
        if i - sweep_index > 8:
            active_sweep = None
            sweep_index = None

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
    # SIGNAL LOGIC (FIX FINAL)
    # =========================
    signal = "WAIT"

    if sc >= config.SCORE_THRESHOLD:

        # =========================
        # BUY LOGIC (FLEXIBLE SMC)
        # =========================
        if active_sweep == "BUY":
            if bos_up or choch_up or sc > 0.80:
                signal = "BUY"
                active_sweep = None
                sweep_index = None

        # =========================
        # SELL LOGIC (FLEXIBLE SMC)
        # =========================
        elif active_sweep == "SELL":
            if bos_down or choch_down or sc > 0.80:
                signal = "SELL"
                active_sweep = None
                sweep_index = None

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
            f"ACTIVE_SWEEP:{active_sweep} "
            f"SIGNAL:{signal} BAL:{balance:.2f} DD:{drawdown:.2f}"
        )
