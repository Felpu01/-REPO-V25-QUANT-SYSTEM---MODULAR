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

from core.entry_quality import entry_quality

import config

print("🚀 V39.2 QUANT SYSTEM STARTED (EXECUTION LAYER FIXED)")


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
    tr_raw = trend(prices, i)
    vol = float(volatility(prices, i))

    tr_dir = 0
    if isinstance(tr_raw, str):
        t = tr_raw.upper()
        if t in ["UP", "BULL", "BULLISH", "BUY", "LONG"]:
            tr_dir = 1
        elif t in ["DOWN", "BEAR", "BEARISH", "SELL", "SHORT"]:
            tr_dir = -1
    else:
        try:
            tr_dir = 1 if float(tr_raw) > 0 else -1
        except:
            tr_dir = 0


    prev_high = max(prices[i - 12:i])
    prev_low = min(prices[i - 12:i])

    bos_up, bos_down = bos(price, prev_high, prev_low)
    choch_up, choch_down = choch(tr_dir, bos_up, bos_down)
    sweep_up, sweep_down = liquidity_sweep(prices, i)

    displacement_valid, displacement_strength = displacement(prices, i)


    # =========================
    # REGIME
    # =========================
    range_size = prev_high - prev_low

    if vol > 0.72 and (bos_up or bos_down):
        regime = "EXPANSION"
    elif range_size < 25 and vol < 0.55:
        regime = "RANGE"
    else:
        regime = "TREND"


    # =========================
    # BIAS MEMORY
    # =========================
    bias_memory *= 0.94

    if bos_up:
        bias_memory += 1.0
    if choch_up:
        bias_memory += 1.4
    if sweep_down:
        bias_memory += 0.7
    if displacement_valid:
        bias_memory += 0.8

    if bos_down:
        bias_memory -= 1.0
    if choch_down:
        bias_memory -= 1.4
    if sweep_up:
        bias_memory -= 0.7

    bias_memory = max(min(bias_memory, 5.0), -5.0)

    bias = (
        "BULLISH" if bias_memory > 0.4
        else "BEARISH" if bias_memory < -0.4
        else "NEUTRAL"
    )


    # ==========================================================
    # FIX 1 — SCORE (NO FILTER, ONLY BASE VALUE)
    # ==========================================================
    base_score = calculate_score(
        price=price,
        trend=tr_dir,
        bos_up=bos_up,
        bos_down=bos_down,
        choch_up=choch_up,
        choch_down=choch_down,
        volatility=vol
    )

    structure_score = (
        (0.25 if bos_up or bos_down else 0) +
        (0.20 if choch_up or choch_down else 0) +
        (0.20 if sweep_up or sweep_down else 0) +
        (displacement_strength * 0.40 if displacement_valid else 0)
    )

    raw_score = (base_score * 0.55) + (structure_score * 0.45)

    # ==========================================================
    # 🔥 FIX 2 — ENTRY QUALITY AS SCORE AMPLIFIER (NOT FILTER)
    # ==========================================================
    eq = entry_quality(
        displacement_strength,
        sweep_up,
        sweep_down,
        bos_up,
        bos_down,
        choch_up,
        choch_down,
        vol,
        bias,
        raw_score
    )

    # NUEVO: score efectivo dinámico (CLAVE DEL FIX)
    sc = raw_score * (0.65 + (eq * 0.50))

    # bias boost leve (no dominante)
    if bias != "NEUTRAL":
        sc += 0.03

    sc = max(0.0, min(sc, 1.0))


    # ==========================================================
    # FIX 3 — SIGNAL LAYER PROBABILÍSTICO (NO BINARIO)
    # ==========================================================
    min_expansion = 0.78
    min_trend = 0.82

    threshold = min_expansion if regime == "EXPANSION" else min_trend

    alignment_ok = (
        (bias == "BULLISH" and tr_dir == 1) or
        (bias == "BEARISH" and tr_dir == -1)
    )

    force_trade = (sc > 0.92 and eq > 0.75 and alignment_ok)

    # cooldown ya no bloquea completamente, solo reduce agresividad
    if cooldown_manager.get_cooldown() > 0:
        sc *= 0.92

    if force_trade:
        signal = "BUY" if bias == "BULLISH" else "SELL"
    elif sc > threshold and eq > 0.45 and alignment_ok:
        signal = generate_signal(
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
    else:
        signal = "WAIT"


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
    save_state({
        "balance": balance,
        "peak": peak,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "total_trades": total_trades,
        "bias_memory": bias_memory,
        "cooldown": cooldown_manager.get_cooldown()
    })


    # =====================
    # RISK CONTROL
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
            f"RAW:{raw_score:.2f} "
            f"REGIME:{regime} "
            f"BIAS:{bias}({bias_memory:.2f}) "
            f"EQ:{eq:.2f} "
            f"DISP:{displacement_strength:.2f} "
            f"SWEEP:{sweep_up}/{sweep_down} "
            f"SIGNAL:{signal} "
            f"BAL:{balance:.2f} "
            f"WR:{wr:.2f}"
        )
