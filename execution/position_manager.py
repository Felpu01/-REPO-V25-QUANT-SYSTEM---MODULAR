import json
import os
from datetime import datetime

# =========================
# STATES
# =========================
OPEN = "OPEN"
CLOSED = "CLOSED"
BREAK_EVEN = "BE"
TRAILING = "TRAILING"


class PositionManager:

    def __init__(self, persistence_file="position_state.json"):
        self.persistence_file = persistence_file
        self.position = None
        self.lock = False
        self.load_state()

    # =========================
    # PERSISTENCE
    # =========================
    def save_state(self):
        try:
            with open(self.persistence_file, "w") as f:
                json.dump({
                    "position": self.position,
                    "lock": self.lock,
                    "timestamp": str(datetime.utcnow())
                }, f, indent=4)
        except:
            pass

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

    def can_enter(self):
        return self.is_idle() and not self.lock

    # =========================
    # OPEN POSITION
    # =========================
    def open_position(self, side, entry, sl, tp, score):

        if not self.can_enter():
            return None

        self.position = {
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "score": score,
            "state": OPEN,
            "created_at": str(datetime.utcnow()),

            "break_even": False,
            "trailing": False
        }

        self.lock = True
        self.save_state()
        return self.position

    # =========================
    # UPDATE POSITION
    # =========================
    def update(self, price):

        if not self.position:
            return None

        side = self.position["side"]
        entry = self.position["entry"]
        sl = self.position["sl"]
        tp = self.position["tp"]

        # =====================
        # SL HIT
        # =====================
        if side == "BUY" and price <= sl:
            return self.close(price, "SL")

        if side == "SELL" and price >= sl:
            return self.close(price, "SL")

        # =====================
        # TP HIT
        # =====================
        if side == "BUY" and price >= tp:
            return self.close(price, "TP")

        if side == "SELL" and price <= tp:
            return self.close(price, "TP")

        # =====================
        # BREAK EVEN
        # =====================
        if not self.position["break_even"]:
            risk = abs(entry - sl)

            if side == "BUY" and price >= entry + risk:
                self.position["sl"] = entry
                self.position["break_even"] = True
                self.position["state"] = BREAK_EVEN

            if side == "SELL" and price <= entry - risk:
                self.position["sl"] = entry
                self.position["break_even"] = True
                self.position["state"] = BREAK_EVEN

        # =====================
        # TRAILING
        # =====================
        if self.position["break_even"]:

            risk = abs(entry - sl)

            if side == "BUY":
                new_sl = price - risk
                if new_sl > self.position["sl"]:
                    self.position["sl"] = new_sl
                    self.position["state"] = TRAILING

            if side == "SELL":
                new_sl = price + risk
                if new_sl < self.position["sl"]:
                    self.position["sl"] = new_sl
                    self.position["state"] = TRAILING

        self.save_state()
        return self.position

    # =========================
    # CLOSE POSITION (FIX REAL)
    # =========================
    def close(self, price, reason):

        entry = self.position["entry"]
        side = self.position["side"]

        # =====================
        # PNL CALCULATION
        # =====================
        if side == "BUY":
            pnl = price - entry
        else:
            pnl = entry - price

        # =====================
        # RESULT CLASSIFICATION
        # =====================
        if pnl > 0:
            result = "WIN"
        elif pnl < 0:
            result = "LOSS"
        else:
            result = "BE"

        closed = self.position

        closed.update({
            "state": CLOSED,
            "close_reason": reason,
            "closed_at": str(datetime.utcnow()),
            "close_price": price,
            "pnl": pnl,
            "result": result
        })

        # reset
        self.position = None
        self.lock = False

        self.save_state()

        return closed

    # =========================
    # GET POSITION
    # =========================
    def get_position(self):
        return self.position
