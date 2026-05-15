from execution.position_manager import PositionManager

pm = PositionManager()

# =========================
# EXECUTION ENGINE
# =========================
def execute(signal, price, atr, score, balance):

    if signal not in ["BUY", "SELL"]:
        return {"status": "NO_SIGNAL"}

    if pm.has_position():
        return {"status": "BLOCKED", "reason": "POSITION_ALREADY_OPEN"}

    if score < 0.80:
        return {"status": "BLOCKED", "reason": "LOW_SCORE"}

    atr = max(atr, 8)

    if atr < 6:
        return {"status": "BLOCKED", "reason": "LOW_VOL"}

    if atr > 120:
        return {"status": "BLOCKED", "reason": "HIGH_VOL"}

    risk = balance * (0.015 if score >= 0.95 else 0.012 if score >= 0.90 else 0.01)

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

    # 🔥 FIX CLAVE: NO pasar signal como keyword
    opened = pm.open_position(
        price,
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


def get_position():
    return pm.get_position()
