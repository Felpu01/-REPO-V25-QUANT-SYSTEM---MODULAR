class MT5Executor:
    def __init__(self):
        self._token   = os.getenv("META_API_TOKEN", "") or META_API_TOKEN
        self._acct_id = os.getenv("MT5_ACCOUNT_ID", "") or MT5_ACCOUNT_ID

        self._api        = None
        self._account    = None
        self._connection = None

        self._positions: dict = {}

        self._risk      = RiskManager()
        self._connected = False
        self._balance   = BALANCE_START

        logger.info(
            f"MT5Executor iniciado | "
            f"Token:{'OK' if self._token else 'MISSING'} | "
            f"Account:{'OK' if self._acct_id else 'MISSING'}"
        )

    def set_connection(self, connection):
        """Recibe la conexión compartida desde main.py"""
        self._connection = connection
        self._connected  = True

        logger.info("✅ Executor: conexión MetaAPI recibida")

    def set_balance(self, balance: float):
        """Actualiza el balance desde main.py"""
        self._balance = balance
        self._risk.update_balance(balance)

    async def connect(self) -> bool:
        """Mantener por compatibilidad — la conexión se maneja en main.py"""

        if self._connected:
            return True

        logger.warning("Executor sin conexión — modo simulación")

        return False

    async def send_order(
        self, symbol, direction, lots, entry, sl, tp, score, pattern, comment="SMC_BOT"
    ) -> TradeResult:

        can, reason = self._risk.can_trade()

        if not can:
            return TradeResult(
                success=False,
                error=f"Risk block: {reason}"
            )

        if len(self._positions) >= MAX_SIMULTANEOUS:
            return TradeResult(
                success=False,
                error="Max posiciones alcanzado"
            )

        lots = self._risk.calculate_lot_size(
            symbol,
            entry,
            sl
        )

        if lots <= 0:
            return TradeResult(
                success=False,
                error="Lot size invalido"
            )

        if not self._connected:
            return await self._sim_order(
                symbol,
                direction,
                lots,
                entry,
                sl,
                tp,
                score,
                pattern
            )

        try:
            opts = {
                "comment": f"{comment}|{pattern}|{score:.0f}"
            }

            if direction == "buy":

                result = await self._connection.create_market_buy_order(
                    symbol,
                    lots,
                    sl,
                    tp,
                    opts
                )

            else:

                result = await self._connection.create_market_sell_order(
                    symbol,
                    lots,
                    sl,
                    tp,
                    opts
                )

            order_id = result.get(
                "orderId",
                f"MT5_{symbol}_{int(datetime.now().timestamp())}"
            )

            self._positions[order_id] = Position(
                order_id=order_id,
                symbol=symbol,
                direction=direction,
                lots=lots,
                entry=entry,
                sl=sl,
                tp=tp,
                open_time=datetime.now(timezone.utc),
                score=score,
                pattern=pattern,
            )

            logger.info(
                f"✅ ORDER | {symbol} {direction.upper()} "
                f"{lots} @ {entry} | ID:{order_id}"
            )

            return TradeResult(
                success=True,
                order_id=order_id,
                entry_price=entry
            )

        except Exception as e:
            logger.error(f"send_order error: {e}")

            return TradeResult(
                success=False,
                error=str(e)
            )

    async def _sim_order(
        self,
        symbol,
        direction,
        lots,
        entry,
        sl,
        tp,
        score,
        pattern
    ) -> TradeResult:

        order_id = f"SIM_{symbol}_{int(datetime.now().timestamp())}"

        self._positions[order_id] = Position(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            lots=lots,
            entry=entry,
            sl=sl,
            tp=tp,
            open_time=datetime.now(timezone.utc),
            score=score,
            pattern=pattern,
        )

        logger.info(
            f"SIM ORDER | {symbol} {direction.upper()} | "
            f"Score:{score:.0f} | {pattern}"
        )

        return TradeResult(
            success=True,
            order_id=order_id,
            entry_price=entry
        )

    async def modify_sl(self, order_id: str, new_sl: float) -> bool:
        pos = self._positions.get(order_id)

        if not pos:
            return False

        if not self._connected:
            pos.sl = new_sl
            return True

        try:
            await self._connection.modify_position(
                order_id,
                new_sl,
                pos.tp
            )

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

                        ok = await self.modify_sl(
                            order_id,
                            round(new_sl, 5)
                        )

                        if ok:
                            pos.be_activated = True

                            logger.info(
                                f"BE activado | "
                                f"{pos.symbol} | "
                                f"SL→{new_sl:.5f}"
                            )

                elif pos.be_activated and profit >= atr_approx * 2:

                    trail_sl = price - atr_approx * 0.8

                    if trail_sl > pos.sl:
                        await self.modify_sl(
                            order_id,
                            round(trail_sl, 5)
                        )

            elif pos.direction == "sell":

                profit = pos.entry - price

                if not pos.be_activated and profit >= atr_approx:

                    new_sl = pos.entry - (pos.sl - pos.entry) * 0.02

                    if new_sl < pos.sl:

                        ok = await self.modify_sl(
                            order_id,
                            round(new_sl, 5)
                        )

                        if ok:
                            pos.be_activated = True

                            logger.info(
                                f"BE activado | "
                                f"{pos.symbol} | "
                                f"SL→{new_sl:.5f}"
                            )

                elif pos.be_activated and profit >= atr_approx * 2:

                    trail_sl = price + atr_approx * 0.8

                    if trail_sl < pos.sl:
                        await self.modify_sl(
                            order_id,
                            round(trail_sl, 5)
                        )

    @property
    def is_connected(self):
        return self._connected

    @property
    def open_positions(self):
        return dict(self._positions)

    @property
    def balance(self):
        return self._balance

    @property
    def risk(self):
        return self._risk

    async def close(self):
        try:
            if self._connection:
                await self._connection.close()
        except Exception:
            pass
