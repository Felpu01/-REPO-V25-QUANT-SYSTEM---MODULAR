class CooldownManager:

    def __init__(self):

        self.cooldown = 0

        self.consecutive_losses = 0

    # =========================
    # REGISTER TRADE RESULT
    # =========================
    def register_trade(self, result):

        # =====================
        # LOSS
        # =====================
        if result == "LOSS":

            self.consecutive_losses += 1

            # =====================
            # DYNAMIC COOLDOWN
            # =====================
            if self.consecutive_losses == 1:

                self.cooldown = 5

            elif self.consecutive_losses == 2:

                self.cooldown = 12

            else:

                self.cooldown = 20

            print(
                f"🧊 COOLDOWN ACTIVATED: "
                f"{self.cooldown}"
            )

        # =====================
        # WIN
        # =====================
        elif result == "WIN":

            self.consecutive_losses = 0

            self.cooldown = 0

        # =====================
        # BREAK EVEN
        # =====================
        elif result == "BE":

            # No reset total
            # No punishment total

            # reduce slowly
            if self.consecutive_losses > 0:

                self.consecutive_losses -= 1

            self.cooldown = max(
                0,
                self.cooldown - 2
            )

    # =========================
    # UPDATE LOOP
    # =========================
    def update(self):

        if self.cooldown > 0:

            self.cooldown -= 1

    # =========================
    # STATUS
    # =========================
    def allowed_to_trade(self):

        return self.cooldown == 0

    # =========================
    # INFO
    # =========================
    def get_cooldown(self):

        return self.cooldown

    # =========================
    # LOSS STREAK
    # =========================
    def get_consecutive_losses(self):

        return self.consecutive_losses
