from persistence import save_state
from persistence import load_state


class PositionManager:

    def __init__(self):

        self.position = None

        self.just_opened = False

        # =====================
        # LOAD RUNTIME POSITION
        # =====================
        runtime = load_state()

        if runtime:

            saved_position = runtime.get(
                "position"
            )

            if saved_position:

                self.position = saved_position

                print("♻️ POSITION RECOVERED")

    # =========================
    # SAVE POSITION STATE
    # =========================
    def persist_position(self):

        runtime = load_state()

        if runtime is None:

            runtime = {}

        runtime["position"] = self.position

        save_state(runtime)

    # =========================
    # CLEAR POSITION
    # =========================
    def clear_position(self):

        runtime = load_state()

        if runtime is None:

            runtime = {}

        runtime["position"] = None

        save_state(runtime)

        self.position = None

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

        # =====================
        # HARD LOCK
        # =====================
        if self.position is not None:

            print("⛔ POSITION ALREADY OPEN")

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

            # =====================
            # NEW INSTITUTIONAL DATA
            # =====================
            "partial_taken": False,

            "trailing_sl": sl,

            "max_profit": 0.0,
        }

        self.just_opened = True

        self.persist_position()

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

            profit = (
                price
                - pos["entry"]
            )

            tp_distance = (
                pos["tp"]
                - pos["entry"]
            )

            pos["max_profit"] = max(
                pos["max_profit"],
                profit
            )

            # =================
            # BREAK EVEN
            # =================
            if (
                profit >= tp_distance * 0.40
                and not pos["break_even"]
            ):

                pos["sl"] = (
                    pos["entry"]
                    + tp_distance * 0.05
                )

                pos["break_even"] = True

                self.persist_position()

                print("🟢 BREAK EVEN ACTIVATED")

            # =================
            # TRAILING
            # =================
            if pos["break_even"]:

                dynamic_trailing = max(
                    tp_distance * 0.20,
                    pos["max_profit"] * 0.35
                )

                new_sl = (
                    price
                    - dynamic_trailing
                )

                if new_sl > pos["sl"]:

                    pos["sl"] = new_sl

                    self.persist_position()

            # =================
            # TAKE PROFIT
            # =================
            if price >= pos["tp"]:

                print("✅ TAKE PROFIT")

                result = {
                    "result": "WIN",
                    "pnl": pos["risk"] * 2
                }

                self.clear_position()

                return result

            # =================
            # STOP LOSS
            # =================
            if price <= pos["sl"]:

                if pos["break_even"]:

                    print("⚪ TRAILING EXIT")

                    pnl = pos["risk"] * 0.35

                    result_type = "WIN"

                else:

                    print("❌ STOP LOSS")

                    pnl = -pos["risk"]

                    result_type = "LOSS"

                result = {
                    "result": result_type,
                    "pnl": pnl
                }

                self.clear_position()

                return result

        # =====================
        # SELL MANAGEMENT
        # =====================
        elif pos["side"] == "SELL":

            profit = (
                pos["entry"]
                - price
            )

            tp_distance = (
                pos["entry"]
                - pos["tp"]
            )

            pos["max_profit"] = max(
                pos["max_profit"],
                profit
            )

            # =================
            # BREAK EVEN
            # =================
            if (
                profit >= tp_distance * 0.40
                and not pos["break_even"]
            ):

                pos["sl"] = (
                    pos["entry"]
                    - tp_distance * 0.05
                )

                pos["break_even"] = True

                self.persist_position()

                print("🟢 BREAK EVEN ACTIVATED")

            # =================
            # TRAILING
            # =================
            if pos["break_even"]:

                dynamic_trailing = max(
                    tp_distance * 0.20,
                    pos["max_profit"] * 0.35
                )

                new_sl = (
                    price
                    + dynamic_trailing
                )

                if new_sl < pos["sl"]:

                    pos["sl"] = new_sl

                    self.persist_position()

            # =================
            # TAKE PROFIT
            # =================
            if price <= pos["tp"]:

                print("✅ TAKE PROFIT")

                result = {
                    "result": "WIN",
                    "pnl": pos["risk"] * 2
                }

                self.clear_position()

                return result

            # =================
            # STOP LOSS
            # =================
            if price >= pos["sl"]:

                if pos["break_even"]:

                    print("⚪ TRAILING EXIT")

                    pnl = pos["risk"] * 0.35

                    result_type = "WIN"

                else:

                    print("❌ STOP LOSS")

                    pnl = -pos["risk"]

                    result_type = "LOSS"

                result = {
                    "result": result_type,
                    "pnl": pnl
                }

                self.clear_position()

                return result

        # =====================
        # SAVE ACTIVE STATE
        # =====================
        self.persist_position()

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
