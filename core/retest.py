def retest_entry(
    price,
    prev_high,
    prev_low,
    bias
):

    tolerance = 15

    bullish_retest = (
        bias == "BULLISH"
        and abs(price - prev_high) <= tolerance
    )

    bearish_retest = (
        bias == "BEARISH"
        and abs(price - prev_low) <= tolerance
    )

    return bullish_retest, bearish_retest
