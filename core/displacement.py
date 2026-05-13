def displacement(prices, i):

    if i < 5:
        return False, 0.0

    current = prices[i]
    prev = prices[i - 1]

    move = abs(current - prev)

    recent_moves = []

    for j in range(i - 5, i):

        candle_move = abs(prices[j] - prices[j - 1])
        recent_moves.append(candle_move)

    avg_move = sum(recent_moves) / len(recent_moves)

    displacement_strength = 0.0

    if avg_move > 0:
        displacement_strength = move / avg_move

    displacement_valid = displacement_strength > 1.5

    return displacement_valid, displacement_strength
