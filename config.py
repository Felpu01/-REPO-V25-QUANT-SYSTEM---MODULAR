# ============================================================
# CONFIG CENTRAL — SMC QUANT BOT
# GitHub → Railway → MetaAPI → MT5 Exness
# ============================================================

# ─── ACCOUNT ────────────────────────────────────────────────
META_API_TOKEN    = ""   # Variable de entorno: META_API_TOKEN
MT5_ACCOUNT_ID    = ""   # Variable de entorno: MT5_ACCOUNT_ID
ANTHROPIC_API_KEY = ""   # Variable de entorno: ANTHROPIC_API_KEY

# ─── ACTIVOS ────────────────────────────────────────────────
SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAUUSDm", "EURUSDm", "USTECm"]

# Configuración por activo: pip_size, atr_min, atr_max, spread_max
SYMBOL_CONFIG = {
    "BTCUSDm": {"pip": 1.0,    "atr_min": 50,   "atr_max": 3000, "spread_max": 30,  "digits": 2},
    "ETHUSDm": {"pip": 0.1,    "atr_min": 5,    "atr_max": 300,  "spread_max": 5,   "digits": 2},
    "XAUUSDm": {"pip": 0.01,   "atr_min": 3,    "atr_max": 80,   "spread_max": 0.5, "digits": 2},
    "EURUSDm": {"pip": 0.0001, "atr_min": 0.003,"atr_max": 0.04, "spread_max": 0.0002,"digits": 5},
    "USTECm": {"pip": 1.0,    "atr_min": 20,   "atr_max": 600,  "spread_max": 5,   "digits": 2},
}

# ─── TIMEFRAMES ─────────────────────────────────────────────
TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1"]

# Barras a cargar por timeframe
BARS = {
    "M1":  100,
    "M5":  200,
    "M15": 200,
    "H1":  300,
    "H4":  300,
    "D1":  500,
}

# ─── RIESGO ─────────────────────────────────────────────────
RISK_PER_TRADE    = 0.01   # 1% por trade
MAX_DAILY_DD      = 0.03   # 3% drawdown diario máximo
MAX_TOTAL_DD      = 0.08   # 8% drawdown total — frena el bot
MAX_SIMULTANEOUS  = 3      # máximo 3 posiciones abiertas
MIN_RR            = 2.5    # mínimo R:R 1:2.5
BALANCE_START     = 10000  # balance inicial (se sobreescribe con balance real)

# ─── SCORING ────────────────────────────────────────────────
SCORE_THRESHOLD   = 0.85   # mínimo score para ejecutar
EQ_THRESHOLD      = 0.70   # mínimo entry quality
AI_MIN_SCORE      = 75     # mínimo score IA (0-100)
FORCE_TRADE_SCORE = 0.94   # score para saltear cooldown

# ─── SMC ────────────────────────────────────────────────────
SWING_LOOKBACK    = 10     # velas para detectar swing highs/lows
BOS_LOOKBACK      = 50     # velas para BOS
FVG_MIN_PCT       = 0.0008 # tamaño mínimo FVG como % del precio
OB_LOOKBACK       = 60     # order blocks lookback
LIQ_TOLERANCE     = 0.0003 # tolerancia equal highs/lows

# ─── TIMEFRAME WEIGHTS (para score multi-tf) ────────────────
TF_WEIGHTS = {
    "D1":  0.30,
    "H4":  0.25,
    "H1":  0.20,
    "M15": 0.15,
    "M5":  0.07,
    "M1":  0.03,
}

# ─── SESIONES (UTC) ─────────────────────────────────────────
SESSIONS = {
    "london":      (7, 16),
    "new_york":    (13, 22),
    "ln_ny_overlap": (13, 16),
    "tokyo":       (0, 9),
    "sydney":      (21, 6),
}
TRADE_SESSIONS = ["london", "new_york", "ln_ny_overlap"]

# ─── COOLDOWN ───────────────────────────────────────────────
COOLDOWN_LOSS_1   = 5
COOLDOWN_LOSS_2   = 12
COOLDOWN_LOSS_3   = 25

# ─── LOOP ───────────────────────────────────────────────────
LOOP_INTERVAL     = 60     # segundos entre ciclos de análisis
DATA_REFRESH      = 30     # segundos entre refresh de datos

# ─── LOGGING ────────────────────────────────────────────────
LOG_LEVEL         = "INFO"
LOG_FILE          = "bot.log"
