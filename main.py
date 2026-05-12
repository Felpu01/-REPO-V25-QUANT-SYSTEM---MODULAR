from execution import execute_trade
import math

print("🚀 V24.3 INSTITUTIONAL SNIPER ENGINE STARTED")

balance = 10000.0
initial_balance = balance

win_count = 0
loss_count = 0
total_trades = 0

peak = balance
drawdown = 0.0


for bar in market_data:

    price = bar["price"]

    trend = bar["trend"]
    vol = bar["vol"]
    sweep = bar["sweep"]
    bos_up = bar["bos_up"]
    bos_down = bar["bos_down"]
    choch_up = bar["choch_up"]
    choch_down = bar["choch_down"]
    score = bar["score"]

    # =========================
    # SNIPER SIGNAL ENGINE (FIXED)
    # =========================

    buy_setup = (
        score >= 0.85 and
        (bos_up or choch_up or sweep) and
        trend != "TREND_DOWN"
    )

    sell_setup = (
        score >= 0.85 and
        (bos_down or choch_down or sweep) and
        trend != "TREND_UP"
    )

    if buy_setup:
        signal = "BUY"
    elif sell_setup:
        signal = "SELL"
    else:
        signal = "WAIT"


    risk_amount = balance * 0.01


    pnl = execute_trade(signal, price, risk_amount)

    # FIX CRÍTICO NAN/INF
    if pnl is None or math.isnan(pnl) or math.isinf(pnl):
        pnl = 0.0


    if signal != "WAIT":
        balance += pnl
        total_trades += 1

        if pnl > 0:
            win_count += 1
        else:
            loss_count += 1

        if balance > peak:
            peak = balance

        drawdown = (peak - balance) / peak * 100 if peak > 0 else 0


    winrate = (win_count / total_trades * 100) if total_trades > 0 else 0


    print(
        f"PRICE:{price:.2f} | TREND:{trend} | VOL:{vol} | "
        f"SWEEP:{sweep} | BOS↑:{bos_up} BOS↓:{bos_down} | "
        f"CHOCH↑:{choch_up} CHOCH↓:{choch_down} | "
        f"SCORE:{score:.2f} | SIGNAL:{signal} | "
        f"PNL:{pnl:.2f} | BAL:{balance:.2f} | "
        f"WR:{winrate:.1f} | DD:{drawdown:.2f}"
    )
