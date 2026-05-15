from data.market_data import get_market_data

from core.engine import trend, volatility
from core.structure import bos, choch, liquidity_sweep
from core.score import calculate_score
from core.displacement import displacement

from core.signal_engine import generate_signal

from execution.execution import execute, update_positions

from risk.risk import risk_control
from risk.cooldown import CooldownManager

from persistence import save_state, load_state

import config

print("🚀 V38 QUANT SYSTEM STARTED (INSTITUTIONAL CORE FIX)")


# =========================
# LOAD STATE
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
# COOLDOWN
# =========================
cooldown_manager = CooldownManager()
cooldown_manager.cooldown = saved_cooldown


# =========================
# DATA
# =========================
data = get_market_data()

if not data or len(data) < 30:
    print("❌ ERROR: insufficient data")
    exit()

prices = [x["price"] if isinstance(x, dict) else x for x in data]


# =========================
# MAIN LOOP
# =========================
for i in range(20, len(prices)):

    cooldown_manager.update()
    price = prices[i]


    # =====================
    # POSITION UPDATE
    # =====================
    result = update_positions(price)

    if result:
        pnl = result.get("pnl", 0)
        balance += pnl

        reason = result.get("close_reason")

        if reason == "TP":
            wins += 1
            total_trades += 1
        elif reason == "SL":
            losses += 1
            total_trades += 1
        elif reason == "BE":
            breakevens += 1
            total_trades += 1


    # =====================
    # MARKET ENGINE
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
    # REGIME FIX V38
    # =====================
    range_size = prev_high - prev_low

    if vol > 0.78 and (bos_up or bos_down):
        regime = "EXPANSION"
    elif range_size < 30 and vol < 0.60:
        regime = "RANGE"
    else:
        regime = "TREND"


    # =====================
    # BIAS MEMORY (SMOOTHED V38)
    # =====================
    bias_memory *= 0.93

    if bos_up:
        bias_memory += 0.9
    if choch_up:
        bias_memory += 1.3
    if sweep_down:
        bias_memory += 0.6
    if displacement_valid:
        bias_memory += 0.7  # 🔥 MÁS PESO REAL

    if bos_down:
        bias_memory -= 0.9
    if choch_down:
        bias_memory -= 1.3
    if sweep_up:
        bias_memory -= 0.6

    bias_memory = max(min(bias_memory, 4.0), -4.0)

    bias = (
        "BULLISH" if bias_memory > 0.5
        else "BEARISH" if bias_memory < -0.5
        else "NEUTRAL"
    )


    # =========================
    # SCORE FIX REAL (CLAVE)
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

    # 🔥 BOOST REAL DE DISPLACEMENT (FIX CRÍTICO)
    sc += displacement_strength * 0.25
    sc = max(0.0, min(sc, 1.0))


    # =========================
    # MARKET ALIGNMENT V38
    # =========================
    alignment_ok = (
        (bias == "BULLISH" and tr > 0) or
        (bias == "BEARISH" and tr < 0)
    )

    liquidity_intent = sweep_up or sweep_down or bos_up or bos_down

    entry_quality = (
        displacement_valid and
        displacement_strength > 0.8 and
        liquidity_intent and
        vol > 0.45
    )


    # =========================
    # COOLDOWN CONTROL FIX
    # =========================
    base_signal = generate_signal(
        regime=regime,
        score=sc,
        bias=bias,
        bias_memory=bias_memory,
        bos_up=bos_up,
        bos_down=bos_down,
        choch_up=choch_up,
        choch_down=choch_down,
        sweep_up=sweep_up,
        sweep_down=sweep_down,
        displacement_valid=displacement_valid,
        volatility=vol
    )

    force_trade = sc >= 0.80 and entry_quality and alignment_ok

    if cooldown_manager.get_cooldown() > 0 and not force_trade:
        signal = "WAIT"
    else:
        signal = base_signal


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
    closed = wins + losses
    wr = wins / closed if closed > 0 else 0


    # =====================
    # LOG
    # =====================
    if i % 50 == 0:
        print(
            f"PRICE:{price:.2f} "
            f"SCORE:{sc:.2f} "
            f"REGIME:{regime} "
            f"BIAS:{bias}({bias_memory:.2f}) "
            f"DISP:{displacement_strength:.2f} "
            f"SWEEP:{sweep_up}/{sweep_down} "
            f"SIGNAL:{signal} "
            f"BAL:{balance:.2f} "
            f"WR:{wr:.2f}"
        )
