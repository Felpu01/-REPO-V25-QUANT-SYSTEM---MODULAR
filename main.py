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
# MARKET REGIME STATE
# =========================
regime = "RANGE"

# =========================
# MARKET BIAS STATE
# =========================
bias = "NEUTRAL"
bias_memory = 0  # 🔥 FIX CLAVE

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
    # REGIME DETECTION
    # =========================

    if vol > 0.7 and (bos_up or bos_down):
        regime = "EXPANSION"

    elif max(prices[i-10:i]) - min(prices[i-10:i]) < 50:
        regime = "RANGE"

    else:
        regime = "TREND"

    # =========================
    # BIAS DETECTION (MEMORY VERSION FIX)
    # =========================

    # decaimiento natural (evita flip constante)
    bias_memory *= 0.9

    # bullish pressure
    if bos_up:
        bias_memory += 1.0

    if choch_up:
        bias_memory += 0.8

    if sweep_down:
        bias_memory += 0.6  # liquidity grab abajo = bullish intent

    # bearish pressure
    if bos_down:
        bias_memory -= 1.0

    if choch_down:
        bias_memory -= 0.8

    if sweep_up:
        bias_memory -= 0.6  # liquidity grab arriba = bearish intent

    # final bias decision
    if bias_memory > 0.5:
        bias = "BULLISH"

    elif bias_memory < -0.5:
        bias = "BEARISH"

    else:
        bias = "NEUTRAL"

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

    # =========================
    # REGIME BOOST
    # =========================

    if regime == "EXPANSION":
        sc += 0.10

    elif regime == "RANGE":
        sc += 0.05

    # =========================
    # FINAL SCORE CAP
    # =========================
    if sc > 1:
        sc = 1.0

    # =========================
    # SIGNAL LOGIC (BIAS FILTERED)
    # =========================
    signal = "WAIT"

    if sc >= config.SCORE_THRESHOLD:

        # BUY ONLY IF BIAS IS BULLISH
        if bias == "BULLISH" and (bos_up or choch_up or sweep_down):
            signal = "BUY"

        # SELL ONLY IF BIAS IS BEARISH
        elif bias == "BEARISH" and (bos_down or choch_down or sweep_up):
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
            f"REGIME:{regime} BIAS:{bias} "
            f"BOS_UP:{bos_up} BOS_DN:{bos_down} "
            f"SWEEP_U:{sweep_up} SWEEP_D:{sweep_down} "
            f"SIGNAL:{signal} BAL:{balance:.2f} DD:{drawdown:.2f}"
        )
