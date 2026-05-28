"""
SMC QUANT BOT — MAIN ORCHESTRATOR FASE 2
GitHub → Railway → MetaAPI → MT5 Exness
Autor: Matías Gonzalez | 2026
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("MAIN")

import config
config.META_API_TOKEN    = os.getenv("META_API_TOKEN",    config.META_API_TOKEN)
config.MT5_ACCOUNT_ID   = os.getenv("MT5_ACCOUNT_ID",    config.MT5_ACCOUNT_ID)
config.ANTHROPIC_API_KEY= os.getenv("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY)

from config import SYMBOLS, LOOP_INTERVAL, DATA_REFRESH, SCORE_THRESHOLD, AI_MIN_SCORE
from data.market_data           import MarketDataEngine
from core.score                 import QuantScoringEngine, get_learning_engine
from ai.validator               import AdaptiveAIEngine
from execution.execution        import MT5Executor
from execution.position_manager import PositionManager


class SMCQuantBot:
    def __init__(self):
        self.market   = MarketDataEngine()
        self.quant    = QuantScoringEngine()
        self.ai       = AdaptiveAIEngine()
        self.executor = MT5Executor()
        self.learning = get_learning_engine()
        self.pos_managers: dict[str, PositionManager] = {
            sym: PositionManager(f"position_{sym}.json")
            for sym in SYMBOLS
        }
        self._running = False
        self._cycle   = 0
        self._stats   = {
            "cycles": 0, "signals": 0, "trades": 0,
            "wins": 0, "losses": 0, "blocked_news": 0,
        }
        self._open_trade_ids: dict[str, str] = {}

    async def start(self):
        logger.info("=" * 60)
        logger.info("  SMC QUANT BOT — FASE 2 INSTITUCIONAL")
        logger.info(f"  Activos: {', '.join(SYMBOLS)}")
        logger.info(f"  Score threshold: {SCORE_THRESHOLD*100:.0f}%")
        logger.info(f"  AI min score: {AI_MIN_SCORE}")
        logger.info(f"  {self.learning.get_evolution_summary()}")

        for sym, pm in self.pos_managers.items():
            if pm.has_position():
                pos = pm.get_position()
                logger.info(
                    f"  RECOVERY | {sym} {pos['side']} @ {pos['entry']} | "
                    f"Estado: {pos['state']}"
                )
        logger.info("=" * 60)

        # ── Backtest on demand ────────────────────────────────
        if os.getenv("RUN_BACKTEST", "false").lower() == "true":
            await self._run_backtest()

        connected = await self.executor.connect()
        if connected:
            logger.info("✅ MT5/Exness conectado via MetaAPI")
        else:
            logger.warning("⚠️  Sin MetaAPI — modo SIMULACION activo")

        self._running = True
        await asyncio.gather(
            self._main_loop(),
            self._position_manager_loop(),
            self._stats_loop(),
        )

    async def _run_backtest(self):
        """Corre backtesting histórico completo antes de arrancar el bot."""
        try:
            from backtesting.backtest_engine import BacktestEngine
            logger.info("=" * 60)
            logger.info("  🔬 INICIANDO BACKTESTING HISTÓRICO")
            logger.info("  BTC/ETH → Binance Vision M1 desde 2017")
            logger.info("  EUR/XAU/NAS → Dukascopy H1 desde 2003")
            logger.info("  Walk-Forward 70/30 | Monte Carlo 1000 sims")
            logger.info("=" * 60)

            bt = BacktestEngine()
            results = await bt.run_all()
            await bt.close()

            # Alimentar el Learning Engine con resultados del backtest
            for sym, res in results.items():
                if res.total_trades >= 10:
                    logger.info(
                        f"📊 Backtest {sym}: WR:{res.win_rate*100:.1f}% | "
                        f"PF:{res.profit_factor:.2f} | "
                        f"Trades:{res.total_trades} | "
                        f"R:{res.total_r:.1f}"
                    )
                    # Registrar trades del backtest en el learning engine
                    for trade in res.trades[:500]:  # máximo 500 trades por símbolo
                        tid = self.learning.record_trade_open(
                            symbol=sym,
                            direction=trade.direction,
                            pattern=trade.pattern,
                            entry=trade.entry,
                            sl=trade.sl,
                            tp=trade.tp,
                            score=trade.score,
                            smc_score=trade.score,
                            momentum_score=0.5,
                            volatility_score=0.5,
                            session="backtest",
                            timeframe_bias="backtest",
                            sweep_detected=False,
                            news_impact="none",
                        )
                        if trade.outcome != "pending":
                            self.learning.record_trade_close(
                                trade_id=tid,
                                outcome=trade.outcome,
                                pnl_pct=trade.pnl_r * 100,
                                rr_achieved=trade.rr_achieved,
                                bars_in_trade=trade.bars_held,
                                exit_reason="backtest",
                            )

            logger.info("=" * 60)
            logger.info("  ✅ BACKTEST COMPLETADO")
            logger.info(f"  Learning Engine actualizado con datos históricos")
            logger.info(f"  {self.learning.get_evolution_summary()}")
            logger.info("  Continuando con bot en vivo...")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Backtest error: {e} — continuando con bot normal")

    async def _main_loop(self):
        while self._running:
            self._cycle += 1
            self._stats["cycles"] += 1
            logger.info(f"\n{'─'*50}")
            logger.info(
                f"CICLO #{self._cycle} | "
                f"{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} | "
                f"Evolution Lvl:{self.learning.get_insights().evolution_level}"
            )

            try:
                all_data = await self.market.refresh_all()
                if not all_data:
                    await asyncio.sleep(DATA_REFRESH)
                    continue

                tasks = [self._analyze_symbol(sym, mtf) for sym, mtf in all_data.items()]
                await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)

            await asyncio.sleep(LOOP_INTERVAL)

    async def _analyze_symbol(self, symbol: str, mtf):
        try:
            pm = self.pos_managers[symbol]

            if pm.has_position():
                pos = pm.get_position()
                logger.debug(
                    f"Skip análisis {symbol} — posición abierta "
                    f"{pos['side']} @ {pos['entry']} [{pos['state']}]"
                )
                return

            quant = await self.quant.analyze(mtf)

            if not quant.valid:
                if "noticia" in quant.reject_reason:
                    self._stats["blocked_news"] += 1
                    logger.info(f"⚠️  {symbol} bloqueado por noticias: {quant.reject_reason}")
                else:
                    logger.debug(f"Skip {symbol}: {quant.reject_reason}")
                return

            self._stats["signals"] += 1
            logger.info(
                f"SENAL | {symbol} {quant.direction.upper()} | "
                f"Score:{quant.final_score*100:.1f}% | {quant.pattern} | "
                f"RR:1:{quant.rr} | Sweep:{quant.sweep_detected} | "
                f"Manip:{quant.manipulation_detected}"
            )

            ai = await self.ai.validate(quant)
            logger.info(
                f"AI | {symbol} | Score:{ai.score:.0f} | "
                f"Approved:{ai.approved} | {ai.confidence} | {ai.market_regime}"
            )
            for w in ai.warnings:
                logger.warning(f"   WARNING: {w}")

            if ai.approved and ai.score >= AI_MIN_SCORE:
                await self._execute(quant, ai)
            else:
                logger.info(f"BLOCK {symbol} | AI:{ai.score:.0f} | {ai.reasoning}")

        except Exception as e:
            logger.error(f"Error {symbol}: {e}")

    async def _execute(self, quant, ai):
        symbol    = quant.symbol
        pm        = self.pos_managers[symbol]
        direction = quant.direction

        if not pm.can_enter():
            logger.info(f"Skip {symbol} — PositionManager bloqueado")
            return

        result = await self.executor.send_order(
            symbol=symbol, direction=direction,
            lots=0.01, entry=quant.entry,
            sl=quant.stop_loss, tp=quant.take_profit,
            score=quant.final_score * 100, pattern=quant.pattern,
        )

        if result.success:
            self._stats["trades"] += 1

            side = "BUY" if direction == "buy" else "SELL"
            pm.open_position(
                side=side,
                entry=result.entry_price if result.entry_price else quant.entry,
                sl=quant.stop_loss,
                tp=quant.take_profit,
                score=quant.final_score * 100,
            )

            trade_id = self.learning.record_trade_open(
                symbol=symbol,
                direction=direction,
                pattern=quant.pattern,
                entry=quant.entry,
                sl=quant.stop_loss,
                tp=quant.take_profit,
                score=quant.final_score,
                smc_score=quant.smc_score,
                momentum_score=quant.momentum_score,
                volatility_score=quant.volatility_score,
                session=quant.htf_bias,
                timeframe_bias=quant.htf_bias,
                sweep_detected=quant.sweep_detected,
                news_impact=quant.news_warning,
            )
            self._open_trade_ids[result.order_id] = trade_id

            logger.info(
                f"TRADE OK | {symbol} {direction.upper()} | "
                f"Entry:{result.entry_price} SL:{quant.stop_loss} "
                f"TP:{quant.take_profit} | RR:1:{quant.rr} | "
                f"ID:{result.order_id} | PM:ACTIVO"
            )
        else:
            logger.error(f"TRADE FAIL | {symbol} | {result.error}")

    async def _position_manager_loop(self):
        while self._running:
            await asyncio.sleep(30)
            try:
                for sym in SYMBOLS:
                    pm = self.pos_managers[sym]
                    if not pm.has_position():
                        continue

                    cached = self.market.get_cached(sym)
                    if not cached or cached.current_price == 0:
                        continue

                    price = cached.current_price
                    pos   = pm.get_position()
                    result = pm.update_position(price)

                    if result is None:
                        continue

                    logger.debug(
                        f"PM {sym} | {pos['side']} | Precio:{price:.5f} | "
                        f"SL:{result.get('sl', 0):.5f} | Estado:{result.get('state')}"
                    )

                    if result.get("state") == "CLOSED":
                        reason  = result.get("close_reason", "")
                        outcome = result.get("result", "")
                        pnl     = result.get("pnl", 0)
                        close_p = result.get("close_price", price)

                        logger.info(
                            f"POSICION CERRADA | {sym} | {reason} | "
                            f"Resultado:{outcome} | PnL:{pnl:.5f} | "
                            f"Precio cierre:{close_p:.5f}"
                        )

                        if outcome == "WIN":
                            self._stats["wins"] += 1
                        elif outcome == "LOSS":
                            self._stats["losses"] += 1

                        order_id = None
                        for oid in self._open_trade_ids:
                            if sym in oid:
                                order_id = oid
                                break

                        if order_id and order_id in self._open_trade_ids:
                            trade_id = self._open_trade_ids.pop(order_id)
                            entry    = result.get("entry", 0)
                            sl       = result.get("sl", 0)
                            risk     = abs(entry - sl)
                            rr_ach   = abs(pnl) / risk if risk > 0 else 0

                            self.learning.record_trade_close(
                                trade_id=trade_id,
                                outcome=outcome.lower(),
                                pnl_pct=pnl / (entry + 1e-9) * 100,
                                rr_achieved=rr_ach,
                                bars_in_trade=0,
                                exit_reason=reason,
                            )

                prices = {}
                for sym in SYMBOLS:
                    cached = self.market.get_cached(sym)
                    if cached:
                        prices[sym] = cached.current_price
                if prices:
                    await self.executor.manage_positions(prices)

            except Exception as e:
                logger.error(f"Position manager loop: {e}")

    async def _stats_loop(self):
        while self._running:
            await asyncio.sleep(1800)
            s   = self._stats
            wr  = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
            ins = self.learning.get_insights()

            open_pos_info = []
            for sym, pm in self.pos_managers.items():
                if pm.has_position():
                    pos = pm.get_position()
                    open_pos_info.append(f"{sym}:{pos['side']}:{pos['state']}")

            logger.info(
                f"\n{'='*50}\n"
                f"STATS | Ciclos:{s['cycles']} Senales:{s['signals']} "
                f"Trades:{s['trades']} WR:{wr:.1f}%\n"
                f"Wins:{s['wins']} Losses:{s['losses']} "
                f"Bloqueados noticias:{s['blocked_news']}\n"
                f"Balance:${self.executor.balance:,.2f} | "
                f"Posiciones abiertas:{len(open_pos_info)}\n"
                f"{' | '.join(open_pos_info) if open_pos_info else 'Sin posiciones'}\n"
                f"LEARNING | Nivel:{ins.evolution_level}/10 | "
                f"WR historico:{ins.win_rate:.1%} | "
                f"Mejor patron:{ins.best_pattern}\n"
                f"{'='*50}"
            )

    async def stop(self):
        self._running = False
        await self.market.close()
        await self.ai.close()
        await self.executor.close()
        logger.info("Bot detenido limpiamente")


async def main():
    bot = SMCQuantBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
