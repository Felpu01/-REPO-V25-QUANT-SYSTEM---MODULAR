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


print("🚀 V24.1 INSTITUTIONAL ENGINE STARTED")

balance = 10000
peak_balance = balance

wins = 0
losses = 0
total_trades = 0

bar = 0
previous_price = 1000


while True:

    bar += 1

    # Simulación de precio
    price = round(previous_price + random.uniform(-3, 3), 2)

    # =========================
    # ENGINE CORE
    # =========================
    trend = detect_trend(price)
    volatility = detect_volatility()
    sweep = detect_liquidity_sweep()

    bos_up, bos_down = detect_bos(price, previous_price)
    choch_up, choch_down = detect_choch(trend, bos_up, bos_down)

    regime = regime_logic(trend, sweep, volatility)

    score = institutional_score(trend, volatility, sweep, regime)

    # =========================
    # SIGNAL LOGIC (FIXED)
    # =========================
    signal = "WAIT"

    # 🔥 FIX: no bloquear tanto el sistema
    if score >= 0.80 and regime in ["INSTITUTIONAL", "TRENDING", "LIQUIDITY_EVENT"]:

        if bos_up or choch_up:
            signal = "BUY"

        elif bos_down or choch_down:
            signal = "SELL"

    # =========================
    # EXECUTION FIX (IMPORTANTE)
    # =========================
    pnl = 0

    if signal != "WAIT":

        pnl = execute_trade(signal, price, balance)[0]

        balance += pnl

        total_trades += 1

        if pnl > 0:
            wins += 1
        else:
            losses += 1

    # =========================
    # RISK ENGINE
    # =========================
    peak_balance = max(peak_balance, balance)

    drawdown = round(((peak_balance - balance) / peak_balance) * 100, 2)

    winrate = round((wins / total_trades) * 100, 2) if total_trades > 0 else 0

    risk_management(drawdown)

    # =========================
    # OUTPUT
    # =========================
    print(
        f"BAR:{bar} | "
        f"PRICE:{price} | "
        f"TREND:{trend} | "
        f"VOL:{volatility} | "
        f"SWEEP:{sweep} | "
        f"BOS↑:{bos_up} | BOS↓:{bos_down} | "
        f"CHOCH↑:{choch_up} | CHOCH↓:{choch_down} | "
        f"REGIME:{regime} | "
        f"SCORE:{score} | "
        f"SIGNAL:{signal} | "
        f"PNL:{round(pnl,2)} | "
        f"BAL:{round(balance,2)} | "
        f"WR:{winrate} | "
        f"DD:{drawdown}"
    )

    previous_price = price
    time.sleep(1)
