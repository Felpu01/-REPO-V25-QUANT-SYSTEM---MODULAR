import json
import os
from datetime import datetime

# =========================
# POSITION STATES
# =========================
IDLE = "IDLE"
OPEN = "OPEN"
BREAK_EVEN = "BREAK_EVEN"
PARTIAL_TP = "PARTIAL_TP"
TRAILING = "TRAILING"
CLOSED = "CLOSED"


class PositionManager:
    def __init__(self, persistence_file="position_state.json"):
        self.persistence_file = persistence_file

        self.position = None
        self.lock = False

        self.load_state()

    # =========================
    # LOAD / SAVE PERSISTENCE
    # =========================
    def save_state(self):
        if self.position:
            data = {
                "position": self.position,
                "lock": self.lock,
                "timestamp": str(datetime.utcnow())
            }
            with open(self.persistence_file, "w") as f:
                json.dump(data, f, indent=4)

    def load_state(self):
        if os.path.exists(self.persistence_file):
            try:
                with open(self.persistence_file, "r") as f:
                    data = json.load(f)
                    self.position = data.get("position")
                    self.lock = data.get("lock", False)
            except:
                self.position = None
                self.lock = False

    # =========================
    # STATE CHECKS
    # =========================
    def has_position(self):
        return self.position is not None and self.position.get("state") != CLOSED

    def is_idle(self):
        return not self.has_position()

    # =========================
    # ENTRY CONTROL
    # =========================
    def can_enter(self):
        return not self.lock and self.is_idle()

    def open_position(self, side, entry, sl, tp, score):
        if not self.can_enter():
            return None

        self.position = {
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "state": OPEN,
            "score": score,
            "created_at": str(datetime.utcnow()),

            # management fields
            "break_even": False,
            "trailing": False,
            "partial_taken": False
        }

        self.lock = True
        self.save_state()

        return self.position

    # =========================
    # UPDATE PRICE ACTION
    # =========================
    def update(self, price):
        if not self.position:
            return None

        side = self.position["side"]
        sl = self.position["sl"]
        tp = self.position["tp"]
        entry = self.position["entry"]

        # =========================
        # STOP LOSS
        # =========================
        if side == "BUY" and price <= sl:
            return self.close("SL")

        if side == "SELL" and price >= sl:
            return self.close("SL")

        # =========================
        # TAKE PROFIT
        # =========================
        if side == "BUY" and price >= tp:
            return self.close("TP")

        if side == "SELL" and price <= tp:
            return self.close("TP")

        # =========================
        # BREAK EVEN LOGIC
        # =========================
        if not self.position["break_even"]:
            if side == "BUY" and price >= entry + abs(entry - sl):
                self.position["sl"] = entry
                self.position["state"] = BREAK_EVEN
                self.position["break_even"] = True

            if side == "SELL" and price <= entry - abs(entry - sl):
                self.position["sl"] = entry
                self.position["state"] = BREAK_EVEN
                self.position["break_even"] = True

        # =========================
        # TRAILING LOGIC (simple institutional)
        # =========================
        if self.position["break_even"]:
            if side == "BUY":
                new_sl = price - abs(entry - sl)
                if new_sl > self.position["sl"]:
                    self.position["sl"] = new_sl
                    self.position["state"] = TRAILING

            if side == "SELL":
                new_sl = price + abs(entry - sl)
                if new_sl < self.position["sl"]:
                    self.position["sl"] = new_sl
                    self.position["state"] = TRAILING

        self.save_state()
        return self.position

    # =========================
    # CLOSE POSITION
    # =========================
    def close(self, reason):
        closed_position = self.position

        closed_position["state"] = CLOSED
        closed_position["close_reason"] = reason
        closed_position["closed_at"] = str(datetime.utcnow())

        self.position = None
        self.lock = False

        self.save_state()

        return closed_position
