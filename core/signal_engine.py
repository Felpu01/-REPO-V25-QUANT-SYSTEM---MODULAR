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
    # ZONAS
    # =========================
    strong = score >= 0.75
    mid = score >= 0.60
    weak = score >= 0.50

    bullish_bias = bias == "BULLISH"
    bearish_bias = bias == "BEARISH"

    structure_bull = bos_up or choch_up
    structure_bear = bos_down or choch_down

    liquidity_bull = sweep_down
    liquidity_bear = sweep_up

    market_ok = regime in ["TREND", "EXPANSION"]

    momentum_ok = volatility > 0.40

    if not market_ok or not momentum_ok:
        return "WAIT"

    # =========================
    # FILTRO DE CONTEXTO (IMPORTANTE FIX)
    # =========================
    context_bull = structure_bull or liquidity_bull or displacement_valid
    context_bear = structure_bear or liquidity_bear or displacement_valid

    # =========================
    # BOOST DE BIAS (FIX CLAVE)
    # =========================
    bias_strong_bull = bullish_bias and bias_memory > 0.6
    bias_strong_bear = bearish_bias and bias_memory < -0.6

    # =========================
    # BUY
    # =========================
    if strong and bullish_bias and context_bull:
        return "BUY"

    if mid and bullish_bias and context_bull:
        return "BUY"

    if weak and bias_strong_bull and structure_bull:
        return "BUY"

    # =========================
    # SELL
    # =========================
    if strong and bearish_bias and context_bear:
        return "SELL"

    if mid and bearish_bias and context_bear:
        return "SELL"

    if weak and bias_strong_bear and structure_bear:
        return "SELL"

    return "WAIT"
