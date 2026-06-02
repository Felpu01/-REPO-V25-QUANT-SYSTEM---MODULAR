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
"""

import asyncio
import logging
import os

from backtesting.data_fetcher import DataFetcher, SYMBOL_START_YEAR
from backtesting.simulator    import Simulator, BacktestResult

logger = logging.getLogger("Backtesting")

# Símbolos por defecto si no se pasan
DEFAULT_SYMBOLS = ["BTCUSDm", "ETHUSDm", "XAUUSDm", "EURUSDm", "USTECm"]


class BacktestEngine:
    """
    Orquestador del backtest institucional.

    Usa MetaAPI para datos reales de Exness (conexión dedicada).
    Fallback automático a Binance/Dukascopy si MetaAPI no responde.
    """

    def __init__(self, token: str = "", account_id: str = ""):
        self._token      = token      or os.getenv("META_API_TOKEN", "")
        self._account_id = account_id or os.getenv("MT5_ACCOUNT_ID", "")
        self.fetcher     = DataFetcher(self._token, self._account_id)
        self.simulator   = Simulator()
        logger.info(
            "BacktestEngine V5 | "
            "Fuente primaria: Exness MT5 via MetaAPI dedicada | "
            "Fallback: Binance + Dukascopy"
        )

    async def run(
        self,
        symbol: str,
        timeframe: str         = "H1",
        min_rr: float          = 2.5,
        score_threshold: float = 0.65,
        walk_forward: bool     = True,
    ) -> BacktestResult:
        """
        Corre el backtest completo para un símbolo.
        Retorna BacktestResult con todas las métricas.
        """
        logger.info(f"{'='*55}")
        logger.info(f"Backtest {symbol} {timeframe}")

        # Descargar datos
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

        # Simular
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
        return result

    async def run_all(
        self,
        symbols: list          = None,
        timeframe: str         = "H1",
        min_rr: float          = 2.5,
        score_threshold: float = 0.65,
    ) -> dict:
        """
        Corre el backtest para todos los activos.
        Conecta MetaAPI dedicada al inicio, la cierra al terminar.
        Genera backtest_learning.json al finalizar.
        """
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

        # Conectar MetaAPI dedicada (datos reales de Exness)
        await self.fetcher.connect()

        results = {}
        for sym in symbols:
            try:
                res = await self.run(sym, timeframe, min_rr, score_threshold)
                results[sym] = res
                await asyncio.sleep(2)  # pausa entre activos
            except Exception as e:
                logger.error(f"Backtest {sym} fallido: {e}", exc_info=True)

        # Resumen
        self._print_summary(results)

        # Monte Carlo para activos con datos suficientes
        for sym, res in results.items():
            if res.total_trades >= 30:
                mc = self.simulator.monte_carlo(res)
                mc.print_report(sym)

        # Guardar backtest_learning.json — clave para Fase 2
        self.simulator.save_learning_insights(results)

        logger.info(
            "✅ FASE 1 COMPLETA\n"
            "   backtest_learning.json generado con datos reales de Exness.\n"
            "   Cambiar RUN_BACKTEST=false y re-deployar para Fase 2."
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
