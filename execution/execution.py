from execution.position_manager import PositionManager

pm = PositionManager()


# =========================
# EXECUTION ENGINE (FIXED V34 STABLE)
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
    # ATR FILTER
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

    if rr < 1.8:
        return {"status": "BLOCKED", "reason": "LOW_RR"}

    # =====================
    # FIX CRÍTICO: MATCH EXACTO FIRMA POSITION MANAGER
    # =====================
    opened = pm.open_position(
        signal,   # side
        price,    # entry
        sl,
        tp,
        risk,
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
