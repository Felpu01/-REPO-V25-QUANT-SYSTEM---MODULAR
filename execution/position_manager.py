import json
import os
from datetime import datetime

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

        # 🔥 AUTO RECOVERY ON START
        self._recover_lock_state()

    # =========================
    # AUTO RECOVERY (CRITICAL FIX)
    # =========================
    def _recover_lock_state(self):

        if self.position is None:
            self.lock = False
            return

        # if position exists but marked closed → unlock
        if self.position.get("state") == CLOSED:
            self.position = None
            self.lock = False

        # if corrupted state → reset
        if not isinstance(self.position, dict):
            self.position = None
            self.lock = False

        # safety: if no valid side → reset
        if self.position and "side" not in self.position:
            self.position = None
            self.lock = False

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
    # STATE CHECKS (FIXED)
    # =========================
    def has_position(self):
        return self.position is not None and self.position.get("state") != CLOSED

    def is_idle(self):
        return not self.has_position()

    # 🔥 FIX: lock NO bloquea si no hay posición real
    def can_enter(self):
        if self.position is None:
            self.lock = False
            return True

        if self.position.get("state") == CLOSED:
            self.lock = False
            self.position = None
            return True

        return False

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

        # SL
        if side == "BUY" and price <= sl:
            return self.close(price, "SL")

        if side == "SELL" and price >= sl:
            return self.close(price, "SL")

        # TP
        if side == "BUY" and price >= tp:
            return self.close(price, "TP")

        if side == "SELL" and price <= tp:
            return self.close(price, "TP")

        # BE
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

        # TRAILING
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
    # CLOSE POSITION (FIXED SAFE)
    # =========================
    def close(self, price, reason):

        if not self.position:
            return None

        entry = self.position["entry"]
        side = self.position["side"]

        pnl = (price - entry) if side == "BUY" else (entry - price)

        result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BE"

        closed = dict(self.position)

        closed.update({
            "state": CLOSED,
            "close_reason": reason,
            "close_price": price,
            "pnl": pnl,
            "result": result,
            "closed_at": str(datetime.utcnow())
        })

        self.position = None
        self.lock = False

        self.save_state()
        return closed

    # =========================
    # GET POSITION
    # =========================
    def get_position(self):
        return self.position
