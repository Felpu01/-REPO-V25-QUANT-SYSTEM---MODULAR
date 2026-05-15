from data.market_data import get_market_data

from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep
from core.score import calculate_score
from core.displacement import displacement
from core.retest import retest_entry

from execution.execution import execute, update_positions

from risk.risk import risk_control
from risk.cooldown import CooldownManager

from persistence import save_state, load_state

import config

print("🚀 V34 QUANT SYSTEM STARTED (PERSISTENCE LAYER)")


# =========================
# LOAD RUNTIME STATE
# =========================
runtime = load_state()

if runtime:
    print("♻️ RESTORING RUNTIME STATE")

    balance = runtime.get("balance", config.BALANCE_START)
    peak = runtime.get("peak", balance)
    wins = runtime.get("wins", 0)
    losses = runtime.get("losses", 0)
    breakevens = runtime.get("breakevens", 0)
    total_trades = runtime.get("total_trades", 0)
    bias_memory = runtime.get("bias_memory", 0.0)
    saved_cooldown = runtime.get("cooldown", 0)

else:
    print("🆕 NEW RUNTIME SESSION")

    balance = config.BALANCE_START
    peak = balance
    wins = 0
    losses = 0
    breakevens = 0
    total_trades = 0
    bias_memory = 0.0
    saved_cooldown = 0


# =========================
# STATE
# =========================
regime = "RANGE"
bias = "NEUTRAL"

cooldown_manager = CooldownManager()
cooldown_manager.cooldown = saved_cooldown


# =========================
# MARKET DATA
# =========================
data = get_market_data()

if not data or len(data) < 30:
    print("❌ ERROR: Insufficient market data")
    exit()

first = data[0]

if isinstance(first, dict):
    prices = [x.get("price", 0) for x in data if x.get("price")]
else:
    prices = data


