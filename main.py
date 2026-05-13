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

# =========================
# DATA NORMALIZATION FIX
# =========================
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

balance = config.BALANCE_START
peak = balance

# =========================
# MARKET STATE
# =========================
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
    # REGIME DETECTION
    # =========================
    if vol > 0.7 and (bos_up or bos_down):
        regime = "EXPANSION"

    elif max(prices[i-10:i]) - min(prices[i-10:i]) < 50:
        regime = "RANGE"

    else:
        regime = "TREND"

    # =========================
    # BIAS MEMORY (SMOOTH)
    # =========================
    bias_memory *= 0.90

    if bos_up:
        bias_memory += 1.0
    if choch_up:
        bias_memory += 1.0
    if sweep_down:
        bias_memory += 0.6

    if bos_down:
        bias_memory -= 1.0
    if choch_down:
        bias_memory -= 1.0
    if sweep_up:
        bias_memory -= 0.6

    # =========================
    # STRUCTURE LOCK
    # =========================
    if choch_up:
        bias = "BULLISH"
        bias_memory = max(bias_memory, 1.3)

    elif choch_down:
        bias = "BEARISH"
        bias_memory = min(bias_memory, -1.3)

    else:
        if bias_memory > 0.7:
            bias = "BULLISH"
        elif bias_memory < -0.7:
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
    # STRUCTURE FILTER
    # =========================
    if bias == "BULLISH" and not (bos_up or choch_up):
        sc *= 0.8

    if bias == "BEARISH" and not (bos_down or choch_down):
        sc *= 0.8

    # =========================
    # LIQUIDITY BOOST
    # =========================
    if sweep_up or sweep_down:
        sc += 0.25

    if choch_up or choch_down:
        sc += 0.15

    if bos_up or bos_down:
        sc += 0.08

    # =========================
    # REGIME BOOST
    # =========================
    if regime == "EXPANSION":
        sc += 0.12
    elif regime == "RANGE":
        sc += 0.06

    # =========================
    # FINAL CAP
    # =========================
    if sc > 1:
        sc = 1.0

    # =========================
    # 🚨 INSTITUTIONAL CONTEXT FILTER (NEW FIX)
    # =========================
    valid_context = False

    if regime == "EXPANSION":
        valid_context = True

    elif regime == "TREND" and abs(bias_memory) > 1.0:
        valid_context = True

    elif regime == "RANGE" and (sweep_up or sweep_down):
        valid_context = True

    # =========================
    # SIGNAL LOGIC (FINAL FIXED)
    # =========================
    signal = "WAIT"

    if sc >= config.SCORE_THRESHOLD and valid_context:

        if bias == "BULLISH" and (bos_up or choch_up or sweep_down):
            signal = "BUY"

        elif bias == "BEARISH" and (bos_down or choch_down or sweep_up):
            signal = "SELL"

    # =========================
    # EXECUTION SAFETY
    # =========================
    pnl = execute(signal, price, balance * config.RISK_PER_TRADE)

    if pnl is None:
        pnl = 0.0

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
