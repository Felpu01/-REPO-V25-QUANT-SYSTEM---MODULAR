# main.py

import time
import random

from engine import detect_trend
from engine import detect_volatility
from engine import detect_liquidity_sweep

from strategy import institutional_score
from strategy import regime_logic

from execution import execute_trade
from risk import risk_management

from structure import detect_structure

print("🚀 V24 INSTITUTIONAL ENGINE STARTED")

balance = 10000
peak_balance = balance

wins = 0
losses = 0

price = 1000
bar = 0

price_history = []

while True:

    bar += 1

    # SIMULACIÓN PRECIO
    price += random.uniform(-3, 3)

    # HISTORIAL
    price_history.append(price)

    if len(price_history) > 100:
        price_history.pop(0)

    # ENGINE
    trend = detect_trend()

    volatility = detect_volatility()

    sweep = detect_liquidity_sweep()

    # STRUCTURE ENGINE
    structure = detect_structure(price_history)

    bos_bullish = structure["bos_bullish"]
    bos_bearish = structure["bos_bearish"]

    choch_bullish = structure["choch_bullish"]
    choch_bearish = structure["choch_bearish"]

    # REGIME
    regime = regime_logic(
        trend,
        sweep,
        volatility
    )

    # SCORE
    score = institutional_score(
        trend,
        volatility,
        sweep,
        regime
    )

    # SIGNALS
    signal = "WAIT"

    if (
        score >= 0.90
        and bos_bullish
        and choch_bullish
        and trend == "TREND_UP"
    ):
        signal = "BUY"

    elif (
        score >= 0.90
        and bos_bearish
        and choch_bearish
        and trend == "TREND_DOWN"
    ):
        signal = "SELL"

    # EXECUTION
    pnl = 0

    if signal != "WAIT":

        pnl = execute_trade(signal)

        balance += pnl

        if pnl > 0:
            wins += 1
        else:
            losses += 1

    # DRAWDOWN
    if balance > peak_balance:
        peak_balance = balance

    drawdown = round(
        (peak_balance - balance) / peak_balance,
        4
    )

    # WINRATE
    total = wins + losses

    if total > 0:
        winrate = round(wins / total, 2)
    else:
        winrate = 0

    # LOGS
    print(
        f"BAR:{bar} "
        f"| PRICE:{round(price,2)} "
        f"| TREND:{trend} "
        f"| VOL:{volatility} "
        f"| SWEEP:{sweep} "
        f"| BOS↑:{bos_bullish} "
        f"| BOS↓:{bos_bearish} "
        f"| CHOCH↑:{choch_bullish} "
        f"| CHOCH↓:{choch_bearish} "
        f"| SCORE:{score} "
        f"| SIGNAL:{signal} "
        f"| PNL:{round(pnl,2)} "
        f"| BAL:{round(balance,2)} "
        f"| WR:{winrate} "
        f"| DD:{drawdown}"
    )

    # RISK MANAGEMENT
    risk_management(drawdown)

    time.sleep(1)
