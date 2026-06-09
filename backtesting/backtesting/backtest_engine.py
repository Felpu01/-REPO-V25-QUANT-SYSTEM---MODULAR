"""
BACKTEST ENGINE — V5.1
Fix: Monte Carlo se saltea para resultados cargados desde checkpoint
     (no tienen trades individuales disponibles).
Fix: USTECm usa fuentes alternativas si Dukascopy falla.
"""

import asyncio
import logging
import os

from backtesting.data_fetcher      import DataFetcher, SYMBOL_START_YEAR
from backtesting.simulator         import Simulator, BacktestResult
from backtesting.github_checkpoint import GitHubCheckpoint

logger = logging.getLogger("Backtesting")

DEFAULT_SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAUUSDm", "EURUSDm", "USTECm"]
LEARNING_FILE   = os.getenv("LEARNING_FILE", "backtest_learning.json")
FORCE_RERUN     = os.getenv("FORCE_RERUN", "false").lower() == "true"


class BacktestEngine:

    def __init__(self, token: str = "", account_id: str = ""):
        self._token      = token      or os.getenv("META_API_TOKEN", "")
        self._account_id = account_id or os.getenv("MT5_ACCOUNT_ID", "")
        self.fetcher     = DataFetcher(self._token, self._account_id)
        self.simulator   = Simulator()
        self.checkpoint  = GitHubCheckpoint()
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

        await self.checkpoint.save(result)
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

        # ── Verificar checkpoints en GitHub ───────────────────
        pending = []
        results = {}

        for sym in symbols:
            if not FORCE_RERUN:
                saved = await self.checkpoint.load(sym)
                if saved is not None:
                    results[sym] = saved
                    logger.info(f"⏭️  {sym}: checkpoint GitHub encontrado — saltando backtest")
                    continue
            pending.append(sym)

        if not pending:
            logger.info("✅ Todos los activos tienen checkpoint — sin trabajo pendiente")
        else:
            skipped = [s for s in symbols if s not in pending]
            logger.info(
                f"📋 Pendientes: {', '.join(pending)}"
                + (f" | Saltados: {', '.join(skipped)}" if skipped else "")
            )
            await self.fetcher.connect()
            for sym in pending:
                try:
                    res = await self.run(sym, timeframe, min_rr, score_threshold)
                    results[sym] = res
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Backtest {sym} fallido: {e}", exc_info=True)

        # ── Resumen ───────────────────────────────────────────
        self._print_summary(results)

        # ── Monte Carlo: SOLO para resultados con trades reales ──
        # Los checkpoints no guardan trades individuales → skip
        for sym, res in results.items():
            is_checkpoint = getattr(res, "source", "") == "github_checkpoint"
            if res.total_trades >= 30 and not is_checkpoint:
                logger.info(f"🎲 Monte Carlo: {sym} ({res.total_trades} trades)")
                mc = self.simulator.monte_carlo(res)
                mc.print_report(sym)
            elif is_checkpoint:
                logger.info(f"⏭️  Monte Carlo {sym}: saltado (cargado desde checkpoint)")

        # ── Guardar learning + subir a GitHub ─────────────────
        self.simulator.save_learning_insights(results)
        if os.path.exists(LEARNING_FILE):
            await self.checkpoint.save_learning_file(LEARNING_FILE)

        completed = len([r for r in results.values() if r.total_trades > 0])
        logger.info(
            f"✅ FASE 1 COMPLETA | {completed}/{len(symbols)} activos\n"
            f"   Checkpoints guardados en GitHub → bot_data/\n"
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
            tag = " [ckpt]" if getattr(res, "source", "") == "github_checkpoint" else ""
            dd  = min(res.max_drawdown * 100, 99999.0)  # cap display a 99999%
            print(
                f"  {sym:10} | "
                f"WR:{res.win_rate*100:.1f}% | "
                f"PF:{res.profit_factor:.2f} | "
                f"DD:{dd:.1f}% | "
                f"R:{res.total_r:.1f} | "
                f"Trades:{res.total_trades}{tag}"
            )
        print(f"{'='*65}\n")

    async def close(self):
        await self.fetcher.close()
        await self.checkpoint.close()
