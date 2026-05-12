import random
from execution import execute_trade

balance = 10000.0
winrate = 0
drawdown = 0.0

def generate_signal(bar):
    score = bar["score"]
    bos_up = bar["bos_up"]
    bos_down = bar["bos_down"]
    choch_up = bar["choch_up"]
    choch_down = bar["choch_down"]
    sweep = bar["sweep"]

    # 🔥 LOGICA INSTITUCIONAL REAL
    if score >= 0.85 and sweep and (bos_up or choch_up):
        return "BUY"

    if score >= 0.85 and sweep and (bos_down or choch_down):
        return "SELL"

    return "WAIT"


def simulate_bar():
    price = round(random.uniform(990, 1010), 2)

    return {
        "price": price,
        "trend": random.choice(["TREND_UP", "TREND_DOWN"]),
        "vol": random.choice(["LOW", "MEDIUM", "HIGH"]),
        "sweep": random.choice([True, False]),
        "bos_up": random.choice([True, False]),
        "bos_down": random.choice([True, False]),
        "choch_up": random.choice([True, False]),
        "choch_down": random.choice([True, False]),
        "score": round(random.choice([0.15, 0.4, 0.5, 0.65, 0.85, 1.0]), 2)
    }


bar_id = 0

while True:
    bar_id += 1
    bar = simulate_bar()

    signal = generate_signal(bar)

    price = bar["price"]

    pnl = 0.0

    if signal != "WAIT":
        pnl = execute_trade(signal, price, balance)
        balance += pnl

    if pnl > 0:
        winrate += 1

    print(
        f"BAR:{bar_id} | PRICE:{price} | TREND:{bar['trend']} | VOL:{bar['vol']} "
        f"| SWEEP:{bar['sweep']} | BOS↑:{bar['bos_up']} | BOS↓:{bar['bos_down']} "
        f"| CHOCH↑:{bar['choch_up']} | CHOCH↓:{bar['choch_down']} "
        f"| SCORE:{bar['score']} | SIGNAL:{signal} "
        f"| PNL:{pnl} | BAL:{balance} | WR:{winrate}"
    )