# =========================
# MAIN LOOP
# =========================
for i in range(20, len(prices)):

    cooldown_manager.update()

    price = prices[i]


    # =====================
    # POSITION UPDATE (SAFE)
    # =====================
    result = update_positions(price)

    if result:
        pnl = result.get("pnl", 0)
        balance += pnl

        close_reason = result.get("close_reason")

        if close_reason == "TP":
            wins += 1
            total_trades += 1

        elif close_reason == "SL":
            losses += 1
            total_trades += 1

        elif close_reason == "BE":
            breakevens += 1
            total_trades += 1


    # =====================
    # MARKET ENGINES
    # =====================
    tr = trend(prices, i)
    vol = volatility(prices, i)

    prev_high = max(prices[i - 12:i])
    prev_low = min(prices[i - 12:i])

    bos_up, bos_down = bos(price, prev_high, prev_low)
    choch_up, choch_down = choch(tr, bos_up, bos_down)
    sweep_up, sweep_down = liquidity_sweep(prices, i)

    displacement_valid, displacement_strength = displacement(prices, i)


    # =====================
    # REGIME ENGINE
    # =====================
    range_size = max(prices[i - 12:i]) - min(prices[i - 12:i])

    if vol > 0.75 and (bos_up or bos_down):
        regime = "EXPANSION"
    elif range_size < 40:
        regime = "RANGE"
    else:
        regime = "TREND"


    # =====================
    # STRUCTURE SCORE
    # =====================
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


    # =====================
    # BIAS MEMORY
    # =====================
    bias_memory *= 0.94

    if bos_up:
        bias_memory += 0.8
    if choch_up:
        bias_memory += 1.0
    if sweep_down:
        bias_memory += 0.5

    if bos_down:
        bias_memory -= 0.8
    if choch_down:
        bias_memory -= 1.0
    if sweep_up:
        bias_memory -= 0.5

    if choch_up:
        bias_memory = max(bias_memory, 1.3)
    elif choch_down:
        bias_memory = min(bias_memory, -1.3)

    if bias_memory > 0.7:
        bias = "BULLISH"
    elif bias_memory < -0.7:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"


    # =====================
    # RETEST ENGINE
    # =====================
    bullish_retest, bearish_retest = retest_entry(
        price, prev_high, prev_low, bias
    )


    # =====================
    # SCORE ENGINE
    # =====================
    sc = calculate_score(
        price=price,
        trend=tr,
        bos_up=bos_up,
        bos_down=bos_down,
        choch_up=choch_up,
        choch_down=choch_down,
        volatility=vol
    )

    if not structure_event:
        sc *= 0.70

    if sweep_up or sweep_down:
        sc += 0.20
    if choch_up or choch_down:
        sc += 0.10
    if bos_up or bos_down:
        sc += 0.05
    if displacement_valid:
        sc += 0.15

    if regime == "EXPANSION":
        sc += 0.12
    elif regime == "TREND":
        sc += 0.07
    elif regime == "RANGE":
        sc -= 0.05

    sc = max(0.0, min(sc, 1.0))


    # =====================
    # SIGNAL ENGINE (CLEAN FIX)
    # =====================
    signal = "WAIT"

    if cooldown_manager.allowed_to_trade():

        strong = sc >= 0.85
        mid = sc >= 0.70
        weak = sc >= 0.55

        bullish = bias_memory > 0.25
        bearish = bias_memory < -0.25

        structure_ok = structure_event or bos_up or bos_down or choch_up or choch_down
        good_market = regime in ["TREND", "EXPANSION"]

        # 🔥 FIX: evita entradas falsas en RANGE
        if regime == "RANGE" and sc < 0.90:
            signal = "WAIT"

        elif strong and bullish and good_market and displacement_valid:
            signal = "BUY"

        elif strong and bearish and good_market and displacement_valid:
            signal = "SELL"

        elif mid and bullish and structure_ok:
            signal = "BUY"

        elif mid and bearish and structure_ok:
            signal = "SELL"

        elif weak and bias_memory > 0.40 and bos_up:
            signal = "BUY"

        elif weak and bias_memory < -0.40 and bos_down:
            signal = "SELL"


    # =====================
    # EXECUTION
    # =====================
    exec_result = execute(
        signal=signal,
        price=price,
        atr=vol,
        score=sc,
        balance=balance
    )


    # =====================
    # SAVE STATE
    # =====================
    runtime_state = {
        "balance": balance,
        "peak": peak,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "total_trades": total_trades,
        "bias_memory": bias_memory,
        "cooldown": cooldown_manager.get_cooldown()
    }

    save_state(runtime_state)


    # =====================
    # RISK
    # =====================
    if balance > peak:
        peak = balance

    dd = (peak - balance) / peak if peak > 0 else 0

    if not risk_control(dd, config.MAX_DRAWDOWN):
        print("🛑 MAX DRAWDOWN HIT - STOP")
        break


    # =====================
    # WINRATE
    # =====================
    closed_trades = wins + losses
    winrate = wins / closed_trades if closed_trades > 0 else 0


    # =====================
    # LOGS
    # =====================
    if i % 50 == 0:
        print(
            f"PRICE:{price:.2f} "
            f"SCORE:{sc:.2f} "
            f"REGIME:{regime} "
            f"BIAS:{bias}({bias_memory:.2f}) "
            f"DISP:{displacement_strength:.2f} "
            f"STRUCT:{structure_score:.2f} "
            f"RETEST:{bullish_retest}/{bearish_retest} "
            f"BOS:{bos_up}/{bos_down} "
            f"CHOCH:{choch_up}/{choch_down} "
            f"SWEEP:{sweep_up}/{sweep_down} "
            f"SIGNAL:{signal} "
            f"BAL:{balance:.2f} "
            f"WR:{winrate:.2f} "
            f"BE:{breakevens} "
            f"CD:{cooldown_manager.get_cooldown()} "
            f"DD:{dd:.2f}"
        )
