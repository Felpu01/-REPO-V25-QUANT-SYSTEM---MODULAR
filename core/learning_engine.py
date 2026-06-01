"""
LEARNING ENGINE — Memoria Adaptativa Institucional
El bot aprende de cada trade, evoluciona continuamente
y auto-calibra sus parámetros para maximizar el win rate.

Fase 1 (backtest): los datos históricos se cargan desde backtest_learning.json
Fase 2 (live):     los trades reales se acumulan encima y refinan el aprendizaje
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import numpy as np

logger = logging.getLogger("LearningEngine")

MEMORY_FILE           = "trade_memory.json"
BACKTEST_LEARNING_FILE = "backtest_learning.json"

_learning_instance = None


# ─── Data structures ─────────────────────────────────────────

@dataclass
class TradeRecord:
    id: str
    symbol: str
    direction: str
    pattern: str
    entry: float
    sl: float
    tp: float
    score_at_entry: float
    smc_score: float
    momentum_score: float
    volatility_score: float
    session: str
    timeframe_bias: str
    sweep_detected: bool
    news_impact: str
    outcome: str = "pending"
    pnl_pct: float = 0.0
    rr_achieved: float = 0.0
    bars_in_trade: int = 0
    exit_reason: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return asdict(self)


@dataclass
class PatternStats:
    pattern: str
    total: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_rr: float = 0.0

    @property
    def win_rate(self):
        return self.wins / self.total if self.total > 0 else 0.0

    @property
    def expectancy(self):
        if self.total == 0:
            return 0.0
        return (self.win_rate * self.avg_rr) - (1 - self.win_rate)


@dataclass
class LearningInsights:
    total_trades: int = 0
    win_rate: float = 0.0
    avg_rr: float = 0.0
    best_pattern: str = ""
    worst_pattern: str = ""
    best_session: str = ""
    best_symbol: str = ""
    recommended_score_threshold: float = 0.85
    recommended_min_rr: float = 2.5
    pattern_stats: dict = field(default_factory=dict)
    symbol_stats: dict = field(default_factory=dict)
    session_stats: dict = field(default_factory=dict)
    evolution_level: int = 1
    confidence: str = "low"
    loaded_from_backtest: bool = False   # flag para saber el origen

    def to_dict(self):
        return {
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate * 100, 1),
            "avg_rr": round(self.avg_rr, 2),
            "best_pattern": self.best_pattern,
            "worst_pattern": self.worst_pattern,
            "best_session": self.best_session,
            "best_symbol": self.best_symbol,
            "score_threshold": self.recommended_score_threshold,
            "min_rr": self.recommended_min_rr,
            "evolution_level": self.evolution_level,
            "confidence": self.confidence,
            "loaded_from_backtest": self.loaded_from_backtest,
        }


# ─── Engine ──────────────────────────────────────────────────

class LearningEngine:
    def __init__(self):
        self._trades: list = []
        self._insights = LearningInsights()
        self._best_hours_by_symbol: dict = {}   # inyectado por BacktestInsights
        self._load_memory()
        logger.info(
            f"LearningEngine iniciado | {len(self._trades)} trades en memoria"
        )

    # ── Persistencia ─────────────────────────────────────────

    def _load_memory(self):
        """
        Orden de carga:
        1. trade_memory.json  (trades reales — máxima prioridad)
        2. backtest_learning.json  (histórico — solo si no hay suficientes reales)
        """
        real_loaded = self._load_trade_memory()

        # Cargar backtest insights si no hay suficiente historial real
        if not real_loaded or self._insights.total_trades < 20:
            self._try_load_backtest_insights()

    def _load_trade_memory(self) -> bool:
        """Carga trades reales desde trade_memory.json."""
        if not os.path.exists(MEMORY_FILE):
            return False
        try:
            with open(MEMORY_FILE, "r") as f:
                data = json.load(f)
            self._trades = [TradeRecord(**t) for t in data.get("trades", [])]
            self._recalculate_insights()
            logger.info(f"Memoria real cargada: {len(self._trades)} trades")
            return len(self._trades) > 0
        except Exception as e:
            logger.error(f"Error cargando trade_memory: {e}")
            return False

    def _try_load_backtest_insights(self):
        """
        Carga insights del backtest histórico si el archivo existe
        y hay datos confiables (>= 30 trades por símbolo).
        """
        if not os.path.exists(BACKTEST_LEARNING_FILE):
            logger.info("Sin backtest_learning.json — aprendizaje desde cero")
            return

        try:
            from core.backtest_insights import load_backtest_insights
            loaded = load_backtest_insights(self)
            if loaded:
                self._insights.loaded_from_backtest = True
        except Exception as e:
            logger.warning(f"BacktestInsights no disponible: {e}")

    def _save_memory(self):
        try:
            data = {
                "trades": [t.to_dict() for t in self._trades],
                "insights": self._insights.to_dict(),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            with open(MEMORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando memoria: {e}")

    # ── Registro de trades ────────────────────────────────────

    def record_trade_open(
        self, symbol, direction, pattern, entry, sl, tp,
        score, smc_score, momentum_score, volatility_score,
        session, timeframe_bias, sweep_detected=False,
        news_impact="none"
    ) -> str:
        trade_id = f"{symbol}_{int(datetime.now().timestamp())}"
        record = TradeRecord(
            id=trade_id, symbol=symbol, direction=direction,
            pattern=pattern, entry=entry, sl=sl, tp=tp,
            score_at_entry=score, smc_score=smc_score,
            momentum_score=momentum_score,
            volatility_score=volatility_score,
            session=session, timeframe_bias=timeframe_bias,
            sweep_detected=sweep_detected, news_impact=news_impact,
        )
        self._trades.append(record)
        self._save_memory()
        logger.info(f"Trade registrado: {trade_id} | {symbol} {direction}")
        return trade_id

    def record_trade_close(
        self, trade_id, outcome, pnl_pct,
        rr_achieved, bars_in_trade, exit_reason
    ):
        trade = next((t for t in self._trades if t.id == trade_id), None)
        if not trade:
            logger.warning(f"Trade {trade_id} no encontrado para cerrar")
            return
        trade.outcome       = outcome
        trade.pnl_pct       = pnl_pct
        trade.rr_achieved   = rr_achieved
        trade.bars_in_trade = bars_in_trade
        trade.exit_reason   = exit_reason
        self._recalculate_insights()
        self._save_memory()
        logger.info(
            f"Trade cerrado: {trade_id} | {outcome.upper()} | "
            f"PnL:{pnl_pct:.2f}% | RR:{rr_achieved:.2f}"
        )
        self._log_learning_update()

    # ── Recálculo de insights ─────────────────────────────────

    def _recalculate_insights(self):
        """
        Recalcula todos los insights a partir de los trades reales cerrados.
        Si hay insights del backtest cargados y pocos trades reales,
        los insights del backtest actúan como prior y los reales los refinan.
        """
        closed = [t for t in self._trades if t.outcome != "pending"]
        if not closed:
            return

        wins = [t for t in closed if t.outcome == "win"]
        self._insights.total_trades = len(closed)
        self._insights.win_rate     = len(wins) / len(closed)

        rrs = [t.rr_achieved for t in closed if t.rr_achieved > 0]
        self._insights.avg_rr = float(np.mean(rrs)) if rrs else 0.0

        # ── Patrones ──
        patterns: dict[str, PatternStats] = {}
        for t in closed:
            if t.pattern not in patterns:
                patterns[t.pattern] = PatternStats(pattern=t.pattern)
            ps = patterns[t.pattern]
            ps.total   += 1
            ps.losses  += 1
            if t.outcome == "win":
                ps.wins   += 1
                ps.losses -= 1
            ps.total_pnl += t.pnl_pct
            ps.avg_rr = (
                (ps.avg_rr * (ps.total - 1) + t.rr_achieved) / ps.total
            )

        self._insights.pattern_stats = {
            k: asdict(v) for k, v in patterns.items()
        }
        valid_pats = {k: v for k, v in patterns.items() if v.total >= 3}
        if valid_pats:
            best  = max(valid_pats.values(), key=lambda x: x.win_rate)
            worst = min(valid_pats.values(), key=lambda x: x.win_rate)
            self._insights.best_pattern  = best.pattern
            self._insights.worst_pattern = worst.pattern

        # ── Sesiones ──
        sessions: dict = {}
        for t in closed:
            if t.session not in sessions:
                sessions[t.session] = {"total": 0, "wins": 0}
            sessions[t.session]["total"] += 1
            if t.outcome == "win":
                sessions[t.session]["wins"] += 1
        self._insights.session_stats = sessions
        valid_ses = {k: v for k, v in sessions.items() if v["total"] >= 3}
        if valid_ses:
            best_s = max(
                valid_ses.items(),
                key=lambda x: x[1]["wins"] / x[1]["total"]
            )
            self._insights.best_session = best_s[0]

        # ── Símbolos ──
        symbols: dict = {}
        for t in closed:
            if t.symbol not in symbols:
                symbols[t.symbol] = {"total": 0, "wins": 0, "pnl": 0.0}
            symbols[t.symbol]["total"] += 1
            if t.outcome == "win":
                symbols[t.symbol]["wins"] += 1
            symbols[t.symbol]["pnl"] += t.pnl_pct
        self._insights.symbol_stats = symbols
        valid_sym = {k: v for k, v in symbols.items() if v["total"] >= 3}
        if valid_sym:
            best_sym = max(
                valid_sym.items(),
                key=lambda x: x[1]["wins"] / x[1]["total"]
            )
            self._insights.best_symbol = best_sym[0]

        self._auto_calibrate()
        self._insights.evolution_level = min(10, 1 + len(closed) // 20)
        self._insights.confidence = (
            "high"   if len(closed) >= 50 else
            "medium" if len(closed) >= 20 else
            "low"
        )

    def _auto_calibrate(self):
        """
        Ajusta threshold y min_rr dinámicamente según rendimiento real.
        Una vez que hay suficientes trades reales, estos tienen prioridad
        sobre el prior del backtest.
        """
        wr    = self._insights.win_rate
        total = self._insights.total_trades
        if total < 10:
            return

        current = self._insights.recommended_score_threshold
        if wr < 0.55:
            self._insights.recommended_score_threshold = min(0.95, current + 0.02)
            logger.info(
                f"Auto-calibración: WR {wr:.1%} → "
                f"threshold {self._insights.recommended_score_threshold:.2f}"
            )
        elif wr > 0.75 and total >= 30:
            self._insights.recommended_score_threshold = max(0.75, current - 0.01)

        if self._insights.avg_rr < 1.5 and total >= 10:
            self._insights.recommended_min_rr = min(
                4.0, self._insights.recommended_min_rr + 0.1
            )

    def _log_learning_update(self):
        i = self._insights
        logger.info(
            f"LEARNING | Trades:{i.total_trades} | WR:{i.win_rate:.1%} | "
            f"RR:{i.avg_rr:.2f} | Threshold:{i.recommended_score_threshold:.2f} | "
            f"Nivel:{i.evolution_level}/10"
        )

    # ── API pública ───────────────────────────────────────────

    def get_insights(self) -> LearningInsights:
        return self._insights

    def get_recommended_threshold(self) -> float:
        return self._insights.recommended_score_threshold

    def get_recommended_min_rr(self) -> float:
        return self._insights.recommended_min_rr

    def get_pattern_win_rate(self, pattern: str) -> float:
        stats = self._insights.pattern_stats.get(pattern, {})
        total = stats.get("total", 0)
        wins  = stats.get("wins", 0)
        return wins / total if total > 0 else 0.5

    def should_avoid_pattern(self, pattern: str) -> bool:
        wr    = self.get_pattern_win_rate(pattern)
        stats = self._insights.pattern_stats.get(pattern, {})
        return stats.get("total", 0) >= 10 and wr < 0.40

    def get_session_multiplier(self, session: str) -> float:
        stats = self._insights.session_stats.get(session, {})
        total = stats.get("total", 0)
        wins  = stats.get("wins", 0)
        if total < 5:
            return 1.0
        wr = wins / total
        if wr >= 0.70: return 1.15
        if wr >= 0.60: return 1.05
        if wr <= 0.40: return 0.85
        return 1.0

    def get_symbol_multiplier(self, symbol: str) -> float:
        stats = self._insights.symbol_stats.get(symbol, {})
        total = stats.get("total", 0)
        wins  = stats.get("wins", 0)
        if total < 5:
            return 1.0
        wr = wins / total
        if wr >= 0.70: return 1.10
        if wr >= 0.60: return 1.05
        if wr <= 0.40: return 0.90
        return 1.0

    def get_best_hours(self, symbol: str) -> list:
        """
        Retorna las mejores horas UTC para operar un símbolo
        según el backtest histórico. Lista vacía = no filtrar.
        """
        return self._best_hours_by_symbol.get(symbol, [])

    def is_good_hour(self, symbol: str, hour_utc: int) -> bool:
        """
        Verifica si la hora actual es una de las mejores para el símbolo.
        Si no hay datos del backtest, siempre retorna True.
        """
        best = self.get_best_hours(symbol)
        if not best:
            return True
        return hour_utc in best

    def get_evolution_summary(self) -> str:
        i = self._insights
        if i.total_trades == 0:
            return "Sin historial — aprendizaje comenzando"
        source = " [backtest]" if i.loaded_from_backtest else " [live]"
        return (
            f"Nivel {i.evolution_level}/10 | {i.total_trades} trades{source} | "
            f"WR: {i.win_rate:.1%} | Mejor patrón: {i.best_pattern} | "
            f"Mejor sesión: {i.best_session}"
        )


# ─── Singleton ───────────────────────────────────────────────

def get_learning_engine() -> LearningEngine:
    global _learning_instance
    if _learning_instance is None:
        _learning_instance = LearningEngine()
    return _learning_instance
