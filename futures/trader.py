from __future__ import annotations

import logging
from typing import Any, Literal

from core.config import Settings
from core.exchange import create_okx_exchange
from core.risk import RiskLimitError, RiskManager
from core.strategy import MovingAverageSignal, moving_average_cross_signal_with_rsi


LOGGER = logging.getLogger(__name__)
Direction = Literal["long", "short"]


class FuturesTrader:
    def __init__(self, settings: Settings) -> None:
        try:
            self.settings = settings
            self.symbol = settings.futures_symbol
            self.exchange = create_okx_exchange(settings, "swap")
            self.exchange.load_markets()
            self.risk = RiskManager(settings, self.exchange)
            self.configure_margin_and_leverage()
        except Exception:
            LOGGER.exception("Futures trader initialization failed.")
            raise

    def configure_margin_and_leverage(self) -> None:
        margin_mode = self.settings.futures_margin_mode
        leverage = self.settings.futures_leverage
        position_sides = self._leverage_position_sides()

        if self.settings.dry_run:
            LOGGER.info(
                "DRY_RUN futures position_mode=%s margin=%s leverage=%sx pos_sides=%s",
                self.settings.futures_position_mode,
                margin_mode,
                leverage,
                position_sides,
            )
            return

        try:
            hedged = self.settings.futures_position_mode == "long_short"
            LOGGER.info("Setting futures position mode: hedged=%s", hedged)
            self.exchange.set_position_mode(hedged)

            for pos_side in position_sides:
                margin_params = self._margin_mode_params(pos_side, leverage)
                leverage_params = self._leverage_params(pos_side)

                LOGGER.info(
                    "Setting futures margin mode: symbol=%s margin=%s posSide=%s",
                    self.symbol,
                    margin_mode,
                    pos_side,
                )
                self.exchange.set_margin_mode(margin_mode, self.symbol, margin_params)

                LOGGER.info(
                    "Setting futures leverage: symbol=%s leverage=%sx margin=%s posSide=%s",
                    self.symbol,
                    leverage,
                    margin_mode,
                    pos_side,
                )
                self.exchange.set_leverage(leverage, self.symbol, leverage_params)
        except Exception:
            LOGGER.exception(
                "Futures margin/leverage configuration failed: symbol=%s margin=%s "
                "leverage=%s position_mode=%s pos_sides=%s",
                self.symbol,
                margin_mode,
                leverage,
                self.settings.futures_position_mode,
                position_sides,
            )
            raise

    def run_once(self) -> dict[str, Any] | list[dict[str, Any]] | None:
        breached, ratio = self.risk.margin_ratio_breached()
        if breached:
            LOGGER.warning(
                "Futures margin ratio %.4f is below %.4f. Closing all positions.",
                ratio,
                self.settings.margin_ratio_threshold,
            )
            return self.close_all_positions(reason="margin_ratio_breached")

        signal = self.fetch_signal()
        LOGGER.info(
            "Futures signal=%s prev_ma5=%.2f prev_ma20=%.2f ma5=%.2f ma20=%.2f rsi=%s",
            signal.signal,
            signal.previous_fast,
            signal.previous_slow,
            signal.current_fast,
            signal.current_slow,
            f"{signal.rsi:.2f}" if signal.rsi is not None else "n/a",
        )

        if signal.signal == "hold":
            return None

        equity = self.risk.assert_can_trade()
        price = self.fetch_last_price()
        contracts = self.contracts_for_notional(self.risk.max_order_notional(equity), price)

        if signal.signal == "buy":
            self.close_positions_by_direction("short")
            if self.has_open_position("long"):
                LOGGER.info("Long position already open; skipping duplicate long entry.")
                return None
            return self.open_long(contracts, price)

        self.close_positions_by_direction("long")
        if self.has_open_position("short"):
            LOGGER.info("Short position already open; skipping duplicate short entry.")
            return None
        return self.open_short(contracts, price)

    def fetch_signal(self) -> MovingAverageSignal:
        candles = self.exchange.fetch_ohlcv(
            self.symbol,
            timeframe=self.settings.timeframe,
            limit=self.settings.ohlcv_limit,
        )
        closes = [float(candle[4]) for candle in candles]
        return moving_average_cross_signal_with_rsi(
            closes,
            fast=5,
            slow=20,
            rsi_period=self.settings.rsi_period,
            rsi_overbought=self.settings.rsi_overbought,
            rsi_oversold=self.settings.rsi_oversold,
        )

    def fetch_last_price(self) -> float:
        ticker = self.exchange.fetch_ticker(self.symbol)
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise RiskLimitError(f"Unable to fetch last price for {self.symbol}.")
        return float(price)

    def open_long(self, contracts: float, entry_price: float | None = None) -> dict[str, Any]:
        entry_price = entry_price or self.fetch_last_price()
        return self._open_position("long", contracts, entry_price)

    def open_short(self, contracts: float, entry_price: float | None = None) -> dict[str, Any]:
        entry_price = entry_price or self.fetch_last_price()
        return self._open_position("short", contracts, entry_price)

    def close_long(self, contracts: float) -> dict[str, Any]:
        return self._close_position("long", contracts)

    def close_short(self, contracts: float) -> dict[str, Any]:
        return self._close_position("short", contracts)

    def close_all_positions(self, *, reason: str) -> list[dict[str, Any]]:
        orders = []
        for position in self.fetch_open_positions():
            LOGGER.warning("Closing %s position because %s.", position["side"], reason)
            orders.append(self._close_position(position["side"], position["contracts"]))
        return orders

    def close_positions_by_direction(self, direction: Direction) -> list[dict[str, Any]]:
        orders = []
        for position in self.fetch_open_positions():
            if position["side"] == direction:
                orders.append(self._close_position(direction, position["contracts"]))
        return orders

    def has_open_position(self, direction: Direction) -> bool:
        return any(position["side"] == direction for position in self.fetch_open_positions())

    def fetch_open_positions(self) -> list[dict[str, Any]]:
        positions = self.exchange.fetch_positions([self.symbol])
        parsed = []
        for position in positions:
            side = self._position_side(position)
            contracts = self._position_contracts(position)
            if side in {"long", "short"} and contracts > 0:
                parsed.append({"side": side, "contracts": contracts, "raw": position})
        return parsed

    def contracts_for_notional(self, notional: float, price: float) -> float:
        self.risk.assert_order_notional(notional)
        market = self.exchange.market(self.symbol)
        contract_size = float(market.get("contractSize") or 1)
        if price <= 0 or contract_size <= 0:
            raise RiskLimitError("Cannot calculate futures contracts from invalid price or contract size.")

        raw_contracts = notional / (price * contract_size)
        contracts = self._normalize_amount(raw_contracts)
        return contracts

    def _open_position(self, direction: Direction, contracts: float, entry_price: float) -> dict[str, Any]:
        side = "buy" if direction == "long" else "sell"
        stop_loss, take_profit = self._stop_loss_take_profit(direction, entry_price)
        params: dict[str, Any] = {
            "tdMode": self.settings.futures_margin_mode,
            "stopLoss": {
                "type": "market",
                "triggerPrice": stop_loss,
            },
            "takeProfit": {
                "type": "market",
                "triggerPrice": take_profit,
            },
        }
        params.update(self._position_mode_params(direction))

        if self.settings.dry_run:
            LOGGER.info(
                "DRY_RUN futures open_%s contracts=%s entry=%s sl=%s tp=%s params=%s",
                direction,
                contracts,
                entry_price,
                stop_loss,
                take_profit,
                params,
            )
            return {
                "dryRun": True,
                "symbol": self.symbol,
                "type": "market",
                "side": side,
                "direction": direction,
                "contracts": contracts,
                "entryPrice": entry_price,
                "params": params,
            }

        LOGGER.info(
            "Opening futures %s contracts=%s entry=%s stopLoss=%s takeProfit=%s",
            direction,
            contracts,
            entry_price,
            stop_loss,
            take_profit,
        )
        return self.exchange.create_order(self.symbol, "market", side, contracts, None, params)

    def _close_position(self, direction: Direction, contracts: float) -> dict[str, Any]:
        side = "sell" if direction == "long" else "buy"
        amount = self._normalize_amount(contracts)
        params: dict[str, Any] = {
            "tdMode": self.settings.futures_margin_mode,
            "reduceOnly": True,
        }
        params.update(self._position_mode_params(direction))

        if self.settings.dry_run:
            LOGGER.info(
                "DRY_RUN futures close_%s contracts=%s params=%s",
                direction,
                amount,
                params,
            )
            return {
                "dryRun": True,
                "symbol": self.symbol,
                "type": "market",
                "side": side,
                "direction": direction,
                "contracts": amount,
                "params": params,
            }

        LOGGER.info("Closing futures %s contracts=%s", direction, amount)
        return self.exchange.create_order(self.symbol, "market", side, amount, None, params)

    def _stop_loss_take_profit(self, direction: Direction, entry_price: float) -> tuple[float, float]:
        if direction == "long":
            stop_loss = entry_price * (1 - self.settings.futures_stop_loss_pct)
            take_profit = entry_price * (1 + self.settings.futures_take_profit_pct)
        else:
            stop_loss = entry_price * (1 + self.settings.futures_stop_loss_pct)
            take_profit = entry_price * (1 - self.settings.futures_take_profit_pct)

        return self._normalize_price(stop_loss), self._normalize_price(take_profit)

    def _position_mode_params(self, direction: Direction) -> dict[str, Any]:
        if self.settings.futures_position_mode == "long_short":
            return {"posSide": direction}
        return {}

    def _leverage_position_sides(self) -> list[str]:
        if self.settings.futures_position_mode == "long_short":
            return ["long", "short"]
        return ["net"]

    def _margin_mode_params(self, pos_side: str, leverage: int) -> dict[str, Any]:
        params: dict[str, Any] = {"lever": leverage}
        if self.settings.futures_margin_mode == "isolated":
            params["posSide"] = pos_side
        return params

    def _leverage_params(self, pos_side: str) -> dict[str, Any]:
        params: dict[str, Any] = {"mgnMode": self.settings.futures_margin_mode}
        if self.settings.futures_margin_mode == "isolated":
            params["posSide"] = pos_side
        return params

    def _normalize_amount(self, amount: float) -> float:
        normalized = float(self.exchange.amount_to_precision(self.symbol, amount))
        min_amount = ((self.exchange.market(self.symbol).get("limits") or {}).get("amount") or {}).get("min")
        if min_amount is not None and normalized < float(min_amount):
            raise RiskLimitError(f"Futures amount {normalized} is below exchange min amount {min_amount}.")
        if normalized <= 0:
            raise RiskLimitError("Futures amount must be greater than 0 after precision normalization.")
        return normalized

    def _normalize_price(self, price: float) -> float:
        normalized = float(self.exchange.price_to_precision(self.symbol, price))
        if normalized <= 0:
            raise RiskLimitError("Futures price must be greater than 0 after precision normalization.")
        return normalized

    @staticmethod
    def _position_side(position: dict[str, Any]) -> str | None:
        side = position.get("side")
        if side in {"long", "short"}:
            return side

        info = position.get("info") or {}
        pos_side = info.get("posSide")
        if pos_side in {"long", "short"}:
            return pos_side

        raw_pos = info.get("pos")
        try:
            pos = float(raw_pos)
        except (TypeError, ValueError):
            return None
        if pos > 0:
            return "long"
        if pos < 0:
            return "short"
        return None

    @staticmethod
    def _position_contracts(position: dict[str, Any]) -> float:
        contracts = position.get("contracts")
        if contracts is None:
            contracts = (position.get("info") or {}).get("pos")
        try:
            return abs(float(contracts or 0))
        except (TypeError, ValueError):
            return 0.0
