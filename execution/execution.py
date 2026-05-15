from execution.position_manager import PositionManager

pm = PositionManager()

# =========================
# EXECUTION ENGINE
# =========================
def execute(
    signal,
    price,
    atr,
    score,
    balance
):

    # =====================
    # VALID SIGNAL
    # =====================
    if signal not in ["BUY", "SELL"]:
        return None

    # =====================
    # POSITION LOCK (FIXED)
    # =====================
    if not pm.can_enter():
        print("⛔ POSITION BLOCKED")
        return None

    # =====================
    # QUALITY FILTER
    # =====================
    if score < 0.80:
        return None

    # =====================
    # VOLATILITY FILTER
    # =====================
    effective_atr = max(atr, 8)

    if effective_atr < 6:
        print("⛔ LOW VOLATILITY")
        return None

    if effective_atr > 120:
        print("⛔ EXTREME VOLATILITY")
        return None

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
    # SL / TP LOGIC
    # =====================
    sl_distance = effective_atr * 1.8
    tp_distance = effective_atr * 3.8

    if signal == "BUY":
        sl = price - sl_distance
        tp = price + tp_distance
    else:
        sl = price + sl_distance
        tp = price - tp_distance

    # =====================
    # RISK REWARD CHECK
    # =====================
    rr = tp_distance / sl_distance

    if rr < 1.8:
        print("⛔ LOW RR")
        return None

    # =====================
    # OPEN POSITION (STATE MACHINE CONTROLLED)
    # =====================
    opened = pm.open_position(
        side=signal,
        entry=price,
        sl=sl,
        tp=tp,
        score=score
    )

    if not opened:
        return None

    return {
        "signal": signal,
        "entry": price,
        "sl": sl,
        "tp": tp,
        "risk": risk_amount,
        "rr": rr,
        "score": score
    }


# =========================
# UPDATE OPEN POSITION
# =========================
def update_positions(price):
    return pm.update(price)


# =========================
# GET POSITION
# =========================
def get_position():
    return pm.position
