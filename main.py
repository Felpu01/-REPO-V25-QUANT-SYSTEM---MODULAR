from data.market_data import get_market_data
from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep
from core.score import calculate_score
from execution.execution import execute
from risk.risk import risk_control
import config

print("🚀 V30 QUANT SYSTEM STARTED (BALANCED INSTITUTIONAL ENGINE)")

data = get_market_data()

if not data or len(data) < 30:
    print("❌ ERROR: Insufficient market data")
    exit()

first = data[0]

if isinstance(first, dict):
    prices = [x.get("price", 0) for x in data if "price" in x]
else:
    prices = data

balance = config.BALANCE_START
peak = balance

regime = "RANGE"
bias = "NEUTRAL"
bias_memory = 0.0

# =========================
# MAIN LOOP
# =========================
for i in range(20, len(prices)):

    price = prices[i]

    tr = trend(prices, i)
    vol = volatility(prices, i)

    prev_high = max(prices[i - 12:i])
    prev_low = min(prices[i - 12:i])

    bos_up, bos_down = bos(price, prev_high, prev_low)
    choch_up, choch_down = choch(tr, bos_up, bos_down)
    sweep_up, sweep_down = liquidity_sweep(prices, i)

    # =========================
    # REGIME
    # =========================
    range_size = max(prices[i - 12:i]) - min(prices[i - 12:i])

    if vol > 0.75 and (bos_up or bos_down):
        regime = "EXPANSION"

    elif range_size < 40:
        regime = "RANGE"

    else:
        regime = "TREND"

    # =========================
    # STRUCTURE ACTIVITY SCORE
    # =========================
    structure_score = 0.0

    if bos_up or bos_down:
        structure_score += 1.0

    if choch_up or choch_down:
        structure_score += 1.2

    if sweep_up or sweep_down:
        structure_score += 0.8

    structure_event = structure_score >= 0.8

    # =========================
    # BIAS MEMORY ENGINE
    # =========================
    bias_memory *= 0.88

    # Bullish pressure
    if bos_up:
        bias_memory += 0.8

    if choch_up:
        bias_memory += 1.0

    if sweep_down:
        bias_memory += 0.5

    # Bearish pressure
    if bos_down:
        bias_memory -= 0.8

    if choch_down:
        bias_memory -= 1.0

    if sweep_up:
        bias_memory -= 0.5

    # =========================
    # STRUCTURE LOCK
    # =========================
    if choch_up:
        bias_memory = max(bias_memory, 1.3)

    elif choch_down:
        bias_memory = min(bias_memory, -1.3)

    # =========================
    # FINAL BIAS FIX
    # =========================
    if bias_memory > 0.7:
        bias = "BULLISH"

    elif bias_memory < -0.7:
        bias = "BEARISH"

    else:
        bias = "NEUTRAL"

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
        sc *= 0.75

    if sweep_up or sweep_down:
        sc += 0.20

    if choch_up or choch_down:
        sc += 0.10

    if bos_up or bos_down:
        sc += 0.05

    # =========================
    # REGIME BOOST
    # =========================
    if regime == "EXPANSION":
        sc += 0.12

    elif regime == "TREND":
        sc += 0.07

    elif regime == "RANGE":
        sc -= 0.03

    sc = max(0.0, min(sc, 1.0))

    # =========================
    # ENTRY CONDITIONS
    # =========================
    alignment = (
        (bias == "BULLISH" and (bos_up or choch_up or sweep_down))
        or
        (bias == "BEARISH" and (bos_down or choch_down or sweep_up))
    )

    quality = (
        structure_event
        and abs(bias_memory) > 1.0
    )

    signal = "WAIT"

    if sc >= config.SCORE_THRESHOLD and (alignment or quality):

        if bias == "BULLISH":
            signal = "BUY"

        elif bias == "BEARISH":
            signal = "SELL"

    # =========================
    # EXECUTION
    # =========================
    pnl = execute(
        signal,
        price,
        balance * config.RISK_PER_TRADE
    )

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
            f"PRICE:{price:.2f} "
            f"SCORE:{sc:.2f} "
            f"REGIME:{regime} "
            f"BIAS:{bias}({bias_memory:.2f}) "
            f"STRUCT:{structure_score:.2f} "
            f"BOS:{bos_up}/{bos_down} "
            f"CHOCH:{choch_up}/{choch_down} "
            f"SWEEP:{sweep_up}/{sweep_down} "
            f"SIGNAL:{signal} "
            f"BAL:{balance:.2f} "
            f"DD:{dd:.2f}"
        )
