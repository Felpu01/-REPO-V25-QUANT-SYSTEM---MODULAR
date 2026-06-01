"""
BACKTEST ENGINE — Orquestador institucional
Une DataFetcher + Simulator en un flujo completo de 2 fases:

  Fase 1 (RUN_BACKTEST=true):
    → Descarga histórico (Binance + Dukascopy)
    → Simula trades con lógica SMC
    → Walk-Forward 70/30 + Monte Carlo
    → Guarda backtest_learning.json para Fase 2

  Fase 2 (RUN_BACKTEST=false):
    → LearningEngine lee backtest_learning.json al arrancar
    → Bot opera ya calibrado desde el minuto 0
"""

import asyncio
import logging
import os

from backtesting.data_fetcher import DataFetcher, EXNESS_SYMBOLS
from backtesting.simulator    import (
    Simulator, BacktestResult, MonteCarloResult
)

logger = logging.getLogger("Backtesting")


class BacktestEngine:
    """
    Orquestador principal del backtest institucional.
    Sin MetaAPI — usa Binance + Dukascopy como fuentes históricas.
    No satura el stream en vivo.
    """

    def __init__(self):
        self.fetcher   = DataFetcher()
        self.simulator = Simulator()
        logger.info(
            "BacktestEngine V5 iniciado | "
            "Fuentes: Binance Vision + Dukascopy | Sin MetaAPI"
        )

    async def run(
        self,
        symbol: str,
        timeframe: str    = "H1",
        min_rr: float     = 2.5,
        score_threshold: float = 0.65,
        walk_forward: bool = True,
    ) -> BacktestResult:
        """
        Corre el backtest completo para un símbolo.
        Retorna un BacktestResult con todas las métricas.
        """
        logger.info(f"{'='*55}")
        logger.info(f"Backtest {symbol} {timeframe}")

        # ── 1. Descarga datos ──────────────────────────────────
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

        # ── 2. Simular ────────────────────────────────────────
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

        # ── 3. Guardar resultado individual ───────────────────
        self.simulator.save_result(result)
        result.print_report()

        return result

    async def run_all(
        self,
        symbols: list         = None,
        timeframe: str        = "H1",
        min_rr: float         = 2.5,
        score_threshold: float = 0.65,
    ) -> dict:
        """
        Corre el backtest para todos los activos configurados.
        Al finalizar guarda backtest_learning.json para la Fase 2.
        """
        if symbols is None:
            symbols = list(EXNESS_SYMBOLS.keys())

        logger.info(
            f"{'='*55}\n"
            f"BACKTEST INSTITUCIONAL — FASE 1\n"
            f"Activos: {', '.join(symbols)}\n"
            f"Timeframe: {timeframe} | Min RR: {min_rr} | "
            f"Score: {score_threshold}\n"
            f"{'='*55}"
        )

        results = {}

        # Secuencial — más estable para descargas largas
        for sym in symbols:
            try:
                res = await self.run(
                    sym, timeframe, min_rr, score_threshold
                )
                results[sym] = res
                await asyncio.sleep(2)  # pausa entre activos
            except Exception as e:
                logger.error(f"Backtest {sym} fallido: {e}", exc_info=True)

        # ── Resumen consolidado ────────────────────────────────
        self._print_summary(results)

        # ── Monte Carlo para activos con suficientes datos ─────
        for sym, res in results.items():
            if res.total_trades >= 30:
                mc = self.simulator.monte_carlo(res)
                mc.print_report(sym)

        # ── Guardar backtest_learning.json (clave para Fase 2) ─
        self.simulator.save_learning_insights(results)

        logger.info(
            "✅ FASE 1 COMPLETA — backtest_learning.json listo.\n"
            "   Cambiar RUN_BACKTEST=false y re-deployar para Fase 2."
        )

        return results

    def _print_summary(self, results: dict):
        print(f"\n{'='*65}")
        print(f"  RESUMEN BACKTEST — {len(results)} ACTIVOS")
        print(f"{'='*65}")
        for sym, res in results.items():
            if res.total_trades == 0:
                print(f"  {sym:10} | SIN DATOS")
                continue
            print(
                f"  {sym:10} | "
                f"WR:{res.win_rate*100:.1f}% | "
                f"PF:{res.profit_factor:.2f} | "
                f"DD:{res.max_drawdown*100:.1f}% | "
                f"R:{res.total_r:.1f} | "
                f"Trades:{res.total_trades} | "
                f"[{res.source}]"
            )
        print(f"{'='*65}\n")

    async def close(self):
        await self.fetcher.close()
