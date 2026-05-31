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


async def init_metaapi(token: str, account_id: str):
    """
    Crea UNA SOLA conexión MetaAPI compartida.
    La retorna para que la usen tanto MarketData como Executor.
    """
    if not token or not account_id:
        return None, None, None
    try:
        from metaapi_cloud_sdk import MetaApi
        logger.info("Iniciando conexión MetaAPI SDK...")
        api     = MetaApi(token)
        account = await api.metatrader_account_api.get_account(account_id)

        if account.state not in ['DEPLOYING', 'DEPLOYED']:
            logger.info("Desplegando cuenta MetaAPI...")
            await account.deploy()

        logger.info("Esperando conexión al broker (1-2 min)...")
        await account.wait_connected()

        connection = account.get_rpc_connection()
        await connection.connect()

        logger.info("Sincronizando terminal...")
        await connection.wait_synchronized()

        logger.info("✅ MetaAPI SDK conectado y sincronizado")
        return api, account, connection

    except Exception as e:
        logger.error(f"MetaAPI init error: {e}")
        return None, None, None


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
        self._metaapi_connection = None

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

        # ── Conexión MetaAPI compartida ───────────────────────
        token      = os.getenv("META_API_TOKEN", "") or config.META_API_TOKEN
        account_id = os.getenv("MT5_ACCOUNT_ID", "") or config.MT5_ACCOUNT_ID

        api, account, connection = await init_metaapi(token, account_id)

        if connection:
            # Compartir la misma conexión con MarketData y Executor
            self.market.set_connection(connection)
            self.executor.set_connection(connection)

            # Obtener balance — FIX: usar _risk.update_balance() vía executor
            try:
                info = await connection.get_account_information()
                balance = info.get("balance", 10000)
                self.executor._balance = balance
                self.executor._risk.update_balance(balance)
                logger.info(f"✅ MT5/Exness conectado | Balance: ${balance:,.2f}")
            except Exception as e:
                logger.warning(f"Balance fetch error: {e}")
        else:
            logger.warning("⚠️  Sin MetaAPI — modo SIMULACION activo")

        self._metaapi_connection = connection

        # ── Backtest on demand ────────────────────────────────
        if os.getenv("RUN_BACKTEST", "false").lower() == "true":
            await self._run_backtest(token, account_id, connection)

        self._running = True
        await asyncio.gather(
            self._main_loop(),
            self._position_manager_loop(),
            self._stats_loop(),
        )

    async def _run_backtest(self, token: str = "", account_id: str = "", connection=None):
        try:
            from backtesting.backtest_engine import BacktestEngine
            logger.info("🔬 INICIANDO BACKTESTING HISTÓRICO — MetaAPI/Exness")
            logger.info(f"   Activos: {', '.join(SYMBOLS)}")

            bt = BacktestEngine(
                token=token or os.getenv("META_API_TOKEN", ""),
                account_id=account_id or os.getenv("MT5_ACCOUNT_ID", ""),
                connection=connection,  # FIX: pasar conexión RPC activa
            )
            results = await bt.run_all(symbols=SYMBOLS)
            await bt.close()

            for sym, res in results.items():
                if res.total_trades >= 10:
                    logger.info(
                        f"📊 Backtest {sym}: WR:{res.win_rate*100:.1f}% | "
                        f"PF:{res.profit_factor:.2f} | Trades:{res.total_trades} | "
                        f"Fuente:{res.source}"
                    )
                    for trade in res.trades[:500]:
                        tid = self.learning.record_trade_open(
                            symbol=sym, direction=trade.direction,
                            pattern=trade.pattern, entry=trade.entry,
                            sl=trade.sl, tp=trade.tp, score=trade.score,
                            smc_score=trade.score, momentum_score=0.5,
                            volatility_score=0.5, session="backtest",
                            timeframe_bias="backtest",
                            sweep_detected=False, news_impact="none",
                        )
                        if trade.outcome != "pending":
                            self.learning.record_trade_close(
                                trade_id=tid, outcome=trade.outcome,
                                pnl_pct=trade.pnl_r * 100,
                                rr_achieved=trade.rr_achieved,
                                bars_in_trade=trade.bars_held,
                                exit_reason="backtest",
                            )

            logger.info("✅ BACKTEST COMPLETADO")
            logger.info(f"  {self.learning.get_evolution_summary()}")

        except Exception as e:
            logger.error(f"Backtest error: {e}", exc_info=True)

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
                return

            quant = await self.quant.analyze(mtf)

            if not quant.valid:
                if "noticia" in quant.reject_reason:
                    self._stats["blocked_news"] += 1
                    logger.info(f"⚠️  {symbol} bloqueado: {quant.reject_reason}")
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
                sl=quant.stop_loss, tp=quant.take_profit,
                score=quant.final_score * 100,
            )
            trade_id = self.learning.record_trade_open(
                symbol=symbol, direction=direction,
                pattern=quant.pattern, entry=quant.entry,
                sl=quant.stop_loss, tp=quant.take_profit,
                score=quant.final_score, smc_score=quant.smc_score,
                momentum_score=quant.momentum_score,
                volatility_score=quant.volatility_score,
                session=quant.htf_bias, timeframe_bias=quant.htf_bias,
                sweep_detected=quant.sweep_detected,
                news_impact=quant.news_warning,
            )
            self._open_trade_ids[result.order_id] = trade_id
            logger.info(
                f"TRADE OK | {symbol} {direction.upper()} | "
                f"Entry:{result.entry_price} SL:{quant.stop_loss} "
                f"TP:{quant.take_profit} | RR:1:{quant.rr} | ID:{result.order_id}"
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
                    price  = cached.current_price
                    pos    = pm.get_position()
                    result = pm.update_position(price)
                    if result is None:
                        continue
                    if result.get("state") == "CLOSED":
                        reason  = result.get("close_reason", "")
                        outcome = result.get("result", "")
                        pnl     = result.get("pnl", 0)
                        close_p = result.get("close_price", price)
                        logger.info(
                            f"POSICION CERRADA | {sym} | {reason} | "
                            f"Resultado:{outcome} | PnL:{pnl:.5f} | "
                            f"Cierre:{close_p:.5f}"
                        )
                        if outcome == "WIN":   self._stats["wins"] += 1
                        elif outcome == "LOSS": self._stats["losses"] += 1
                        order_id = next((o for o in self._open_trade_ids if sym in o), None)
                        if order_id and order_id in self._open_trade_ids:
                            trade_id = self._open_trade_ids.pop(order_id)
                            entry = result.get("entry", 0)
                            sl    = result.get("sl", 0)
                            risk  = abs(entry - sl)
                            self.learning.record_trade_close(
                                trade_id=trade_id,
                                outcome=outcome.lower(),
                                pnl_pct=pnl / (entry + 1e-9) * 100,
                                rr_achieved=abs(pnl) / risk if risk > 0 else 0,
                                bars_in_trade=0,
                                exit_reason=reason,
                            )
                prices = {sym: self.market.get_cached(sym).current_price
                          for sym in SYMBOLS if self.market.get_cached(sym)}
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
            open_pos = [
                f"{sym}:{pm.get_position()['side']}:{pm.get_position()['state']}"
                for sym, pm in self.pos_managers.items() if pm.has_position()
            ]
            logger.info(
                f"\n{'='*50}\n"
                f"STATS | Ciclos:{s['cycles']} Senales:{s['signals']} "
                f"Trades:{s['trades']} WR:{wr:.1f}%\n"
                f"Wins:{s['wins']} Losses:{s['losses']} "
                f"Bloqueados:{s['blocked_news']}\n"
                f"Balance:${self.executor.balance:,.2f} | "
                f"Posiciones:{len(open_pos)}\n"
                f"{' | '.join(open_pos) if open_pos else 'Sin posiciones'}\n"
                f"LEARNING | Nivel:{ins.evolution_level}/10 | "
                f"WR:{ins.win_rate:.1%} | Patron:{ins.best_pattern}\n"
                f"{'='*50}"
            )

    async def stop(self):
        self._running = False
        await self.market.close()
        await self.ai.close()
        await self.executor.close()
        try:
            if self._metaapi_connection:
                await self._metaapi_connection.close()
        except Exception:
            pass
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
