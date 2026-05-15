# core/signal_engine.py

def generate_signal(
    regime,
    score,
    bias,
    bias_memory,
    bos_up,
    bos_down,
    choch_up,
    choch_down,
    sweep_up,
    sweep_down,
    displacement_valid
):

    # =========================
    # SCORE ZONES (SIMPLIFICADO)
    # =========================
    strong = score >= 0.78
    mid = score >= 0.65
    weak = score >= 0.55

    bullish_bias = bias == "BULLISH"
    bearish_bias = bias == "BEARISH"

    structure_bull = bos_up or choch_up
    structure_bear = bos_down or choch_down

    liquidity_bull = sweep_down
    liquidity_bear = sweep_up

    market_ok = regime in ["TREND", "EXPANSION"]

    # =========================
    # FILTRO BASE (OBLIGATORIO)
    # =========================
    if not market_ok:
        return "WAIT"

    # =========================
    # 🔥 ENTRY LOGIC V35.1
    # =========================

    # -------- BUY --------
    if strong and bullish_bias:

        if structure_bull or liquidity_bull or displacement_valid:
            return "BUY"

    if mid and bullish_bias and (structure_bull or liquidity_bull):
        return "BUY"

    if weak and bullish_bias and bias_memory > 0.8 and structure_bull:
        return "BUY"

    # -------- SELL --------
    if strong and bearish_bias:

        if structure_bear or liquidity_bear or displacement_valid:
            return "SELL"

    if mid and bearish_bias and (structure_bear or liquidity_bear):
        return "SELL"

    if weak and bearish_bias and bias_memory < -0.8 and structure_bear:
        return "SELL"

    # =========================
    # DEFAULT
    # =========================
    return "WAIT"
