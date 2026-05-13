from data.market_data import get_market_data

from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep
from core.score import calculate_score

from execution.execution import execute
from risk.risk import risk_control

import config

print("🚀 V27 QUANT SYSTEM STARTED (ADAPTIVE ENGINE)")

# =========================
# DATA VALIDATION
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

bias = "NEUTRAL"
bias_memory = 0

# =========================
# LOOP
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
    # REGIME
    # =========================
    range_size = prev_high - prev_low

    if vol > 0.75 and (bos_up or bos_down):
        regime = "EXPANSION"
    elif range_size < 60:
        regime = "RANGE"
    else:
        regime = "TREND"

    # =========================
    # BIAS MEMORY (ADAPTIVE DECAY)
    # =========================
    if regime == "RANGE":
        bias_memory *= 0.82
    elif regime == "TREND":
        bias_memory *= 0.90
    else:
        bias_memory *= 0.94

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
    # BIAS (SOFT DIRECTION)
    # =========================
    if bias_memory > 0.55:
        bias = "BULLISH"
    elif bias_memory < -0.55:
        bias = "BEARISH"

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
    # LIQUIDITY ENGINE (IMPROVED)
    # =========================
    liquidity_boost = 0.0

    if sweep_up or sweep_down:
        liquidity_boost += 0.25

    if choch_up or choch_down:
        liquidity_boost += 0.13

    if bos_up or bos_down:
        liquidity_boost += 0.07

    # RANGE BONUS (IMPORTANT FIX)
    if regime == "RANGE":
        liquidity_boost += (sweep_up or sweep_down) * 0.10

    # =========================
    # FINAL SCORE (REAL EDGE)
    # =========================
    final_score = sc + liquidity_boost + (bias_memory * 0.06)

    final_score = min(final_score, 1.0)

    # =========================
    # ADAPTIVE THRESHOLD (KEY FIX)
    # =========================
    if regime == "EXPANSION":
        threshold = 0.84
    elif regime == "TREND":
        threshold = 0.82
    else:  # RANGE
        threshold = 0.78

    # =========================
    # ENTRY RULE
    # =========================
    signal = "WAIT"

    if final_score >= threshold:

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
            f"PRICE:{price:.2f} "
            f"FS:{final_score:.2f} "
            f"SCORE:{sc:.2f} "
            f"TH:{threshold:.2f} "
            f"BIAS:{bias}({bias_memory:.2f}) "
            f"REGIME:{regime} "
            f"BOS:{bos_up}/{bos_down} "
            f"CHOCH:{choch_up}/{choch_down} "
            f"SWEEP:{sweep_up}/{sweep_down} "
            f"SIGNAL:{signal} "
            f"BAL:{balance:.2f} DD:{drawdown:.2f}"
        )
