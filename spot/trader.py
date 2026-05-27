from __future__ import annotations

import logging
from typing import Any

from core.config import Settings
from core.exchange import create_okx_exchange
from core.risk import RiskLimitError, RiskManager
from core.strategy import MovingAverageSignal, moving_average_cross_signal_with_rsi


LOGGER = logging.getLogger(__name__)


class SpotTrader:
    def __init__(self, settings: Settings, *, symbol: str | None = None) -> None:
        self.settings = settings
        self.symbol = symbol or settings.spot_symbol
        self.exchange = create_okx_exchange(settings, "spot")
        self.exchange.load_markets()
        self.risk = RiskManager(settings, self.exchange)

    def run_once(self) -> dict[str, Any] | None:
        signal = self.fetch_signal()
        LOGGER.info(
            "Spot %s signal=%s prev_ma5=%.2f prev_ma20=%.2f ma5=%.2f ma20=%.2f rsi=%s",
            self.symbol,
            signal.signal,
            signal.previous_fast,
            signal.previous_slow,
            signal.current_fast,
            signal.current_slow,
            f"{signal.rsi:.2f}" if signal.rsi is not None else "n/a",
        )

        if signal.signal == "hold":
            return None

        price = self.fetch_last_price()
        equity = self.risk.assert_can_trade()
        amount = self.risk.amount_for_price(price, equity)

        if signal.signal == "buy":
            self.risk.assert_order_notional(amount * price, equity)
            return self.create_market_order("buy", amount)

        base_free = self.fetch_base_free_balance()
        sell_amount = min(base_free, amount)
        if sell_amount <= 0:
            LOGGER.info("Spot sell skipped because there is no free %s balance.", self.base_currency)
            return None

        self.risk.assert_order_notional(sell_amount * price, equity)
        return self.create_market_order("sell", sell_amount)

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

    def fetch_base_free_balance(self) -> float:
        balance = self.exchange.fetch_balance()
        free = balance.get("free") or {}
        return float(free.get(self.base_currency) or 0)

    @property
    def base_currency(self) -> str:
        market = self.exchange.market(self.symbol)
        return str(market["base"])

    def create_market_order(self, side: str, amount: float) -> dict[str, Any]:
        return self._create_order(order_type="market", side=side, amount=amount, price=None)

    def create_limit_order(self, side: str, amount: float, price: float) -> dict[str, Any]:
        return self._create_order(order_type="limit", side=side, amount=amount, price=price)

    def _create_order(
        self,
        *,
        order_type: str,
        side: str,
        amount: float,
        price: float | None,
    ) -> dict[str, Any]:
        amount_value = self._normalize_amount(amount)
        price_value = self._normalize_price(price) if price is not None else None
        params: dict[str, Any] = {"tdMode": "cash"}
        if side == "buy":
            params["tgtCcy"] = "base_ccy"

        if self.settings.dry_run:
            LOGGER.info(
                "DRY_RUN spot %s %s %s amount=%s price=%s params=%s",
                self.symbol,
                side,
                order_type,
                amount_value,
                price_value,
                params,
            )
            return {
                "dryRun": True,
                "symbol": self.symbol,
                "type": order_type,
                "side": side,
                "amount": amount_value,
                "price": price_value,
                "params": params,
            }

        LOGGER.info("Creating spot %s %s %s amount=%s price=%s", self.symbol, side, order_type, amount_value, price_value)
        return self.exchange.create_order(self.symbol, order_type, side, amount_value, price_value, params)

    def _normalize_amount(self, amount: float) -> float:
        normalized = float(self.exchange.amount_to_precision(self.symbol, amount))
        min_amount = ((self.exchange.market(self.symbol).get("limits") or {}).get("amount") or {}).get("min")
        if min_amount is not None and normalized < float(min_amount):
            raise RiskLimitError(f"Spot amount {normalized} is below exchange min amount {min_amount}.")
        if normalized <= 0:
            raise RiskLimitError("Spot amount must be greater than 0 after precision normalization.")
        return normalized

    def _normalize_price(self, price: float | None) -> float | None:
        if price is None:
            return None
        normalized = float(self.exchange.price_to_precision(self.symbol, price))
        if normalized <= 0:
            raise RiskLimitError("Spot price must be greater than 0 after precision normalization.")
        return normalized
