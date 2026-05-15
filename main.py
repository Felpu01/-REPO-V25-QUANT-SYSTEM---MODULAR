class PositionManager:

    def __init__(self):

        self.position = None

        self.just_opened = False

    # =========================
    # OPEN POSITION
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

        # HARD LOCK
        if self.position is not None:
            return False

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
            "lowest_price": entry,
        }

        self.just_opened = True

        print(f"🚀 OPEN {signal}")
        print(f"ENTRY:{entry}")
        print(f"SL:{sl}")
        print(f"TP:{tp}")

        return True

    # =========================
    # UPDATE POSITION
    # =========================
    def update_position(self, price):

        # =====================
        # NO POSITION
        # =====================
        if self.position is None:
            return None

        # =====================
        # SKIP SAME CANDLE
        # =====================
        if self.just_opened:

            self.just_opened = False

            return None

        pos = self.position

        # =====================
        # TRACKING
        # =====================
        pos["highest_price"] = max(
            pos["highest_price"],
            price
        )

        pos["lowest_price"] = min(
            pos["lowest_price"],
            price
        )

        # =====================
        # BUY MANAGEMENT
        # =====================
        if pos["side"] == "BUY":

            profit = price - pos["entry"]

            tp_distance = (
                pos["tp"] - pos["entry"]
            )

            # =================
            # BREAK EVEN
            # =================
            if (
                profit >= tp_distance * 0.5
                and not pos["break_even"]
            ):

                pos["sl"] = pos["entry"]

                pos["break_even"] = True

                print("🟢 BREAK EVEN ACTIVATED")

            # =================
            # TRAILING
            # =================
            if pos["break_even"]:

                new_sl = (
                    price
                    - tp_distance * 0.25
                )

                if new_sl > pos["sl"]:

                    pos["sl"] = new_sl

            # =================
            # TAKE PROFIT
            # =================
            if price >= pos["tp"]:

                print("✅ TAKE PROFIT")

                result = {
                    "result": "WIN",
                    "pnl": pos["risk"] * 2
                }

                self.position = None

                return result

            # =================
            # STOP LOSS
            # =================
            if price <= pos["sl"]:

                if pos["break_even"]:

                    print("⚪ BREAK EVEN EXIT")

                    pnl = 0

                    result_type = "BE"

                else:

                    print("❌ STOP LOSS")

                    pnl = -pos["risk"]

                    result_type = "LOSS"

                result = {
                    "result": result_type,
                    "pnl": pnl
                }

                self.position = None

                return result

        # =====================
        # SELL MANAGEMENT
        # =====================
        elif pos["side"] == "SELL":

            profit = pos["entry"] - price

            tp_distance = (
                pos["entry"] - pos["tp"]
            )

            # =================
            # BREAK EVEN
            # =================
            if (
                profit >= tp_distance * 0.5
                and not pos["break_even"]
            ):

                pos["sl"] = pos["entry"]

                pos["break_even"] = True

                print("🟢 BREAK EVEN ACTIVATED")

            # =================
            # TRAILING
            # =================
            if pos["break_even"]:

                new_sl = (
                    price
                    + tp_distance * 0.25
                )

                if new_sl < pos["sl"]:

                    pos["sl"] = new_sl

            # =================
            # TAKE PROFIT
            # =================
            if price <= pos["tp"]:

                print("✅ TAKE PROFIT")

                result = {
                    "result": "WIN",
                    "pnl": pos["risk"] * 2
                }

                self.position = None

                return result

            # =================
            # STOP LOSS
            # =================
            if price >= pos["sl"]:

                if pos["break_even"]:

                    print("⚪ BREAK EVEN EXIT")

                    pnl = 0

                    result_type = "BE"

                else:

                    print("❌ STOP LOSS")

                    pnl = -pos["risk"]

                    result_type = "LOSS"

                result = {
                    "result": result_type,
                    "pnl": pnl
                }

                self.position = None

                return result

        return None

    # =========================
    # POSITION STATUS
    # =========================
    def has_position(self):

        return (
            self.position is not None
            and self.position["status"] == "OPEN"
        )

    # =========================
    # GET POSITION
    # =========================
    def get_position(self):

        return self.position
