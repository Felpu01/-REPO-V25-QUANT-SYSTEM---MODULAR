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
    # NO POSITION
    # =====================
    if signal not in ["BUY", "SELL"]:
        return None

    if pm.has_position():
        return None

    # =====================
    # RISK MODEL
    # =====================
    risk_percent = 0.01

    risk_amount = balance * risk_percent

    # =====================
    # VOLATILITY FLOOR
    # =====================
    effective_atr = max(atr, 8)

    # =====================
    # DYNAMIC SL/TP
    # =====================
    sl_distance = effective_atr * 1.8

    tp_distance = effective_atr * 3.5

    # =====================
    # BUY
    # =====================
    if signal == "BUY":

        sl = price - sl_distance

        tp = price + tp_distance

    # =====================
    # SELL
    # =====================
    else:

        sl = price + sl_distance

        tp = price - tp_distance

    # =====================
    # OPEN POSITION
    # =====================
    pm.open_position(
        signal=signal,
        entry=price,
        sl=sl,
        tp=tp,
        risk=risk_amount,
        score=score
    )

    return {
        "signal": signal,
        "entry": price,
        "sl": sl,
        "tp": tp,
        "risk": risk_amount
    }


# =========================
# UPDATE OPEN POSITION
# =========================
def update_positions(price):

    return pm.update_position(price)
