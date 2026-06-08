"""
BACKTEST ENGINE — Orquestador institucional V5
Datos reales de Exness MT5 (bid/ask/spread real via MetaAPI dedicada)
con fallback a Binance + Dukascopy si MetaAPI no está disponible.

FASE 1 (RUN_BACKTEST=true):
  → Abre conexión MetaAPI DEDICADA (separada del stream en vivo)
  → Descarga histórico real de Exness con spread/bid/ask reales
  → Simula trades con lógica SMC institucional
  → Walk-Forward 70/30 + Monte Carlo
  → Guarda backtest_learning.json → Bot arranca calibrado en Fase 2

FASE 2 (RUN_BACKTEST=false):
  → LearningEngine carga backtest_learning.json al arrancar
  → Bot opera en vivo ya calibrado desde el minuto 0

CHECKPOINT SYSTEM (v2):
  → Cada activo completado se guarda en RESULTS_DIR/
  → En el próximo deploy, los activos con checkpoint se saltean
  → Solo corre los activos que faltan
  → FORCE_RERUN=true borra checkpoints y re-corre todo
  → Requiere Railway Volume montado en /data (ver README)
"""

import asyncio
import json
import logging
import os
from dataclasses import field
from datetime import datetime, timezone

from backtesting.data_fetcher import DataFetcher, SYMBOL_START_YEAR
from backtesting.simulator    import Simulator, BacktestResult

logger = logging.getLogger("Backtesting")

DEFAULT_SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAUUSDm", "EURUSDm", "USTECm"]

# ─── Paths persistentes ──────────────────────────────────────
# Railway: montar Volume en /data → sobrevive redeploys
# Local:   usa ./backtest_results (creado automáticamente)
PERSISTENT_DIR = os.getenv("PERSISTENT_DIR", "/data")
RESULTS_DIR    = os.path.join(PERSISTENT_DIR, "backtest_results")
CACHE_DIR      = os.path.join(PERSISTENT_DIR, "backtest_cache")
LEARNING_FILE  = os.path.join(PERSISTENT_DIR, "backtest_learning.json")

# Forzar re-ejecución ignorando checkpoints (env var Railway)
FORCE_RERUN = os.getenv("FORCE_RERUN", "false").lower() == "true"

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR,   exist_ok=True)


# ─── Checkpoint helpers ───────────────────────────────────────

def _checkpoint_path(symbol: str) -> str:
    return os.path.join(RESULTS_DIR, f"{symbol}_checkpoint.json")


def _checkpoint_exists(symbol: str) -> bool:
    """Retorna True si el símbolo ya tiene backtest guardado."""
    if FORCE_RERUN:
        return False
    return os.path.exists(_checkpoint_path(symbol))


def _save_checkpoint(result: BacktestResult):
    """Serializa BacktestResult completo a JSON."""
    path = _checkpoint_path(result.symbol)
    try:
        data = {
            "saved_at":      datetime.now(timezone.utc).isoformat(),
            "symbol":        result.symbol,
            "timeframe":     result.timeframe,
            "source":        result.source,
            "start_date":    result.start_date,
            "end_date":      result.end_date,
            "total_trades":  result.total_trades,
            "wins":          result.wins,
            "losses":        result.losses,
            "breakevens":    result.breakevens,
            "win_rate":      result.win_rate,
            "profit_factor": result.profit_factor,
            "expectancy":    result.expectancy,
            "max_drawdown":  result.max_drawdown,
            "sharpe_ratio":  result.sharpe_ratio,
            "total_r":       result.total_r,
            "avg_win_r":     result.avg_win_r,
            "avg_loss_r":    result.avg_loss_r,
            "avg_rr":        result.avg_rr,
            "avg_bars_held": result.avg_bars_held,
            "avg_slippage":  result.avg_slippage,
            "yearly_stats":  result.yearly_stats,
            "session_stats": result.session_stats,
            "pattern_stats": result.pattern_stats,
            "best_hours":    result.best_hours,
            # trades y equity_curve se omiten para ahorrar espacio
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"💾 Checkpoint guardado: {result.symbol} → {path}")
    except Exception as e:
        logger.error(f"Error guardando checkpoint {result.symbol}: {e}")


