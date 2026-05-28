"""
EXECUTION ENGINE — MetaAPI SDK oficial
Usa metaapi-cloud-sdk en vez de REST API directa.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config import (
    META_API_TOKEN, MT5_ACCOUNT_ID, RISK_PER_TRADE,
    MAX_DAILY_DD, MAX_TOTAL_DD, MAX_SIMULTANEOUS,
    MIN_RR, BALANCE_START, SYMBOL_CONFIG
)

logger = logging.getLogger("Executor")


@dataclass
class TradeResult:
    success: bool
    order_id: str = ""
    entry_price: float = 0.0
    error: str = ""


@dataclass
class Position:
    order_id: str
    symbol: str
    direction: str
    lots: float
    entry: float
    sl: float
    tp: float
    open_time: datetime
    score: float
    pattern: str
    be_activated: bool = False

    @property
    def age_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.open_time).seconds / 60


class RiskManager:
    def __init__(self):
        self._balance    = BALANCE_START
        self._peak       = BALANCE_START
        self._total_dd   = 0.0
        self._day_losses = 0.0
        self._last_reset = datetime.now(timezone.utc).date()

    def update_balance(self, balance: float):
        self._balance = balance
        if balance > self._peak:
            self._peak = balance
        self._total_dd = (self._peak - balance) / (self._peak + 1e-9)

    def record_loss(self, amount: float):
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset:
            self._day_losses = 0.0
            self._last_reset = today
        self._day_losses += abs(amount)

    def can_trade(self) -> tuple:
        daily_pct = self._day_losses / (self._balance + 1e-9)
        if daily_pct >= MAX_DAILY_DD:
            return False, f"Drawdown diario {daily_pct*100:.1f}%"
        if self._total_dd >= MAX_TOTAL_DD:
            return False, f"Drawdown total {self._total_dd*100:.1f}%"
        return True, "OK"

    def calculate_lot_size(self, symbol: str, entry: float, sl: float) -> float:
        risk_amount = self._balance * RISK_PER_TRADE
        cfg  = SYMBOL_CONFIG.get(symbol, {})
        pip  = cfg.get("pip", 0.0001)
        sl_pips = abs(entry - sl) / pip
        if sl_pips <= 0:
            return 0.01
        if symbol in ["BTCUSD", "ETHUSD"]:
            return round(max(0.001, min(risk_amount / (sl_pips * pip), 1.0)), 3)
        elif symbol == "XAUUSD":
            return round(max(0.01, min(risk_amount / (sl_pips * pip * 100), 5.0)), 2)
        elif symbol == "NAS100":
            return round(max(0.01, min(risk_amount / (sl_pips * pip), 5.0)), 2)
        else:
            return round(max(0.01, min(risk_amount / (sl_pips * pip * 100000), 10.0)), 2)


class MT5Executor:
    def __init__(self):
        self._token   = os.getenv("META_API_TOKEN", "") or META_API_TOKEN
        self._acct_id = os.getenv("MT5_ACCOUNT_ID", "") or MT5_ACCOUNT_ID
        self._api        = None
        self._account    = None
        self._connection = None
        self._positions: dict = {}
        self._risk       = RiskManager()
        self._connected  = False
        self._balance    = BALANCE_START
        logger.info(
            f"MT5Executor iniciado | "
            f"Token:{'OK' if self._token else 'MISSING'} | "
            f"Account:{'OK' if self._acct_id else 'MISSING'}"
        )

    async def connect(self) -> bool:
        if not self._token or not self._acct_id:
            logger.warning("MetaAPI no configurado — modo simulación")
            return False
        try:
            from metaapi_cloud_sdk import MetaApi
            logger.info("Conectando via MetaAPI SDK...")
            self._api     = MetaApi(self._token)
            self._account = await self._api.metatrader_account_api.get_account(self._acct_id)

            if self._account.state not in ['DEPLOYING', 'DEPLOYED']:
                logger.info("Desplegando cuenta MetaAPI...")
                await self._account.deploy()

            logger.info("Esperando conexión al broker (puede tardar 1-2 min)...")
            await self._account.wait_connected()

            self._connection = self._account.get_rpc_connection()
            await self._connection.connect()

            logger.info("Sincronizando estado del terminal...")
            await self._connection.wait_synchronized()

            info = await self._connection.get_account_information()
            self._balance = info.get("balance", BALANCE_START)
            self._risk.update_balance(self._balance)
            self._connected = True
            logger.info(f"✅ MetaAPI SDK conectado | Balance: ${self._balance:,.2f}")
            return True

        except Exception as e:
            logger.error(f"MetaAPI connect error: {e}")
            self._connected = False
            return False

    async def send_order(
        self, symbol, direction, lots, entry, sl, tp, score, pattern, comment="SMC_BOT"
    ) -> TradeResult:
        can, reason = self._risk.can_trade()
        if not can:
            return TradeResult(success=False, error=f"Risk block: {reason}")
        if len(self._positions) >= MAX_SIMULTANEOUS:
            return TradeResult(success=False, error="Max posiciones alcanzado")

        lots = self._risk.calculate_lot_size(symbol, entry, sl)
        if lots <= 0:
            return TradeResult(success=False, error="Lot size invalido")

        if not self._connected:
            return await self._sim_order(symbol, direction, lots, entry, sl, tp, score, pattern)

        try:
            opts = {"comment": f"{comment}|{pattern}|{score:.0f}"}
            if direction == "buy":
                result = await self._connection.create_market_buy_order(
                    symbol, lots, sl, tp, opts
                )
            else:
                result = await self._connection.create_market_sell_order(
                    symbol, lots, sl, tp, opts
                )

            order_id = result.get("orderId", f"MT5_{symbol}_{int(datetime.now().timestamp())}")
            self._positions[order_id] = Position(
                order_id=order_id, symbol=symbol,
                direction=direction, lots=lots,
                entry=entry, sl=sl, tp=tp,
                open_time=datetime.now(timezone.utc),
                score=score, pattern=pattern,
            )
            logger.info(
                f"✅ ORDER | {symbol} {direction.upper()} "
                f"{lots} @ {entry} | ID:{order_id}"
            )
            return TradeResult(success=True, order_id=order_id, entry_price=entry)

        except Exception as e:
            logger.error(f"send_order error: {e}")
            return TradeResult(success=False, error=str(e))

    async def _sim_order(
        self, symbol, direction, lots, entry, sl, tp, score, pattern
    ) -> TradeResult:
        order_id = f"SIM_{symbol}_{int(datetime.now().timestamp())}"
        self._positions[order_id] = Position(
            order_id=order_id, symbol=symbol,
            direction=direction, lots=lots,
            entry=entry, sl=sl, tp=tp,
            open_time=datetime.now(timezone.utc),
            score=score, pattern=pattern,
        )
        logger.info(
            f"SIM ORDER | {symbol} {direction.upper()} | "
            f"Score:{score:.0f} | {pattern}"
        )
        return TradeResult(success=True, order_id=order_id, entry_price=entry)

    async def modify_sl(self, order_id: str, new_sl: float) -> bool:
        pos = self._positions.get(order_id)
        if not pos:
            return False
        if not self._connected:
            pos.sl = new_sl
            return True
        try:
            await self._connection.modify_position(order_id, new_sl, pos.tp)
            pos.sl = new_sl
            return True
        except Exception as e:
            logger.error(f"modify_sl error: {e}")
        return False

    async def close_position(self, order_id: str, reason: str = "") -> bool:
        pos = self._positions.get(order_id)
        if not pos:
            return False
        if not self._connected:
            logger.info(f"SIM CLOSE | {pos.symbol} | {reason}")
            del self._positions[order_id]
            return True
        try:
            await self._connection.close_position(order_id)
            logger.info(f"✅ CLOSED {pos.symbol} | {reason}")
            del self._positions[order_id]
            return True
        except Exception as e:
            logger.error(f"close_position error: {e}")
        return False

    async def manage_positions(self, current_prices: dict):
        for order_id, pos in list(self._positions.items()):
            price = current_prices.get(pos.symbol, 0)
            if price == 0:
                continue
            atr_approx = abs(pos.entry - pos.sl) * 1.5
            if pos.direction == "buy":
                profit = price - pos.entry
                if not pos.be_activated and profit >= atr_approx:
                    new_sl = pos.entry + (pos.entry - pos.sl) * 0.02
                    if new_sl > pos.sl:
                        ok = await self.modify_sl(order_id, round(new_sl, 5))
                        if ok:
                            pos.be_activated = True
                            logger.info(f"BE activado | {pos.symbol} | SL→{new_sl:.5f}")
                elif pos.be_activated and profit >= atr_approx * 2:
                    trail_sl = price - atr_approx * 0.8
                    if trail_sl > pos.sl:
                        await self.modify_sl(order_id, round(trail_sl, 5))
            elif pos.direction == "sell":
                profit = pos.entry - price
                if not pos.be_activated and profit >= atr_approx:
                    new_sl = pos.entry - (pos.sl - pos.entry) * 0.02
                    if new_sl < pos.sl:
                        ok = await self.modify_sl(order_id, round(new_sl, 5))
                        if ok:
                            pos.be_activated = True
                            logger.info(f"BE activado | {pos.symbol} | SL→{new_sl:.5f}")
                elif pos.be_activated and profit >= atr_approx * 2:
                    trail_sl = price + atr_approx * 0.8
                    if trail_sl < pos.sl:
                        await self.modify_sl(order_id, round(trail_sl, 5))

    @property
    def is_connected(self): return self._connected
    @property
    def open_positions(self): return dict(self._positions)
    @property
    def balance(self): return self._balance
    @property
    def risk(self): return self._risk

    async def close(self):
        try:
            if self._connection:
                await self._connection.close()
        except Exception:
            pass
