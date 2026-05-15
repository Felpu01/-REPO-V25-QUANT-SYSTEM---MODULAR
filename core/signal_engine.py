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
    displacement_valid,
    volatility
):

    # =========================
    # SCORE ZONES (V35.5 AJUSTE)
    # =========================
    strong = score >= 0.60
    mid = score >= 0.48
    weak = score >= 0.40

    bullish_bias = bias == "BULLISH"
    bearish_bias = bias == "BEARISH"

    structure_bull = bos_up or choch_up
    structure_bear = bos_down or choch_down

    liquidity_bull = sweep_down
    liquidity_bear = sweep_up

    market_ok = regime in ["TREND", "EXPANSION"]

    # 🔥 FIX CLAVE: más realista (evita bloqueo excesivo)
    momentum_ok = volatility > 0.35

    # =========================
    # FILTRO BASE
    # =========================
    if not market_ok:
        return "WAIT"

    if not momentum_ok:
        return "WAIT"

    # =========================
    # CONFLUENCIA FUERTE (OVERRIDE INSTITUCIONAL)
    # =========================
    strong_confluence = (
        score >= 0.80 and
        bias != "NEUTRAL" and
        (structure_bull or structure_bear or displacement_valid)
    )

    if strong_confluence:
        return "BUY" if bias == "BULLISH" else "SELL"

    # =========================
    # BUY LOGIC
    # =========================
    if strong and bullish_bias:
        if structure_bull or liquidity_bull or displacement_valid:
            return "BUY"

    if mid and bullish_bias:
        if structure_bull or liquidity_bull:
            return "BUY"

    if weak and bullish_bias and bias_memory > 0.4:
        if structure_bull or displacement_valid:
            return "BUY"

    # =========================
    # SELL LOGIC
    # =========================
    if strong and bearish_bias:
        if structure_bear or liquidity_bear or displacement_valid:
            return "SELL"

    if mid and bearish_bias:
        if structure_bear or liquidity_bear:
            return "SELL"

    if weak and bearish_bias and bias_memory < -0.4:
        if structure_bear or displacement_valid:
            return "SELL"

    return "WAIT"
