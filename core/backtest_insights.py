"""
BACKTEST INSIGHTS LOADER
Traduce el backtest_learning.json generado por el BacktestEngine
al formato interno del LearningEngine, para que el bot arranque
la Fase 2 (live) ya calibrado con datos históricos reales.

Uso:
    from core.backtest_insights import load_backtest_insights
    load_backtest_insights(learning_engine_instance)
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("BacktestInsights")

BACKTEST_LEARNING_FILE = "backtest_learning.json"


def load_backtest_insights(engine) -> bool:
    """
    Carga los insights del backtest histórico en el LearningEngine.
    Solo se aplica si:
      - El archivo backtest_learning.json existe
      - El engine no tiene trades reales suficientes (< 20)
      - El backtest tiene datos confiables (>= 50 trades por símbolo)

    Retorna True si cargó datos, False si no había nada que cargar.
    """
    if not os.path.exists(BACKTEST_LEARNING_FILE):
        logger.info("BacktestInsights: sin archivo histórico — arrancando desde cero")
        return False

    try:
        with open(BACKTEST_LEARNING_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"BacktestInsights: error leyendo {BACKTEST_LEARNING_FILE}: {e}")
        return False

    # No sobreescribir si ya hay suficiente historial real
    real_trades = engine._insights.total_trades
    if real_trades >= 20:
        logger.info(
            f"BacktestInsights: {real_trades} trades reales en memoria — "
            f"no se sobreescribe con histórico"
        )
        return False

    symbols_data = data.get("symbols", {})
    if not symbols_data:
        logger.warning("BacktestInsights: archivo vacío o sin símbolos")
        return False

    generated_at = data.get("generated_at", "desconocido")
    logger.info(
        f"BacktestInsights: cargando insights históricos "
        f"(generados: {generated_at[:10]}) — {len(symbols_data)} símbolos"
    )

    # ── Construir pattern_stats consolidado ──────────────────
    pattern_stats = {}
    session_stats = {}
    symbol_stats  = {}
    best_hours_by_symbol = {}
    total_trades_all = 0
    total_wins_all   = 0

    for sym, sym_data in symbols_data.items():
        trades = sym_data.get("total_trades", 0)
        if trades < 30:
            logger.debug(f"  {sym}: solo {trades} trades históricos — saltando")
            continue

        win_rate = sym_data.get("win_rate", 0) / 100.0
        wins = int(trades * win_rate)
        total_trades_all += trades
        total_wins_all   += wins

        # Símbolo stats
        symbol_stats[sym] = {
            "total": trades,
            "wins":  wins,
            "pnl":   sym_data.get("profit_factor", 1.0),
        }

        # Mejores horas
        best_hours = sym_data.get("best_hours", [])
        if best_hours:
            best_hours_by_symbol[sym] = best_hours

        # Pattern stats — consolidar entre símbolos
        for pat, pst in sym_data.get("pattern_stats", {}).items():
            if pat not in pattern_stats:
                pattern_stats[pat] = {
                    "pattern": pat,
                    "total":   0,
                    "wins":    0,
                    "losses":  0,
                    "total_pnl": 0.0,
                    "avg_rr":  0.0,
                }
            ps = pattern_stats[pat]
            p_trades = pst.get("trades", 0)
            p_wr     = pst.get("wr", 0) / 100.0
            p_wins   = int(p_trades * p_wr)
            p_r      = pst.get("total_r", 0.0)
            ps["total"]     += p_trades
            ps["wins"]      += p_wins
            ps["losses"]    += p_trades - p_wins
            ps["total_pnl"] += p_r
            # running avg RR
            if ps["total"] > 0:
                ps["avg_rr"] = ps["total_pnl"] / ps["total"]

        # Session stats — consolidar entre símbolos
        for ses, sst in sym_data.get("session_stats", {}).items():
            if ses not in session_stats:
                session_stats[ses] = {"total": 0, "wins": 0}
            s_trades = sst.get("trades", 0)
            s_wr     = sst.get("wr", 0) / 100.0
            session_stats[ses]["total"] += s_trades
            session_stats[ses]["wins"]  += int(s_trades * s_wr)

        logger.info(
            f"  {sym}: WR:{win_rate*100:.1f}% | "
            f"Patrón:{sym_data.get('best_pattern','?')} | "
            f"Sesión:{sym_data.get('best_session','?')} | "
            f"Horas:{best_hours[:3] if best_hours else '?'}"
        )

    if total_trades_all == 0:
        logger.warning("BacktestInsights: ningún símbolo con suficientes trades")
        return False

    # ── Inyectar en el LearningEngine ────────────────────────
    ins = engine._insights

    # Stats base
    ins.total_trades   = total_trades_all
    ins.win_rate       = total_wins_all / total_trades_all
    ins.pattern_stats  = pattern_stats
    ins.session_stats  = session_stats
    ins.symbol_stats   = symbol_stats

    # Mejor patrón (mínimo 10 trades, mejor win rate)
    valid_patterns = {
        k: v for k, v in pattern_stats.items() if v["total"] >= 10
    }
    if valid_patterns:
        best_pat = max(
            valid_patterns.items(),
            key=lambda x: (x[1]["wins"] / x[1]["total"]) * x[1]["total"]
        )
        worst_pat = min(
            valid_patterns.items(),
            key=lambda x: x[1]["wins"] / x[1]["total"]
        )
        ins.best_pattern  = best_pat[0]
        ins.worst_pattern = worst_pat[0]

    # Mejor sesión
    valid_sessions = {
        k: v for k, v in session_stats.items() if v["total"] >= 10
    }
    if valid_sessions:
        best_ses = max(
            valid_sessions.items(),
            key=lambda x: x[1]["wins"] / x[1]["total"]
        )
        ins.best_session = best_ses[0]

    # Mejor símbolo
    valid_syms = {
        k: v for k, v in symbol_stats.items() if v["total"] >= 30
    }
    if valid_syms:
        best_sym = max(
            valid_syms.items(),
            key=lambda x: x[1]["wins"] / x[1]["total"]
        )
        ins.best_symbol = best_sym[0]

    # Nivel de evolución basado en cantidad de datos históricos
    ins.evolution_level = min(10, 1 + total_trades_all // 200)
    ins.confidence      = (
        "high"   if total_trades_all >= 500 else
        "medium" if total_trades_all >= 100 else
        "low"
    )

    # Auto-calibrar thresholds con datos históricos
    _calibrate_from_history(ins, valid_patterns)

    # Guardar horas óptimas como atributo extra (usado por score.py)
    engine._best_hours_by_symbol = best_hours_by_symbol

    # Guardar la memoria actualizada
    engine._save_memory()

    logger.info(
        f"✅ BacktestInsights cargado | "
        f"Trades históricos: {total_trades_all} | "
        f"WR: {ins.win_rate*100:.1f}% | "
        f"Mejor patrón: {ins.best_pattern} | "
        f"Mejor sesión: {ins.best_session} | "
        f"Nivel: {ins.evolution_level}/10"
    )
    return True


def _calibrate_from_history(ins, valid_patterns: dict):
    """
    Ajusta el score threshold y el min_RR recomendados
    según lo que funcionó históricamente.
    """
    wr = ins.win_rate

    # Si el WR histórico es bajo, subir el threshold (ser más selectivo)
    if wr < 0.50:
        ins.recommended_score_threshold = min(0.92, 0.85 + 0.04)
        logger.info(
            f"BacktestInsights: WR histórico bajo ({wr:.1%}) → "
            f"threshold ajustado a {ins.recommended_score_threshold:.2f}"
        )
    elif wr > 0.70:
        # Si el WR es alto, podemos ser un poco menos restrictivos
        ins.recommended_score_threshold = max(0.80, 0.85 - 0.02)
        logger.info(
            f"BacktestInsights: WR histórico alto ({wr:.1%}) → "
            f"threshold ajustado a {ins.recommended_score_threshold:.2f}"
        )

    # Ajustar min RR según el avg_rr histórico
    if valid_patterns:
        avg_rrs = [
            v["avg_rr"] for v in valid_patterns.values()
            if v["avg_rr"] > 0
        ]
        if avg_rrs:
            hist_avg_rr = sum(avg_rrs) / len(avg_rrs)
            if hist_avg_rr < 1.5:
                ins.recommended_min_rr = min(3.5, ins.recommended_min_rr + 0.3)
                logger.info(
                    f"BacktestInsights: avg RR histórico bajo ({hist_avg_rr:.2f}) → "
                    f"min_rr ajustado a {ins.recommended_min_rr:.1f}"
                )
            elif hist_avg_rr > 3.0:
                ins.recommended_min_rr = max(2.0, ins.recommended_min_rr - 0.2)


def get_best_hours(engine, symbol: str) -> list:
    """
    Retorna las mejores horas UTC para un símbolo según el backtest.
    Si no hay datos, retorna lista vacía (no filtrar).
    """
    best_hours = getattr(engine, "_best_hours_by_symbol", {})
    return best_hours.get(symbol, [])


def has_backtest_data() -> bool:
    """Verifica si existe el archivo de insights del backtest."""
    return os.path.exists(BACKTEST_LEARNING_FILE)
