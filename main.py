"""
SMC QUANT BOT — MAIN ORCHESTRATOR
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
from data.market_data   import MarketDataEngine
from quant.score        import QuantScoringEngine
from ai.validator       import AdaptiveAIEngine
from execution.executor import MT5Executor


class SMCQuantBot:
    def __init__(self):
        self.market   = MarketDataEngine()
        self.quant    = QuantScoringEngine()
        self.ai       = AdaptiveAIEngine()
        self.executor = MT5Executor()
        self._running = False
        self._cycle   = 0
        self._stats   = {"cycles":0,"signals":0,"trades":0,"wins":0,"losses":0}

    async def start(self):
        logger.info("=" * 60)
        logger.info("  SMC QUANT BOT V2 — INICIANDO")
        logger.info(f"  Activos: {', '.join(SYMBOLS)}")
        logger.info(f"  Score threshold: {SCORE_THRESHOLD*100:.0f}%")
        logger.info(f"  AI min score: {AI_MIN_SCORE}")
        logger.info("=" * 60)

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

    async def _main_loop(self):
        while self._running:
            self._cycle += 1
            self._stats["cycles"] += 1
            logger.info(f"\n{'─'*50}")
            logger.info(f"CICLO #{self._cycle} | {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")

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
            quant = await self.quant.analyze(mtf)
            if not quant.valid:
                logger.debug(f"Skip {symbol}: {quant.reject_reason}")
                return

            self._stats["signals"] += 1
            logger.info(
                f"SENAL | {symbol} {quant.direction.upper()} | "
                f"Score:{quant.final_score*100:.1f}% | {quant.pattern} | RR:1:{quant.rr}"
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
                logger.info(f"BLOCK {symbol} | AI score:{ai.score:.0f} | {ai.reasoning}")

        except Exception as e:
            logger.error(f"Error {symbol}: {e}")

    async def _execute(self, quant, ai):
        symbol = quant.symbol
        open_pos = self.executor.open_positions
        if any(p.symbol == symbol for p in open_pos.values()):
            logger.info(f"Skip {symbol} — posicion ya abierta")
            return

        result = await self.executor.send_order(
            symbol=symbol, direction=quant.direction,
            lots=0.01, entry=quant.entry,
            sl=quant.stop_loss, tp=quant.take_profit,
            score=quant.final_score * 100, pattern=quant.pattern,
        )

        if result.success:
            self._stats["trades"] += 1
            logger.info(
                f"TRADE OK | {symbol} {quant.direction.upper()} | "
                f"Entry:{result.entry_price} SL:{quant.stop_loss} TP:{quant.take_profit} | "
                f"RR:1:{quant.rr} | ID:{result.order_id}"
            )
        else:
            logger.error(f"TRADE FAIL | {symbol} | {result.error}")

    async def _position_manager_loop(self):
        while self._running:
            await asyncio.sleep(30)
            try:
                prices = {}
                for sym in SYMBOLS:
                    cached = self.market.get_cached(sym)
                    if cached:
                        prices[sym] = cached.current_price
                if prices:
                    await self.executor.manage_positions(prices)
            except Exception as e:
                logger.error(f"Position manager: {e}")

    async def _stats_loop(self):
        while self._running:
            await asyncio.sleep(1800)
            s  = self._stats
            wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
            logger.info(
                f"\n{'='*50}\n"
                f"STATS | Ciclos:{s['cycles']} Senales:{s['signals']} "
                f"Trades:{s['trades']} WR:{wr:.1f}%\n"
                f"Balance:${self.executor.balance:,.2f} | "
                f"Posiciones:{len(self.executor.open_positions)}\n"
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
