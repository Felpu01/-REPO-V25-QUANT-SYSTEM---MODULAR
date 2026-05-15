class SetupMemory:

    def __init__(self, ttl=5):
        self.active_setup = None
        self.ttl = ttl
        self.age = 0

    # =========================
    # UPDATE MEMORY
    # =========================
    def update(self, signal_data):

        if signal_data is None:
            self._decay()
            return None

        # =====================
        # CREATE NEW SETUP
        # =====================
        if self.active_setup is None:
            self.active_setup = signal_data
            self.age = 0
            return self.active_setup

        # =====================
        # VALIDATE CONTINUATION
        # =====================
        if self._is_still_valid(signal_data):
            self.age = 0
            self.active_setup = signal_data
        else:
            self._decay()

        return self.active_setup

    # =========================
    # VALIDATION LOGIC
    # =========================
    def _is_still_valid(self, signal_data):

        return (
            signal_data["regime"] in ["TREND", "EXPANSION"]
            and abs(signal_data["bias_memory"]) > 0.3
        )

    # =========================
    # DECAY
    # =========================
    def _decay(self):

        self.age += 1

        if self.age >= self.ttl:
            self.active_setup = None
            self.age = 0

    # =========================
    # GET ACTIVE
    # =========================
    def get(self):
        return self.active_setup
