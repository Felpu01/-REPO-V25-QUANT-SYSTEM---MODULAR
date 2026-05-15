class PositionManager:

    def __init__(self):

        self.position = None

    # =========================
    # ABRIR POSICIÓN
    # =========================
    def open_position(
        self,
        signal,
        entry,
        sl,
        tp,
        risk,
        score
    ):

        if self.position is not None:
            return

        self.position = {
            "side": signal,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "risk": risk,
            "score": score,
            "status": "OPEN",
            "break_even": False,
            "trailing_active": False,
            "highest_price": entry,
            "lowest_price": entry
        }

        print(f"🚀 OPEN {signal}")
        print(f"ENTRY:{entry}")
        print(f"SL:{sl}")
        print(f"TP:{tp}")

    # =========================
    # GESTIÓN ACTIVA
    # =========================
    def update_position(self, price):

        if self.position is None:
            return None

        pos = self.position

        # =====================
        # TRACKING
        # =====================
        if price > pos["highest_price"]:
            pos["highest_price"] = price

        if price < pos["lowest_price"]:
            pos["lowest_price"] = price

        # =====================
        # BUY
        # =====================
        if pos["side"] == "BUY":

            profit = price - pos["entry"]

            # BREAK EVEN
            if (
                profit >= (pos["tp"] - pos["entry"]) * 0.5
                and not pos["break_even"]
            ):

                pos["sl"] = pos["entry"]

                pos["break_even"] = True

                print("🟢 BREAK EVEN ACTIVATED")

            # TRAILING
            if pos["break_even"]:

                new_sl = price - (
                    (pos["tp"] - pos["entry"]) * 0.2
                )

                if new_sl > pos["sl"]:
                    pos["sl"] = new_sl

            # TP
            if price >= pos["tp"]:

                print("✅ TAKE PROFIT")

                result = {
                    "result": "WIN",
                    "pnl": pos["risk"] * 2
                }

                self.position = None

                return result

            # SL
            if price <= pos["sl"]:

                print("❌ STOP LOSS")

                result = {
                    "result": "LOSS",
                    "pnl": -pos["risk"]
                }

                self.position = None

                return result

        # =====================
        # SELL
        # =====================
        if pos["side"] == "SELL":

            profit = pos["entry"] - price

            # BREAK EVEN
            if (
                profit >= (pos["entry"] - pos["tp"]) * 0.5
                and not pos["break_even"]
            ):

                pos["sl"] = pos["entry"]

                pos["break_even"] = True

                print("🟢 BREAK EVEN ACTIVATED")

            # TRAILING
            if pos["break_even"]:

                new_sl = price + (
                    (pos["entry"] - pos["tp"]) * 0.2
                )

                if new_sl < pos["sl"]:
                    pos["sl"] = new_sl

            # TP
            if price <= pos["tp"]:

                print("✅ TAKE PROFIT")

                result = {
                    "result": "WIN",
                    "pnl": pos["risk"] * 2
                }

                self.position = None

                return result

            # SL
            if price >= pos["sl"]:

                print("❌ STOP LOSS")

                result = {
                    "result": "LOSS",
                    "pnl": -pos["risk"]
                }

                self.position = None

                return result

        return None

    # =========================
    # STATUS
    # =========================
    def has_position(self):

        return self.position is not None
