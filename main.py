from data.market_data import get_market_data

from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep

from core.score import calculate_score

from execution.execution import execute
from risk.risk import risk_control

import config

print("🚀 V25 QUANT SYSTEM STARTED")

# =========================
# DATA VALIDATION FIX
# =========================
data = get_market_data()

if not data or len(data) < 20:
    print("❌ ERROR: Insufficient market data")
    exit()

first = data[0]

if isinstance(first, dict):
    prices = [x.get("price", 0) for x in data if "price" in x]
elif isinstance(first, (int, float)):
    prices = data
else:
    print("❌ ERROR: Unknown data format")
    exit()

if len(prices) < 20:
    print("❌ ERROR: Prices invalid")
    exit()

# =========================
# STATE
# =========================
balance = config.BALANCE_START
peak = balance

regime = "RANGE"
bias = "NEUTRAL"
bias_memory = 0

# =========================
# MAIN LOOP
# =========================
for i in range(10, len(prices)):

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

    sweep_up, sweep_down = liquidity_sweep(prices, i)

    # =========================
    # REGIME DETECTION (FIXED)
    # =========================
    range_size = prev_high - prev_low

    if vol > 0.75 and (bos_up or bos_down):
        regime = "EXPANSION"

    elif range_size < 60:
        regime = "RANGE"

    else:
        regime = "TREND"

    # =========================
    # BIAS MEMORY (ADAPTIVE CORE FIX)
    # =========================
    if regime == "RANGE":
        bias_memory *= 0.80
    elif regime == "TREND":
        bias_memory *= 0.90
    else:
        bias_memory *= 0.95

    # structure pressure
    if bos_up:
        bias_memory += 1.0
    if choch_up:
        bias_memory += 1.1
    if sweep_down:
        bias_memory += 0.7

    if bos_down:
        bias_memory -= 1.0
    if choch_down:
        bias_memory -= 1.1
    if sweep_up:
        bias_memory -= 0.7

    # =========================
    # STRUCTURE LOCK
    # =========================
    if choch_up:
        bias = "BULLISH"
        bias_memory = max(bias_memory, 1.2)

    elif choch_down:
        bias = "BEARISH"
        bias_memory = min(bias_memory, -1.2)

    else:
        if bias_memory > 0.6:
            bias = "BULLISH"
        elif bias_memory < -0.6:
            bias = "BEARISH"

    # =========================
    # SCORE
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
    # STRUCTURE FILTER (REDUCED OVERFILTER)
    # =========================
    if bias == "BULLISH" and not (bos_up or choch_up):
        sc *= 0.85

    if bias == "BEARISH" and not (bos_down or choch_down):
        sc *= 0.85

    # =========================
    # LIQUIDITY BOOST (REALISTIC)
    # =========================
    if sweep_up or sweep_down:
        sc += 0.22

    if choch_up or choch_down:
        sc += 0.12

    if bos_up or bos_down:
        sc += 0.06

    # =========================
    # REGIME BOOST
    # =========================
    if regime == "EXPANSION":
        sc += 0.10
    elif regime == "RANGE":
        sc += 0.04

    # =========================
    # FINAL CAP
    # =========================
    sc = min(sc, 1.0)

    # =========================
    # CONTEXT FILTER (BALANCED FIX)
    # =========================
    valid_context = (
        regime == "EXPANSION"
        or (regime == "TREND" and abs(bias_memory) > 0.9)
        or (regime == "RANGE" and (sweep_up or sweep_down))
    )

    # =========================
    # SIGNAL LOGIC
    # =========================
    signal = "WAIT"

    if sc >= config.SCORE_THRESHOLD and valid_context:

        if bias == "BULLISH" and (bos_up or choch_up or sweep_down):
            signal = "BUY"

        elif bias == "BEARISH" and (bos_down or choch_down or sweep_up):
            signal = "SELL"

    # =========================
    # EXECUTION
    # =========================
    pnl = execute(signal, price, balance * config.RISK_PER_TRADE)

    if pnl is None:
        pnl = 0.0

    balance += pnl

    # =========================
    # RISK
    # =========================
    peak = max(peak, balance)
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
