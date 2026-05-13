def entry_confirmation(
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
    volatility
):

    # =========================
    # MOMENTUM CONFIRMATION
    # =========================
    momentum_ok = volatility > 0.50

    # =========================
    # BUY CONFIRMATION
    # =========================
    buy_confirmed = (

        regime in ["TREND", "EXPANSION"]

        and score >= 0.82

        and bias == "BULLISH"

        and bias_memory > 1.0

        and momentum_ok

        and (
            bos_up
            or choch_up
        )

        and sweep_down
    )

    # =========================
    # SELL CONFIRMATION
    # =========================
    sell_confirmed = (

        regime in ["TREND", "EXPANSION"]

        and score >= 0.90

        and bias == "BEARISH"

        and bias_memory < -1.0

        and momentum_ok

        and (
            bos_down
            or choch_down
        )

        and sweep_up
    )

    # =========================
    # FINAL SIGNAL
    # =========================
    if buy_confirmed:
        return "BUY"

    if sell_confirmed:
        return "SELL"

    return "WAIT"