def _load_checkpoint(symbol: str) -> BacktestResult:
    """Reconstruye BacktestResult desde JSON guardado."""
    path = _checkpoint_path(symbol)
    with open(path) as f:
        d = json.load(f)

    result = BacktestResult(
        symbol       = d["symbol"],
        timeframe    = d["timeframe"],
        source       = d.get("source", "checkpoint"),
        start_date   = d.get("start_date", "N/A"),
        end_date     = d.get("end_date",   "N/A"),
        total_trades = d.get("total_trades", 0),
        wins         = d.get("wins",        0),
        losses       = d.get("losses",      0),
        breakevens   = d.get("breakevens",  0),
        win_rate     = d.get("win_rate",    0.0),
        profit_factor= d.get("profit_factor", 0.0),
        expectancy   = d.get("expectancy",  0.0),
        max_drawdown = d.get("max_drawdown", 0.0),
        sharpe_ratio = d.get("sharpe_ratio", 0.0),
        total_r      = d.get("total_r",     0.0),
        avg_win_r    = d.get("avg_win_r",   0.0),
        avg_loss_r   = d.get("avg_loss_r",  0.0),
        avg_rr       = d.get("avg_rr",      0.0),
        avg_bars_held= d.get("avg_bars_held", 0.0),
        avg_slippage = d.get("avg_slippage", 0.0),
        yearly_stats = d.get("yearly_stats",  {}),
        session_stats= d.get("session_stats", {}),
        pattern_stats= d.get("pattern_stats", {}),
        best_hours   = d.get("best_hours",   []),
    )
    saved_at = d.get("saved_at", "unknown")
    logger.info(
        f"📂 Checkpoint cargado: {symbol} | "
        f"WR:{result.win_rate*100:.1f}% | PF:{result.profit_factor:.2f} | "
        f"Guardado: {saved_at[:10]}"
    )
    return result


# ─── BacktestEngine ───────────────────────────────────────────

