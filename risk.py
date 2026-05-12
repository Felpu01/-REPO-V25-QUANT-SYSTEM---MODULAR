# risk.py

import time


def risk_management(drawdown):

    if drawdown >= 0.10:

        print("🛑 MAX DRAWDOWN HIT")
        print("⏸ COOLING DOWN...")

        time.sleep(5)

        return False

    return True
