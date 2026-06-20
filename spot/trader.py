from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.config import Settings
from core.exchange import create_okx_exchange
from core.market_data import fetch_closed_ohlcv
from core.position_registry import PositionRegistry
from core.risk import OrderRejected, RiskLimitError, RiskManager
from core.strategy import MovingAverageSignal, filtered_ma_cross_signal
from core.trade_logger import TradeLogger


LOGGER = logging.getLogger(__name__)


class SpotTrader:
    def __init__(self, settings: Settings, *, symbol: str | None = None) -> None:
        self.settings = settings
        self.symbol = symbol or settings.spot_symbol
        self.exchange = create_okx_exchange(settings, "spot")
        self.exchange.load_markets()
        self.risk = RiskManager(settings, self.exchange)
        self.trade_logger = TradeLogger(settings.trade_log_file)
        self.positions = PositionRegistry.for_mode(
            settings.position_registry_file,
            dry_run=settings.dry_run,
        )

    def run_once(self) -> dict[str, Any] | None:
        signal = self.fetch_signal()
        LOGGER.info(
            "Spot %s signal=%s reason=%s prev_ma5=%.2f prev_ma20=%.2f ma5=%.2f ma20=%.2f rsi=%s atr_pct=%s slow_slope_pct=%s",
            self.symbol,
            signal.signal,
            signal.reason,
            signal.previous_fast,
            signal.previous_slow,
            signal.current_fast,
            signal.current_slow,
            f"{signal.rsi:.2f}" if signal.rsi is not None else "n/a",
            f"{signal.atr_pct:.4%}" if signal.atr_pct is not None else "n/a",
            f"{signal.slow_slope_pct:.4%}" if signal.slow_slope_pct is not None else "n/a",
        )

        # A protective exit has priority over every strategy signal. In
        # particular, a transient buy signal must not bypass a breached stop and
        # immediately increase the same position.
        if self._check_stop_loss(signal):
            return None

        if signal.signal == "hold":
            return None

        price = self.fetch_last_price()

        if signal.signal == "buy":
            if self.positions.in_cooldown("spot", self.symbol):
                LOGGER.info("Spot %s buy skipped: in post-stop-out cooldown.", self.symbol)
                return None
            equity = self.risk.assert_can_trade()
            amount = self.risk.amount_for_price(price, equity)
            self.risk.assert_order_notional(amount * price, equity)
            with self.positions.entry_slot(
                "spot",
                self.symbol,
                self.settings.max_open_positions,
            ) as allowed:
                if not allowed:
                    LOGGER.info(
                        "Spot %s buy skipped: max open positions reached (%d).",
                        self.symbol,
                        self.settings.max_open_positions,
                    )
                    return None
                order = self.create_market_order("buy", amount)
                filled, average = self._execution_details(order, amount, price)
                self.positions.record_open("spot", self.symbol, "long", filled, average)
                self._record_trade(signal, "buy", filled, average, order)
                return order

        position = self.positions.get("spot", self.symbol)
        if not position:
            LOGGER.info(
                "Spot %s sell skipped: registry has no bot-opened position; wallet holdings are untouched.",
                self.symbol,
            )
            return None
        base_free = self.fetch_base_free_balance()
        sell_amount = min(base_free, float(position["amount"]))
        if sell_amount <= 0:
            LOGGER.info("Spot sell skipped because there is no free %s balance.", self.base_currency)
            return None

        # This is a risk-reducing exit. Entry caps and the daily-loss circuit
        # breaker must never prevent the bot from closing its own position.
        order = self.create_market_order("sell", sell_amount)
        filled, average = self._execution_details(order, sell_amount, price)
        self.positions.record_reduce("spot", self.symbol, filled)
        self._record_trade(signal, "sell", filled, average, order)
        return order

    def _check_stop_loss(self, signal: MovingAverageSignal) -> bool:
        """Hard stop for bot-opened spot positions.

        The futures side has an exchange-attached stop; spot previously had NO
        protective exit at all (exit only on an opposite MA cross). This check
        runs every cycle: if the price has fallen SPOT_STOP_LOSS_PCT below the
        weighted-average entry of the position THIS BOT opened, close that
        position at market and start the stop-out cooldown.

        Granularity note: checked once per trade interval (default 60s), so the
        realized stop price can be worse than the configured level in a fast
        crash. That is still categorically safer than no stop. Only sells the
        registered (bot-opened) amount — never pre-existing wallet holdings.

        Returns True once a breached stop has been handled, even if no free
        balance was available. The caller must not process another signal in the
        same cycle after a stop-out decision.
        """
        if self.settings.spot_stop_loss_pct <= 0:
            return False
        position = self.positions.get("spot", self.symbol)
        if not position:
            return False
        entry = float(position["entry_price"])
        stop_price = entry * (1 - self.settings.spot_stop_loss_pct)
        price = self.fetch_last_price()
        if price > stop_price:
            return False

        base_free = self.fetch_base_free_balance()
        sell_amount = min(base_free, float(position["amount"]))
        LOGGER.warning(
            "Spot %s STOP LOSS triggered: price=%.8g <= stop=%.8g (entry=%.8g). Selling %.8g.",
            self.symbol,
            price,
            stop_price,
            entry,
            sell_amount,
        )
        if sell_amount > 0:
            order = self.create_market_order("sell", sell_amount)
            filled, average = self._execution_details(order, sell_amount, price)
            self._record_trade(signal, "sell", filled, average, order, reason="stop_loss")
            self.positions.record_reduce("spot", self.symbol, filled)
        else:
            # No asset remains available to sell, so the stale bot position must
            # not keep triggering the same stop forever.
            self.positions.record_close("spot", self.symbol)
        self.positions.set_cooldown("spot", self.symbol, self.settings.stop_out_cooldown_seconds)
        return True

    @staticmethod
    def _execution_details(
        order: dict[str, Any] | None,
        fallback_amount: float,
        fallback_price: float,
    ) -> tuple[float, float]:
        """Use exchange-confirmed fills for registry and audit records.

        Requested market-order size can differ from the filled size because of
        precision, partial fills, or fees. Falling back keeps dry-run and sparse
        exchange responses usable without overstating a known fill.
        """
        if not isinstance(order, dict):
            return fallback_amount, fallback_price
        amount = float(order.get("filled") or order.get("amount") or fallback_amount)
        price = float(order.get("average") or order.get("price") or fallback_price)
        return amount, price

    def _record_trade(
        self,
        signal: MovingAverageSignal,
        side: str,
        amount: float,
        price: float,
        order: dict[str, Any] | None,
        *,
        reason: str | None = None,
    ) -> None:
        order_id = ""
        if isinstance(order, dict):
            order_id = order.get("id") or ("dry_run" if order.get("dryRun") else "")
        self.trade_logger.record(
            timestamp=datetime.now(timezone.utc).isoformat(),
            market="spot",
            symbol=self.symbol,
            side=side,
            reason=reason or signal.reason,
            amount=amount,
            price=price,
            notional=amount * price,
            rsi=signal.rsi,
            atr_pct=signal.atr_pct,
            slow_slope_pct=signal.slow_slope_pct,
            order_id=order_id,
            sandbox=self.settings.sandbox_mode,
            dry_run=self.settings.dry_run,
        )

    def fetch_signal(self) -> MovingAverageSignal:
        candles = fetch_closed_ohlcv(
            self.exchange,
            self.symbol,
            self.settings.timeframe,
            limit=self.settings.ohlcv_limit,
        )
        highs = [float(candle[2]) for candle in candles]
        lows = [float(candle[3]) for candle in candles]
        closes = [float(candle[4]) for candle in candles]
        return filtered_ma_cross_signal(
            highs,
            lows,
            closes,
            fast=5,
            slow=20,
            rsi_period=self.settings.rsi_period,
            rsi_overbought=self.settings.rsi_overbought,
            rsi_oversold=self.settings.rsi_oversold,
            atr_period=self.settings.atr_period,
            min_atr_pct=self.settings.atr_min_pct,
            max_atr_pct=self.settings.atr_max_pct,
            min_slow_slope_pct=self.settings.ma_min_trend_slope_pct,
        )

    def fetch_last_price(self) -> float:
        ticker = self.exchange.fetch_ticker(self.symbol)
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise OrderRejected(f"Unable to fetch last price for {self.symbol}.")
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
            raise OrderRejected(f"Spot amount {normalized} is below exchange min amount {min_amount}.")
        if normalized <= 0:
            raise OrderRejected("Spot amount must be greater than 0 after precision normalization.")
        return normalized

    def _normalize_price(self, price: float | None) -> float | None:
        if price is None:
            return None
        normalized = float(self.exchange.price_to_precision(self.symbol, price))
        if normalized <= 0:
            raise OrderRejected("Spot price must be greater than 0 after precision normalization.")
        return normalized
