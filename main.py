from data.market_data import get_market_data
from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep
from core.score import calculate_score
from core.displacement import displacement
from core.retest import retest_entry

from execution.execution import execute
from execution.execution import update_positions

from risk.risk import risk_control

import config

print("🚀 V33.3 QUANT SYSTEM STARTED (STABLE POSITION ENGINE)")


# =========================
# MARKET DATA
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


# =========================
# ACCOUNT
# =========================
balance = config.BALANCE_START

peak = balance

wins = 0
losses = 0
breakevens = 0
total_trades = 0


# =========================
# STATE
# =========================
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

    bos_up, bos_down = bos(
        price,
        prev_high,
        prev_low
    )

    choch_up, choch_down = choch(
        tr,
        bos_up,
        bos_down
    )

    sweep_up, sweep_down = liquidity_sweep(
        prices,
        i
    )

    # =========================
    # DISPLACEMENT ENGINE
    # =========================
    displacement_valid, displacement_strength = displacement(
        prices,
        i
    )

    # =========================
    # REGIME ENGINE
    # =========================
    range_size = (
        max(prices[i - 12:i])
        - min(prices[i - 12:i])
    )

    if vol > 0.75 and (bos_up or bos_down):

        regime = "EXPANSION"

    elif range_size < 40:

        regime = "RANGE"

    else:

        regime = "TREND"

    # =========================
    # STRUCTURE SCORE
    # =========================
    structure_score = 0.0

    if bos_up or bos_down:
        structure_score += 1.0

    if choch_up or choch_down:
        structure_score += 1.2

    if sweep_up or sweep_down:
        structure_score += 0.8

    if displacement_valid:
        structure_score += 1.0

    structure_event = structure_score >= 1.0

    # =========================
    # BIAS MEMORY ENGINE
    # =========================
    bias_memory *= 0.94

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

        bias_memory = max(
            bias_memory,
            1.3
        )

    elif choch_down:

        bias_memory = min(
            bias_memory,
            -1.3
        )

    # =========================
    # FINAL BIAS
    # =========================
    if bias_memory > 0.7:

        bias = "BULLISH"

    elif bias_memory < -0.7:

        bias = "BEARISH"

    else:

        bias = "NEUTRAL"

    # =========================
    # RETEST ENGINE
    # =========================
    bullish_retest, bearish_retest = retest_entry(
        price,
        prev_high,
        prev_low,
        bias
    )

    # =========================
    # SCORE ENGINE
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
    #
