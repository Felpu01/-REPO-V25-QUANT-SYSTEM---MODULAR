# main.py

import time
import random

from engine import detect_trend
from engine import detect_volatility
from engine import detect_liquidity_sweep

from structure import detect_bos
from structure import detect_choch

from strategy import institutional_score
from strategy import regime_logic

from execution import execute_trade
from risk import risk_management


print("🚀 V24 INSTITUTIONAL ENGINE STARTED")

# =========================
# INITIAL CAPITAL
# =========================

balance = 10000
peak_balance = balance
drawdown = 0

wins = 0
losses = 0
total_trades = 0

bar = 0

price = 1000

# =========================
# MAIN LOOP
# =========================

while True:

    bar += 1

    # =========================
    # MARKET SIMULATION
    # =========================

    move = random.uniform(-3, 3)
    price += move

    # =========================
    # ENGINES
    # =========================

    trend = detect_trend(price)

    volatility = detect_volatility()

    sweep = detect_liquidity_sweep()

    # =========================
    # STRUCTURE
    # =========================

    bos_up, bos_down = detect_bos()

    choch_up, choch_down = detect_choch()

    # =========================
    # MARKET REGIME
    # =========================

    regime = regime_logic(
        trend,
        sweep,
        volatility
    )

    # =========================
    # INSTITUTIONAL SCORE
    # =========================

    score = institutional_score(
        trend,
        volatility,
        sweep,
        regime
    )

    # =========================
    # SIGNAL ENGINE
    # =========================

    signal = "WAIT"

    # BUY CONDITIONS
    if (
        trend == "TREND_UP"
        and (bos_up or choch_up)
        and score >= 0.85
        and regime != "RANGING"
    ):

        signal = "BUY"

    # SELL CONDITIONS
    elif (
        trend == "TREND_DOWN"
        and (bos_down or choch_down)
        and score >= 0.85
        and regime != "RANGING"
    ):

        signal = "SELL"

    # =========================
    # EXECUTION ENGINE
    # =========================

    pnl = 0
    result = "NONE"

    if signal != "WAIT":

        pnl, result = execute_trade(signal)

        balance += pnl

        total_trades += 1

        if result == "WIN":
            wins += 1

        else:
            losses += 1

    # =========================
    # RISK MANAGEMENT
    # =========================

    peak_balance, drawdown = risk_management(
        balance,
        peak_balance
    )

    # =========================
    # WINRATE
    # =========================

    if total_trades > 0:
        winrate = round((wins / total_trades) * 100, 2)

    else:
        winrate = 0

    # =========================
    # LOGS
    # =========================

    print(
        f"BAR:{bar} | "
        f"PRICE:{round(price,2)} | "
        f"TREND:{trend} | "
        f"VOL:{volatility} | "
        f"SWEEP:{sweep} | "
        f"BOS↑:{bos_up} | "
        f"BOS↓:{bos_down} | "
        f"CHOCH↑:{choch_up} | "
        f"CHOCH↓:{choch_down} | "
        f"SCORE:{score} | "
        f"REGIME:{regime} | "
        f"SIGNAL:{signal} | "
        f"RESULT:{result} | "
        f"PNL:{pnl} | "
        f"BAL:{round(balance,2)} | "
        f"WR:{winrate}% | "
        f"DD:{drawdown}%"
    )

    time.sleep(1)
