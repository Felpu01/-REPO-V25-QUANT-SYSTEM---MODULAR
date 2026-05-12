from config import RISK_PER_TRADE
from config import MAX_DRAWDOWN


class RiskManager:

    def __init__(self, balance):

        self.balance = balance
        self.initial_balance = balance
        self.max_balance = balance

    def position_size(self):

        return round(self.balance * RISK_PER_TRADE, 2)

    def update_balance(self, pnl):

        self.balance += pnl

        if self.balance > self.max_balance:
            self.max_balance = self.balance

    def drawdown(self):

        dd = (
            (self.max_balance - self.balance)
            / self.max_balance
        )

        return round(dd, 4)

    def can_trade(self):

        return self.drawdown() < MAX_DRAWDOWN
