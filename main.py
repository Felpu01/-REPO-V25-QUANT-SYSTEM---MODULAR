from data.market_data import get_market_data
from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep
from core.score import calculate_score
from execution.execution import execute
from risk.risk import risk_control
import config

print("🚀 V28 QUANT SYSTEM STARTED (INSTITUTIONAL FILTERED)")

# =========================
# DATA LOAD
# =========================
data = get_market_data()

if not data or len(data) < 30:
    print("❌ ERROR: Insufficient market data")
    exit()

first = data[0]

if isinstance(first, dict):
    prices = [x.get("price", 0) for x in data if "price" in x]
else:
    prices = data

if len(prices) < 30:
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
for i in range(20, len(prices)):

    price = prices[i]

    # =========================
    # ENGINE
    # =========================
    tr = trend(prices, i)
    vol = volatility(prices, i)

    prev_high = max(prices[i-15:i])
    prev_low = min(prices[i-15:i])

    bos_up, bos_down = bos(price, prev_high, prev_low)
    choch_up, choch_down = choch(tr, bos_up, bos_down)

    sweep_up, sweep_down = liquidity_sweep(prices, i)

    # =========================
    # REGIME DETECTION (STRICTER)
    # =========================
    range_size = max(prices[i-15:i]) - min(prices[i-15:i])

    if vol > 0.75 and (bos_up or bos_down):
        regime = "EXPANSION"
    elif range_size < 35:
        regime = "RANGE"
    else:
        regime = "TREND"

    # =========================
    # BIAS MEMORY (CLEANER)
    # =========================
    bias_memory *= 0.85

    if bos_up:
        bias_memory += 0.9
    if choch_up:
        bias_memory += 1.1
    if sweep_down:
        bias_memory += 0.5

    if bos_down:
        bias_memory -= 0.9
    if choch_down:
        bias_memory -= 1.1
    if sweep_up:
        bias_memory -= 0.5

    # =========================
    # STRUCTURE LOCK
    # =========================
    structure_valid = bos_up or bos_down or choch_up or choch_down

    if choch_up:
        bias = "BULLISH"
        bias_memory = max(bias_memory, 1.4)

    elif choch_down:
        bias = "BEARISH"
        bias_memory = min(bias_memory, -1.4)

    else:
        if bias_memory > 0.9:
            bias = "BULLISH"
        elif bias_memory < -0.9:
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
    # LIQUIDITY FILTER (IMPORTANT FIX)
    # =========================
    if not structure_valid:
        sc *= 0.6   # reduce noise trades

    if sweep_up or sweep_down:
        sc += 0.20

    if choch_up or choch_down:
        sc += 0.12

    if bos_up or bos_down:
        sc += 0.06

    # =========================
    # REGIME BOOST (CONTROLLED)
    # =========================
    if regime == "EXPANSION":
        sc += 0.10
    elif regime == "TREND":
        sc += 0.05
    elif regime == "RANGE":
        sc -= 0.05   # 🔥 anti-chop fix

    # =========================
    # FINAL CAP
    # =========================
    sc = min(sc, 1.0)

    # =========================
    # 🚨 INSTITUTIONAL ENTRY FILTER V28
    # =========================
    alignment = (
        (bias == "BULLISH" and (bos_up or choch_up)) or
        (bias == "BEARISH" and (bos_down or choch_down))
    )

    high_quality = (
        regime == "EXPANSION" or
        (regime == "TREND" and abs(bias_memory) > 1.2)
    )

    # =========================
    # SIGNAL LOGIC (STRICT ENTRY)
    # =========================
    signal = "WAIT"

    if sc >= config.SCORE_THRESHOLD and alignment and high_quality:

        if bias == "BULLISH":
            signal = "BUY"
        elif bias == "BEARISH":
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
            f"PRICE:{price:.2f} SCORE:{sc:.2f} "
            f"REGIME:{regime} BIAS:{bias} ({bias_memory:.2f}) "
            f"BOS:{bos_up}/{bos_down} "
            f"CHOCH:{choch_up}/{choch_down} "
            f"SWEEP:{sweep_up}/{sweep_down} "
            f"SIGNAL:{signal} BAL:{balance:.2f} DD:{drawdown:.2f}"
        )
