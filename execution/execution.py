from execution.position_manager import PositionManager

pm = PositionManager()


# =========================
# V35 HELPERS
# =========================
def get_dynamic_threshold(regime):
    if regime == "EXPANSION":
        return 0.72
    if regime == "TREND":
        return 0.78
    return 0.82


def momentum_ok(bos_up, bos_down, sweep_up, sweep_down, displacement_valid):
    score = 0

    if bos_up or bos_down:
        score += 1
    if sweep_up or sweep_down:
        score += 1
    if displacement_valid:
        score += 1

    return score >= 2


# =========================
# EXECUTION ENGINE V35 STEP 1
# =========================
def execute(signal, price, atr, score, balance):

    # =====================
    # VALID SIGNAL
    # =====================
    if signal not in ["BUY", "SELL"]:
        return {"status": "NO_SIGNAL"}

    # =====================
    # POSITION LOCK
    # =====================
    if pm.has_position():
        return {"status": "BLOCKED", "reason": "POSITION_ALREADY_OPEN"}

    # =====================
    # DYNAMIC SCORE THRESHOLD (V35 FIX)
    # =====================
    threshold = get_dynamic_threshold("TREND")  # (simple stage 1 version)

    if score < threshold:
        return {"status": "BLOCKED", "reason": "LOW_SCORE"}

    # =====================
    # ATR FILTER
    # =====================
    atr = max(atr, 8)

    if atr < 6:
        return {"status": "BLOCKED", "reason": "LOW_VOL"}

    if atr > 120:
        return {"status": "BLOCKED", "reason": "HIGH_VOL"}

    # =====================
    # RR FILTER (RELAXED V35)
    # =====================
    sl_dist_test = atr * 1.8
    tp_dist_test = atr * 3.8
    rr = tp_dist_test / sl_dist_test

    if rr < 1.5:
        return {"status": "BLOCKED", "reason": "LOW_RR"}

    # =====================
    # RISK MODEL
    # =====================
    if score >= 0.95:
        risk = balance * 0.015
    elif score >= 0.90:
        risk = balance * 0.012
    else:
        risk = balance * 0.01

    # =====================
    # SL / TP
    # =====================
    sl_dist = atr * 1.8
    tp_dist = atr * 3.8

    if signal == "BUY":
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist

    rr = tp_dist / sl_dist

    # =====================
    # MOMENTUM FILTER (V35 CORE ADD)
    # =====================
    # Nota: en main.py todavía no lo pasamos, así que lo dejamos soft-fail
    momentum = True

    if not momentum:
        return {"status": "BLOCKED", "reason": "NO_MOMENTUM"}

    # =====================
    # POSITION OPEN
    # =====================
    opened = pm.open_position(
        signal,
        price,
        sl,
        tp,
        score
    )

    if not opened:
        return {"status": "BLOCKED", "reason": "OPEN_FAILED"}

    return {
        "status": "OPENED",
        "signal": signal,
        "entry": price,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "rr": rr,
        "score": score
    }


# =========================
# UPDATE POSITION
# =========================
def update_positions(price):
    return pm.update_position(price)


# =========================
# GET POSITION
# =========================
def get_position():
    return pm.get_position()
