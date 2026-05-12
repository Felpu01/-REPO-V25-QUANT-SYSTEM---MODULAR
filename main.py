import random
import time

from config import BALANCE_START

from engine import market_engine

from strategy import institutional_score
from strategy import regime_logic

from risk import RiskManager

from execution import ExecutionEngine


print("🚀 V23 HEDGE FUND ENGINE STARTED")


price = 1000
prev_price = price

bar = 0

wins = 0
losses = 0
trades = 0

risk = RiskManager(BALANCE_START)

execution = ExecutionEngine()


while True:

    bar += 1

    # MARKET SIMULATION
    move = random.uniform(-3, 3)

    price += move

    trend, sweep, volatility = market_engine(
        price,
        prev_price
    )

    score = institutional_score(
        trend,
        sweep,
        volatility
    )

    regime = regime_logic(
        trend,
        sweep,
        volatility
    )

    execution.step_cooldown()

    signal = execution.signal(
        score,
        regime
    )

    pnl = 0

    # EXECUTION
    if signal == "BUY":

        trades += 1

        pnl = random.uniform(-15, 30)

        if pnl > 0:
            wins += 1
        else:
            losses += 1

    elif signal == "SELL":

        trades += 1

        pnl = random.uniform(-15, 30)

        if pnl > 0:
            wins += 1
        else:
            losses += 1

    risk.update_balance(pnl)

    drawdown = risk.drawdown()

    if trades > 0:
        winrate = round(wins / trades, 2)
    else:
        winrate = 0

    print(
        f"BAR:{bar} | "
        f"PRICE:{round(price,2)} | "
        f"TREND:{trend} | "
        f"VOL:{volatility} | "
        f"SWEEP:{sweep} | "
        f"SCORE:{score} | "
        f"SIGNAL:{signal} | "
        f"PNL:{round(pnl,2)} | "
        f"BAL:{round(risk.balance,2)} | "
        f"WR:{winrate} | "
        f"DD:{drawdown}"
    )

    # HARD RISK STOP
    if not risk.can_trade():

        print("\n🛑 MAX DRAWDOWN HIT")
        print("🚫 TRADING DISABLED")

        break

    prev_price = price

    time.sleep(0.5)
