"""
EXECUTION ENGINE — MetaAPI → MT5 Exness
Ejecución, gestión de posiciones, trailing stop, break even.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import aiohttp
import json

from config import (
    META_API_TOKEN, MT5_ACCOUNT_ID, RISK_PER_TRADE,
    MAX_DAILY_DD, MAX_TOTAL_DD, MAX_SIMULTANEOUS,
    MIN_RR, BALANCE_START, SYMBOL_CONFIG
)

logger = logging.getLogger("Executor")

BASE_URL = "https://mt-client-api-v1.london.agiliumtrade.ai"


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
    partial_closed: bool = False

    @property
    def age_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.open_time).seconds / 60


class RiskManager:
    def __init__(self):
        self._daily_dd   = 0.0
        self._total_dd   = 0.0
        self._balance    = BALANCE_START
        self._peak       = BALANCE_START
        self._day_losses = 0.0
        self._last_reset = datetime.now(timezone.utc).date()

    def update_balance(self, balance: float):
        self._balance = balance
        if balance > self._peak:
            self._peak = balance
        self._total_dd = (self._peak - balance) / self._peak

    def record_loss(self, amount: float):
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset:
            self._day_losses = 0.0
            self._last_reset = today
        self._day_losses += abs(amount)

    def can_trade(self) -> tuple:
        daily_dd_pct = self._day_losses / (self._balance + 1e-9)
        if daily_dd_pct >= MAX_DAILY_DD:
            return False, f"Drawdown diario {daily_dd_pct*100:.1f}% >= {MAX_DAILY_DD*100:.0f}%"
        if self._total_dd >= MAX_TOTAL_DD:
            return False, f"Drawdown total {self._total_dd*100:.1f}% >= {MAX_TOTAL_DD*100:.0f}%"
        return True, "OK"

    def calculate_lot_size(self, symbol: str, entry: float, sl: float) -> float:
        risk_amount = self._balance * RISK_PER_TRADE
        cfg = SYMBOL_CONFIG.get(symbol, {})
        pip = cfg.get("pip", 0.0001)
        sl_pips = abs(entry - sl) / pip
        if sl_pips <= 0:
            return 0.01
        if symbol in ["BTCUSD", "ETHUSD"]:
            lot = risk_amount / (sl_pips * pip * 1)
            return round(max(0.001, min(lot, 1.0)), 3)
        elif symbol == "XAUUSD":
            lot = risk_amount / (sl_pips * pip * 100)
            return round(max(0.01, min(lot, 5.0)), 2)
        elif symbol == "NAS100":
            lot = risk_amount / (sl_pips * pip * 1)
            return round(max(0.01, min(lot, 5.0)), 2)
        else:
            lot = risk_amount / (sl_pips * pip * 100000)
            return round(max(0.01, min(lot, 10.0)), 2)


class MT5Executor:
    def __init__(self):
        # Lee desde env primero, luego config
        self._token   = os.getenv("META_API_TOKEN", "") or META_API_TOKEN
        self._acct_id = os.getenv("MT5_ACCOUNT_ID", "") or MT5_ACCOUNT_ID
        self._session: Optional[aiohttp.ClientSession] = None
        self._positions: dict = {}
        self._risk    = RiskManager()
        self._connected = False
        self._balance   = BALANCE_START
        has_token = bool(self._token)
        has_acct  = bool(self._acct_id)
        logger.info(f"MT5Executor iniciado | Token:{'OK' if has_token else 'MISSING'} | Account:{'OK' if has_acct else 'MISSING'}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # SSL desactivado para compatibilidad con MetaAPI en Railway
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                headers={
                    "auth-token": self._token,
                    "Content-Type": "application/json",
                },
                connector=connector
            )
        return self._session

    async def connect(self) -> bool:
        if not self._token or not self._acct_id:
            logger.warning("MetaAPI no configurado — modo simulación")
            self._connected = False
            return False
        try:
            sess = await self._get_session()
            url  = f"{BASE_URL}/users/current/accounts/{self._acct_id}"
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json()
                    self._balance = data.get("balance", BALANCE_START)
                    self._risk.update_balance(self._balance)
                    self._connected = True
                    logger.info(f"✅ MetaAPI conectado | Balance: ${self._balance:,.2f}")
                    return True
                else:
                    logger.error(f"MetaAPI connect: HTTP {r.status}")
                    return False
        except Exception as e:
            logger.error(f"MetaAPI connect error: {e}")
            return False

    async def send_order(
        self, symbol, direction, lots, entry, sl, tp, score, pattern, comment="SMC_BOT"
    ) -> TradeResult:
        can, reason = self._risk.can_trade()
        if not can:
            return TradeResult(success=False, error=f"Risk block: {reason}")
        if len(self._positions) >= MAX_SIMULTANEOUS:
            return TradeResult(success=False, error=f"Max posiciones ({MAX_SIMULTANEOUS}) alcanzado")
        lots = self._risk.calculate_lot_size(symbol, entry, sl)
        if lots <= 0:
            return TradeResult(success=False, error="Lot size invalido")
        if not self._connected:
            return await self._sim_order(symbol, direction, lots, entry, sl, tp, score, pattern)
        try:
            sess = await self._get_session()
            url  = f"{BASE_URL}/users/current/accounts/{self._acct_id}/trade"
            action = "ORDER_TYPE_BUY" if direction == "buy" else "ORDER_TYPE_SELL"
            payload = {
                "actionType": action,
                "symbol": symbol,
                "volume": lots,
                "stopLoss": sl,
                "takeProfit": tp,
                "comment": f"{comment}|{pattern}|{score:.0f}",
            }
            async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                if r.status in [200, 201] and data.get("orderId"):
                    order_id = data["orderId"]
                    self._positions[order_id] = Position(
                        order_id=order_id, symbol=symbol,
                        direction=direction, lots=lots,
                        entry=entry, sl=sl, tp=tp,
                        open_time=datetime.now(timezone.utc),
                        score=score, pattern=pattern,
                    )
                    logger.info(f"✅ ORDER | {symbol} {direction.upper()} {lots} @ {entry} | ID:{order_id}")
                    return TradeResult(success=True, order_id=order_id, entry_price=entry)
                else:
                    err = data.get("error", str(data))
                    logger.error(f"Order failed: {err}")
                    return TradeResult(success=False, error=err)
        except Exception as e:
            logger.error(f"send_order error: {e}")
            return TradeResult(success=False, error=str(e))

    async def _sim_order(self, symbol, direction, lots, entry, sl, tp, score, pattern) -> TradeResult:
        order_id = f"SIM_{symbol}_{int(datetime.now().timestamp())}"
        self._positions[order_id] = Position(
            order_id=order_id, symbol=symbol,
            direction=direction, lots=lots,
            entry=entry, sl=sl, tp=tp,
            open_time=datetime.now(timezone.utc),
            score=score, pattern=pattern,
        )
        logger.info(f"SIM ORDER | {symbol} {direction.upper()} | Score:{score:.0f} | {pattern}")
        return TradeResult(success=True, order_id=order_id, entry_price=entry)

    async def modify_sl(self, order_id: str, new_sl: float) -> bool:
        pos = self._positions.get(order_id)
        if not pos:
            return False
        if not self._connected:
            pos.sl = new_sl
            return True
        try:
            sess = await self._get_session()
            url  = f"{BASE_URL}/users/current/accounts/{self._acct_id}/trade"
            payload = {"actionType": "POSITION_MODIFY", "positionId": order_id, "stopLoss": new_sl, "takeProfit": pos.tp}
            async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status in [200, 201]:
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
            sess = await self._get_session()
            url  = f"{BASE_URL}/users/current/accounts/{self._acct_id}/trade"
            payload = {"actionType": "POSITION_CLOSE_ID", "positionId": order_id}
            async with sess.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status in [200, 201]:
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
                            logger.info(f"BE activado | {pos.symbol} | SL → {new_sl:.5f}")
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
                            logger.info(f"BE activado | {pos.symbol} | SL → {new_sl:.5f}")
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
        if self._session and not self._session.closed:
            await self._session.close()
