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
    """
    V35 SIGNAL ENGINE
    ÚNICO CEREBRO DE DECISIÓN (BUY / SELL / WAIT)
    """

    # =========================
    # 1. MARKET FILTER
    # =========================
    market_ok = regime in ["TREND", "EXPANSION"]

    # en RANGE evitamos entradas agresivas
    if regime == "RANGE" and score < 0.88:
        return "WAIT"

    # =========================
    # 2. MOMENTUM FILTER
    # =========================
    momentum_ok = volatility > 0.50 and displacement_valid

    # =========================
    # 3. STRUCTURE CONFIRMATION
    # =========================
    structure_bull = bos_up or choch_up
    structure_bear = bos_down or choch_down

    structure_ok = structure_bull or structure_bear

    # =========================
    # 4. BIAS FILTER (INSTITUTIONAL MEMORY)
    # =========================
    bullish_bias = bias == "BULLISH" and bias_memory > 0.25
    bearish_bias = bias == "BEARISH" and bias_memory < -0.25

    strong_bullish_bias = bias_memory > 1.0
    strong_bearish_bias = bias_memory < -1.0

    # =========================
    # 5. SCORE ZONES (NO HARD BLOCK, SOLO CONTEXTO)
    # =========================
    strong = score >= 0.85
    mid = score >= 0.70
    weak = score >= 0.55

    # =========================
    # 6. LIQUIDITY CONTEXT
    # =========================
    liquidity_bull = sweep_down
    liquidity_bear = sweep_up

    # =========================
    # 7. BUY LOGIC (INSTITUTIONAL FLOW)
    # =========================
    if (
        market_ok
        and strong
        and bullish_bias
        and strong_bullish_bias
        and structure_bull
        and liquidity_bull
        and momentum_ok
    ):
        return "BUY"

    if (
        market_ok
        and mid
        and bullish_bias
        and structure_bull
        and liquidity_bull
    ):
        return "BUY"

    if (
        weak
        and strong_bullish_bias
        and bos_up
    ):
        return "BUY"

    # =========================
    # 8. SELL LOGIC (INSTITUTIONAL FLOW)
    # =========================
    if (
        market_ok
        and strong
        and bearish_bias
        and strong_bearish_bias
        and structure_bear
        and liquidity_bear
        and momentum_ok
    ):
        return "SELL"

    if (
        market_ok
        and mid
        and bearish_bias
        and structure_bear
        and liquidity_bear
    ):
        return "SELL"

    if (
        weak
        and strong_bearish_bias
        and bos_down
    ):
        return "SELL"

    # =========================
    # 9. DEFAULT
    # =========================
    return "WAIT"