class BacktestEngine:
    """
    Orquestador del backtest institucional.

    Checkpoint system:
      - Activos completados se saltan automáticamente.
      - Solo corre los que faltan.
      - FORCE_RERUN=true re-corre todo ignorando checkpoints.
    """

    def __init__(self, token: str = "", account_id: str = ""):
        self._token      = token      or os.getenv("META_API_TOKEN", "")
        self._account_id = account_id or os.getenv("MT5_ACCOUNT_ID", "")
        self.fetcher     = DataFetcher(
            self._token,
            self._account_id,
            cache_dir=CACHE_DIR,   # ← usa el directorio persistente
        )
        self.simulator = Simulator()
        logger.info(
            "BacktestEngine V5 | "
            "Fuente primaria: Exness MT5 via MetaAPI dedicada | "
            "Fallback: Binance + Dukascopy"
        )
        if FORCE_RERUN:
            logger.warning("⚠️  FORCE_RERUN=true — ignorando todos los checkpoints")

    async def run(
        self,
        symbol: str,
        timeframe: str         = "H1",
        min_rr: float          = 2.5,
        score_threshold: float = 0.65,
        walk_forward: bool     = True,
    ) -> BacktestResult:
        logger.info(f"{'='*55}")
        logger.info(f"Backtest {symbol} {timeframe}")

        bars, source = await self.fetcher.fetch(symbol, timeframe)

        if len(bars) < 200:
            logger.error(
                f"Datos insuficientes {symbol}: {len(bars)} barras "
                f"(mínimo 200) — saltando"
            )
            return BacktestResult(
                symbol=symbol, timeframe=timeframe,
                start_date="N/A", end_date="N/A"
            )

        logger.info(
            f"{symbol} [{source}]: {len(bars)} barras | "
            f"{bars[0].time[:10]} → {bars[-1].time[:10]}"
        )

        if walk_forward and len(bars) > 1000:
            result = self.simulator.walk_forward(
                bars, symbol, timeframe, source, min_rr, score_threshold
            )
        else:
            trades = self.simulator.simulate_trades(
                bars, symbol, min_rr, score_threshold
            )
            result = self.simulator.calculate_metrics(
                trades, bars, symbol, timeframe, source
            )

        result.start_date = bars[0].time[:10]
        result.end_date   = bars[-1].time[:10]
        result.source     = source

        self.simulator.save_result(result)
        result.print_report()

        # ── Guardar checkpoint ────────────────────────────────
        _save_checkpoint(result)

        return result

    async def run_all(
        self,
        symbols: list          = None,
        timeframe: str         = "H1",
        min_rr: float          = 2.5,
        score_threshold: float = 0.65,
    ) -> dict:
        if symbols is None:
            symbols = DEFAULT_SYMBOLS

        logger.info(
            f"{'='*55}\n"
            f"BACKTEST INSTITUCIONAL — FASE 1\n"
            f"Activos: {', '.join(symbols)}\n"
            f"Timeframe: {timeframe} | Min RR: {min_rr} | "
            f"Score threshold: {score_threshold}\n"
            f"{'='*55}"
        )

        # ── Verificar checkpoints existentes ──────────────────
        pending  = []
        results  = {}

        for sym in symbols:
            if _checkpoint_exists(sym):
                try:
                    results[sym] = _load_checkpoint(sym)
                    logger.info(f"⏭️  {sym}: checkpoint encontrado — saltando backtest")
                except Exception as e:
                    logger.warning(f"Checkpoint {sym} corrupto ({e}) — re-corriendo")
                    pending.append(sym)
            else:
                pending.append(sym)

        if not pending:
            logger.info("✅ Todos los activos tienen checkpoint — sin trabajo pendiente")
        else:
            logger.info(
                f"📋 Activos pendientes: {', '.join(pending)} | "
                f"Saltados: {', '.join(s for s in symbols if s not in pending)}"
            )

            # ── Conectar MetaAPI solo si hay trabajo ──────────
            await self.fetcher.connect()

            for sym in pending:
                try:
                    res = await self.run(sym, timeframe, min_rr, score_threshold)
                    results[sym] = res
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Backtest {sym} fallido: {e}", exc_info=True)

        # ── Resumen completo (checkpoints + nuevos) ───────────
        self._print_summary(results)

        # ── Monte Carlo para activos con datos suficientes ────
        for sym, res in results.items():
            if res.total_trades >= 30:
                mc = self.simulator.monte_carlo(res)
                mc.print_report(sym)

        # ── Guardar backtest_learning.json ────────────────────
        self.simulator.save_learning_insights(results, path=LEARNING_FILE)

        completed_count = len([r for r in results.values() if r.total_trades > 0])
        logger.info(
            f"✅ FASE 1 COMPLETA | {completed_count}/{len(symbols)} activos\n"
            f"   backtest_learning.json → {LEARNING_FILE}\n"
            f"   Cambiar RUN_BACKTEST=false y re-deployar para Fase 2."
        )
        return results

    def _print_summary(self, results: dict):
        print(f"\n{'='*65}")
        print(f"  RESUMEN BACKTEST INSTITUCIONAL — {len(results)} ACTIVOS")
        print(f"{'='*65}")
        for sym, res in results.items():
            if res.total_trades == 0:
                print(f"  {sym:10} | SIN DATOS SUFICIENTES")
                continue
            checkpoint_tag = " [ckpt]" if not FORCE_RERUN and os.path.exists(_checkpoint_path(sym)) else ""
            print(
                f"  {sym:10} | "
                f"WR:{res.win_rate*100:.1f}% | "
                f"PF:{res.profit_factor:.2f} | "
                f"DD:{res.max_drawdown*100:.1f}% | "
                f"R:{res.total_r:.1f} | "
                f"Trades:{res.total_trades}{checkpoint_tag}"
            )
        print(f"{'='*65}\n")

    async def close(self):
        await self.fetcher.close()
