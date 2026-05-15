import json
import os


STATE_FILE = "runtime_state.json"


# =========================
# SAVE STATE
# =========================
def save_state(state):

    try:

        with open(STATE_FILE, "w") as f:

            json.dump(
                state,
                f,
                indent=4
            )

    except Exception as e:

        print(f"❌ SAVE ERROR: {e}")


# =========================
# LOAD STATE
# =========================
def load_state():

    if not os.path.exists(STATE_FILE):

        return None

    try:

        with open(STATE_FILE, "r") as f:

            return json.load(f)

    except Exception as e:

        print(f"❌ LOAD ERROR: {e}")

        return None
