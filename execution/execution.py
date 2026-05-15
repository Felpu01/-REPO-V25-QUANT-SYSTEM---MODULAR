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

    effective_atr = max(atr, 8)

    if effective_atr < 6:
        return {"status": "BLOCKED", "reason": "LOW_VOL"}

    if effective_atr > 120:
        return {"status": "BLOCKED", "reason": "HIGH_VOL"}

    # =====================
    # RISK MODEL
    # =====================
    if score >= 0.95:
        risk_percent = 0.015
    elif score >= 0.90:
        risk_percent = 0.012
    else:
        risk_percent = 0.01

    risk_amount = balance * risk_percent

    sl_distance = effective_atr * 1.8
    tp_distance = effective_atr * 3.8

    if signal == "BUY":
        sl = price - sl_distance
        tp = price + tp_distance
    else:
        sl = price + sl_distance
        tp = price - tp_distance

    rr = tp_distance / sl_distance

    if rr < 1.8:
        return {"status": "BLOCKED", "reason": "LOW_RR"}

    # =========================
    # OPEN POSITION (SAFE ADAPTER FIX)
    # =========================

    try:
        # 🔥 CASE 1: positional args (lo más común en tu bot)
        opened = pm.open_position(
            signal,
            price,
            sl,
            tp,
            risk_amount,
            score
        )

    except TypeError:
        try:
            # 🔥 CASE 2: dict-style API
            opened = pm.open_position({
                "signal": signal,
                "entry": price,
                "sl": sl,
                "tp": tp,
                "risk": risk_amount,
                "score": score
            })

        except Exception as e:
            return {
                "status": "BLOCKED",
                "reason": f"OPEN_ERROR: {str(e)}"
            }

    if not opened:
        return {"status": "BLOCKED", "reason": "OPEN_FAILED"}

    return {
        "status": "OPENED",
        "signal": signal,
        "entry": price,
        "sl": sl,
        "tp": tp,
        "risk": risk_amount,
        "rr": rr,
        "score": score
    }


# =========================
# UPDATE POSITIONS SAFE
# =========================
def update_positions(price):

    if hasattr(pm, "update_position"):
        return pm.update_position(price)

    if hasattr(pm, "update"):
        return pm.update(price)

    if hasattr(pm, "manage"):
        return pm.manage(price)

    return None


# =========================
# GET POSITION
# =========================
def get_position():
    return pm.get_position()
