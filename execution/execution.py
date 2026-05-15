from execution.position_manager import PositionManager

pm = PositionManager()


# =========================
# EXECUTION ENGINE (STABLE V34 FIX)
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
    # SCORE FILTER
    # =====================
    if score < 0.80:
        return {"status": "BLOCKED", "reason": "LOW_SCORE"}

    # =====================
    # ATR FILTER (CLEANED)
    # =====================
    atr = max(atr, 8)

    if atr < 6:
        return {"status": "BLOCKED", "reason": "LOW_VOL"}

    if atr > 120:
        return {"status": "BLOCKED", "reason": "HIGH_VOL"}

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
    # SL / TP MODEL
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
    # QUALITY FILTER
    # =====================
    if rr < 2.0:
        return {"status": "BLOCKED", "reason": "LOW_RR"}

    # =====================
    # OPEN POSITION (FIXED CALL)
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
