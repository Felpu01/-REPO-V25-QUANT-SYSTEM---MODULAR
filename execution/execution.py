from execution.position_manager import PositionManager

pm = PositionManager()

# =========================
# EXECUTION ENGINE
# =========================
def execute(signal, price, atr, score, balance):

    # =====================
    # VALID SIGNAL
    # =====================
    if signal not in ["BUY", "SELL"]:
        return {"status": "NO_SIGNAL"}

    # =====================
    # SINGLE POSITION LOCK
    # =====================
    if pm.has_position():
        return {"status": "BLOCKED", "reason": "POSITION_ALREADY_OPEN"}

    # =====================
    # QUALITY FILTER
    # =====================
    if score < 0.65:
        return {"status": "BLOCKED", "reason": "LOW_SCORE"}

    # =====================
    # VOLATILITY FILTER
    # =====================
    atr = max(atr, 5)

    if atr < 4:
        return {"status": "BLOCKED", "reason": "LOW_VOL"}

    if atr > 200:
        return {"status": "BLOCKED", "reason": "HIGH_VOL"}

    # =====================
    # RISK MODEL
    # =====================
    risk = balance * (
        0.015 if score >= 0.95 else
        0.012 if score >= 0.90 else
        0.01
    )

    # =====================
    # SL / TP
    # =====================
    sl_dist = atr * 1.8
    tp_dist = atr * 3.2

    if signal == "BUY":
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist

    rr = tp_dist / sl_dist

    # =====================
    # RR FILTER
    # =====================
    if rr < 1.3:
        return {"status": "BLOCKED", "reason": "LOW_RR"}

    # =====================
    # OPEN POSITION (FIXED CONTRACT)
    # =====================
    opened = pm.open_position(
        side=signal,
        entry=price,
        sl=sl,
        tp=tp,
        risk=risk,
        score=score
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
