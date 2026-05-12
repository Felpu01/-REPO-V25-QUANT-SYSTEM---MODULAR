from config import SCORE_THRESHOLD
from config import COOLDOWN_BARS


class ExecutionEngine:

    def __init__(self):

        self.cooldown = 0

    def step_cooldown(self):

        if self.cooldown > 0:
            self.cooldown -= 1

    def reset_cooldown(self):

        self.cooldown = COOLDOWN_BARS

    def signal(self, score, regime):

        if self.cooldown > 0:
            return "WAIT"

        if score < SCORE_THRESHOLD:
            return "WAIT"

        if regime == "LONG_SETUP":

            self.reset_cooldown()

            return "BUY"

        if regime == "SHORT_SETUP":

            self.reset_cooldown()

            return "SELL"

        return "WAIT"
