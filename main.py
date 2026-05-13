from data.market_data import get_market_data
from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep
from core.score import calculate_score
from execution.execution import execute
from risk.risk import risk_control
import config

print("🚀 V29 QUANT SYSTEM STARTED (STRUCTURE-LOCKED)")

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

    tr = trend(prices, i)
    vol = volatility(prices, i)

    prev_high = max(prices[i-15:i])
    prev_low = min(prices[i-15:i])

    bos_up, bos_down = bos(price, prev_high, prev_low)
    choch_up, choch_down = choch(tr, bos_up, bos_down)

    sweep_up, sweep_down = liquidity_sweep(prices, i)

    # =========================
    # REGIME
    # =========================
    range_size = max(prices[i-15:i]) - min(prices[i-15:i])

    if vol > 0.75 and (bos_up or bos_down):
        regime = "EXPANSION"
    elif range_size < 35:
        regime = "RANGE"
    else:
        regime = "TREND"

    # =========================
    # STRUCTURE EVENT TRACKING (NEW CORE FIX)
    # =========================
    structure_event = False

    if bos_up or bos_down or choch_up or choch_down or sweep_up or sweep_down:
        structure_event = True

    # decay
    bias_memory *= 0.85

    # build
    if bos_up:
        bias_memory += 0.9
    if choch_up:
        bias_memory += 1.2
    if sweep_down:
        bias_memory += 0.6

    if bos_down:
        bias_memory -= 0.9
    if choch_down:
        bias_memory -= 1.2
    if sweep_up:
        bias_memory -= 0.6

    # =========================
    # STRUCTURE LOCK
    # =========================
    if choch_up:
        bias = "BULLISH"
        bias_memory = max(bias_memory, 1.5)

    elif choch_down:
        bias = "BEARISH"
        bias_memory = min(bias_memory, -1.5)

    else:
        if bias_memory > 0.9:
            bias = "BULLISH"
        elif bias_memory < -0.9:
            bias = "BEARISH"

    # =========================
    # ❌ CRITICAL FIX: GHOST BIAS BLOCK
    # =========================
    if not structure_event:
        bias_memory *= 0.7   # fade hard
        bias = "NEUTRAL" if abs(bias_memory) < 0.8 else bias

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
    # FILTERS
    # =========================
    if not structure_event:
        sc *= 0.55   # anti-noise hard filter

    if sweep_up or sweep_down:
        sc += 0.20

    if choch_up or choch_down:
        sc += 0.12

    if bos_up or bos_down:
        sc += 0.06

    # regime control
    if regime == "EXPANSION":
        sc += 0.10
    elif regime == "RANGE":
        sc -= 0.08

    sc = min(sc, 1.0)

    # =========================
    # ALIGNMENT CHECK (STRICT)
    # =========================
    alignment = (
        (bias == "BULLISH" and (bos_up or choch_up)) or
        (bias == "BEARISH" and (bos_down or choch_down))
    )

    quality = (structure_event and abs(bias_memory) > 1.2)

    signal = "WAIT"

    if sc >= config.SCORE_THRESHOLD and alignment and quality:

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

    dd = (peak - balance) / peak if peak > 0 else 0

    if not risk_control(dd, config.MAX_DRAWDOWN):
        print("🛑 MAX DRAWDOWN HIT - STOP")
        break

    # =========================
    # LOG
    # =========================
    if i % 20 == 0:
        print(
            f"PRICE:{price:.2f} SCORE:{sc:.2f} "
            f"REGIME:{regime} BIAS:{bias}({bias_memory:.2f}) "
            f"BOS:{bos_up}/{bos_down} "
            f"CHOCH:{choch_up}/{choch_down} "
            f"SWEEP:{sweep_up}/{sweep_down} "
            f"SIGNAL:{signal} BAL:{balance:.2f} DD:{dd:.2f}"
        )
