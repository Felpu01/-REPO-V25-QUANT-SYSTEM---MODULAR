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
        return {
            "status": "NO_SIGNAL"
        }

    # =====================
    # SINGLE POSITION LOCK
    # =====================
    if pm.has_position():
        return {
            "status": "BLOCKED",
            "reason": "POSITION_ALREADY_OPEN"
        }

    # =====================
    # QUALITY FILTER
    # =====================
    if score < 0.80:
        return {
            "status": "BLOCKED",
            "reason": "LOW_SCORE"
        }

    # =====================
    # VOLATILITY FILTER
    # =====================
    effective_atr = max(atr, 8)

    if effective_atr < 6:
        return {
            "status": "BLOCKED",
            "reason": "LOW_VOL"
        }

    if effective_atr > 120:
        return {
            "status": "BLOCKED",
            "reason": "HIGH_VOL"
        }

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

    # =====================
    # SL / TP
    # =====================
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
        return {
            "status": "BLOCKED",
            "reason": "LOW_RR"
        }

    # =====================
    # OPEN POSITION
    # =====================
    opened = pm.open_position(
        signal=signal,
        entry=price,
        sl=sl,
        tp=tp,
        risk=risk_amount,
        score=score
    )

    if not opened:
        return {
            "status": "BLOCKED",
            "reason": "OPEN_FAILED"
        }

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
# UPDATE POSITION
# =========================
def update_positions(price):
    return pm.update_position(price)


# =========================
# GET POSITION
# =========================
def get_position():
    return pm.get_position()
