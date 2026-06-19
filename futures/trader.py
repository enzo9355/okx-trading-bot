from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypeVar

import ccxt

from core.config import Settings
from core.exchange import create_okx_exchange
from core.position_registry import PositionRegistry
from core.risk import OrderRejected, RiskLimitError, RiskManager
from core.strategy import MovingAverageSignal, filtered_ma_cross_signal
from core.trade_logger import TradeLogger


LOGGER = logging.getLogger(__name__)
Direction = Literal["long", "short"]
T = TypeVar("T")

# When several futures workers start at the same time (e.g. systemd restart, or
# `--mode both` spinning up 5 symbols at once) they each call set_position_mode /
# set_margin_mode / set_leverage on OKX in lockstep, which trips OKX's rate limit
# (error code 50011 "Too Many Requests") and crashes the worker. systemd then
# restarts immediately, hitting the same wall, producing a restart storm.
# These two helpers stagger startup and retry rate-limited setup calls.

_RATE_LIMIT_RETRIES = 5
_RATE_LIMIT_BACKOFF_BASE_SECS = 2.0
_STARTUP_JITTER_MAX_SECS = 4.0


def _is_rate_limit_error(exc: BaseException) -> bool:
    """True if the exception looks like an OKX 50011 'Too Many Requests' error."""
    if isinstance(exc, ccxt.RateLimitExceeded):
        return True
    text = str(exc)
    return "50011" in text or "Too Many Requests" in text


def _retry_on_rate_limit(operation: str, func: Callable[[], T]) -> T:
    """Call `func()`; if OKX returns a rate-limit error, wait and retry with
    exponential backoff. Only rate-limit errors are retried — any other error
    propagates immediately."""
    last_exc: BaseException | None = None
    for attempt in range(1, _RATE_LIMIT_RETRIES + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 — narrowed by _is_rate_limit_error
            if not _is_rate_limit_error(exc):
                raise
            last_exc = exc
            if attempt == _RATE_LIMIT_RETRIES:
                break
            wait_secs = _RATE_LIMIT_BACKOFF_BASE_SECS * (2 ** (attempt - 1))
            wait_secs += random.uniform(0, 1.0)  # jitter so retries don't sync
            LOGGER.warning(
                "OKX rate limit hit during %s (attempt %d/%d), waiting %.1fs...",
                operation,
                attempt,
                _RATE_LIMIT_RETRIES,
                wait_secs,
            )
            time.sleep(wait_secs)
    assert last_exc is not None
    raise last_exc


class FuturesTrader:
    def __init__(self, settings: Settings, *, symbol: str | None = None) -> None:
        try:
            self.settings = settings
            self.symbol = symbol or settings.futures_symbol
            self.exchange = create_okx_exchange(settings, "swap")
            self.exchange.load_markets()
            self.risk = RiskManager(settings, self.exchange)
            self.trade_logger = TradeLogger(settings.trade_log_file)
            self.positions = PositionRegistry(settings.position_registry_file)
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

        # Random startup jitter so that when N futures workers all spin up at the
        # same instant, they don't all hammer set_position_mode / set_margin_mode /
        # set_leverage on OKX in the same millisecond. Spreads the bursts out.
        jitter = random.uniform(0, _STARTUP_JITTER_MAX_SECS)
        LOGGER.info("Futures startup jitter for %s: sleeping %.2fs", self.symbol, jitter)
        time.sleep(jitter)

        try:
            hedged = self.settings.futures_position_mode == "long_short"
            LOGGER.info("Setting futures position mode: hedged=%s", hedged)
            _retry_on_rate_limit(
                f"set_position_mode({self.symbol})",
                lambda: self.exchange.set_position_mode(hedged),
            )

            for pos_side in position_sides:
                margin_params = self._margin_mode_params(pos_side, leverage)
                leverage_params = self._leverage_params(pos_side)

                LOGGER.info(
                    "Setting futures margin mode: symbol=%s margin=%s posSide=%s",
                    self.symbol,
                    margin_mode,
                    pos_side,
                )
                _retry_on_rate_limit(
                    f"set_margin_mode({self.symbol})",
                    lambda mp=margin_params: self.exchange.set_margin_mode(margin_mode, self.symbol, mp),
                )

                LOGGER.info(
                    "Setting futures leverage: symbol=%s leverage=%sx margin=%s posSide=%s",
                    self.symbol,
                    leverage,
                    margin_mode,
                    pos_side,
                )
                _retry_on_rate_limit(
                    f"set_leverage({self.symbol})",
                    lambda lp=leverage_params: self.exchange.set_leverage(leverage, self.symbol, lp),
                )
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
        breached, ratio = self.risk.margin_ratio_breached(self.symbol)
        if breached:
            LOGGER.warning(
                "Futures margin ratio %.4f is at or above %.4f. Closing all positions.",
                ratio,
                self.settings.margin_ratio_threshold,
            )
            return self.close_all_positions(reason="margin_ratio_breached")

        signal = self.fetch_signal()
        LOGGER.info(
            "Futures %s signal=%s reason=%s prev_ma5=%.2f prev_ma20=%.2f ma5=%.2f ma20=%.2f rsi=%s atr_pct=%s slow_slope_pct=%s",
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

        self._reconcile_stop_outs(signal)

        if signal.signal == "hold":
            return None

        price = self.fetch_last_price()

        if signal.signal == "buy":
            closed = self.close_positions_by_direction("short")
            if closed:
                self.positions.record_close("futures", self.symbol)
                for closed_contracts, close_order in closed:
                    self._record_trade(
                        signal,
                        "close_short",
                        closed_contracts,
                        price,
                        close_order,
                        reason="signal_flip",
                    )
            if self.has_open_position("long"):
                LOGGER.info("Long position already open; skipping duplicate long entry.")
                return None
            with self.positions.entry_lock():
                if not self._entry_allowed():
                    return None
                equity = self.risk.assert_can_trade()
                contracts = self.contracts_for_notional(self.risk.max_order_notional(equity), price)
                order = self.open_long(contracts, price)
                self.positions.record_open("futures", self.symbol, "long", contracts, price)
                self._record_trade(signal, "long", contracts, price, order)
                return order

        closed = self.close_positions_by_direction("long")
        if closed:
            self.positions.record_close("futures", self.symbol)
            for closed_contracts, close_order in closed:
                self._record_trade(
                    signal,
                    "close_long",
                    closed_contracts,
                    price,
                    close_order,
                    reason="signal_flip",
                )
        if self.has_open_position("short"):
            LOGGER.info("Short position already open; skipping duplicate short entry.")
            return None
        with self.positions.entry_lock():
            if not self._entry_allowed():
                return None
            equity = self.risk.assert_can_trade()
            contracts = self.contracts_for_notional(self.risk.max_order_notional(equity), price)
            order = self.open_short(contracts, price)
            self.positions.record_open("futures", self.symbol, "short", contracts, price)
            self._record_trade(signal, "short", contracts, price, order)
            return order

    def _entry_allowed(self) -> bool:
        """Gate new futures entries on the stop-out cooldown and the global
        max-open-positions cap (shared with spot via the registry file)."""
        if self.positions.in_cooldown("futures", self.symbol):
            LOGGER.info("Futures %s entry skipped: in post-stop-out cooldown.", self.symbol)
            return False
        if (
            not self.positions.has_position("futures", self.symbol)
            and self.positions.open_count() >= self.settings.max_open_positions
        ):
            LOGGER.info(
                "Futures %s entry skipped: max open positions reached (%d).",
                self.symbol,
                self.settings.max_open_positions,
            )
            return False
        return True

    def _reconcile_stop_outs(self, signal: MovingAverageSignal) -> None:
        """Detect exchange-side stop-loss/take-profit fills.

        The futures SL/TP execute on OKX's servers, so the bot only learns about
        them by noticing the position is gone. When the registry says we have a
        position but the exchange says we don't, infer a stop-out (or TP fill),
        log it, clear the registry entry, and start the cooldown so the very
        next cross signal can't immediately revenge-enter the same symbol.
        """
        if self.settings.dry_run:
            return
        registered = self.positions.get("futures", self.symbol)
        if not registered:
            return
        side = str(registered.get("side"))
        if side in ("long", "short") and not self.has_open_position(side):  # type: ignore[arg-type]
            LOGGER.warning(
                "Futures %s %s position closed on exchange (stop-loss/take-profit fill inferred). "
                "Starting %ds cooldown.",
                self.symbol,
                side,
                self.settings.stop_out_cooldown_seconds,
            )
            price = self.fetch_last_price()
            self._record_trade(
                signal,
                f"close_{side}",
                float(registered.get("amount") or 0),
                price,
                None,
                reason="stop_out",
            )
            self.positions.record_close("futures", self.symbol)
            self.positions.set_cooldown("futures", self.symbol, self.settings.stop_out_cooldown_seconds)

    def _record_trade(
        self,
        signal: MovingAverageSignal,
        side: str,
        contracts: float,
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
            market="futures",
            symbol=self.symbol,
            side=side,
            reason=reason or signal.reason,
            amount=contracts,
            price=price,
            notional="",  # futures notional depends on contract size; left blank
            rsi=signal.rsi,
            atr_pct=signal.atr_pct,
            slow_slope_pct=signal.slow_slope_pct,
            order_id=order_id,
            sandbox=self.settings.sandbox_mode,
            dry_run=self.settings.dry_run,
        )

    def fetch_signal(self) -> MovingAverageSignal:
        candles = self.exchange.fetch_ohlcv(
            self.symbol,
            timeframe=self.settings.timeframe,
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

    def close_positions_by_direction(self, direction: Direction) -> list[tuple[float, dict[str, Any]]]:
        """Close one direction and retain its actual contract count for logging."""
        orders: list[tuple[float, dict[str, Any]]] = []
        for position in self.fetch_open_positions():
            if position["side"] == direction:
                contracts = float(position["contracts"])
                orders.append((contracts, self._close_position(direction, contracts)))
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
            raise OrderRejected("Cannot calculate futures contracts from invalid price or contract size.")

        raw_contracts = notional / (price * contract_size)
        contracts = self._normalize_amount(raw_contracts)
        return contracts

    def _open_position(self, direction: Direction, contracts: float, entry_price: float) -> dict[str, Any]:
        side = "buy" if direction == "long" else "sell"
        stop_loss, take_profit = self._stop_loss_take_profit(direction, entry_price)
        params: dict[str, Any] = {
            "tdMode": self.settings.futures_margin_mode,
        }
        # Only attach SL/TP if the pct is non-zero (0 = disabled by user)
        if stop_loss > 0:
            params["stopLoss"] = {
                "type": "market",
                "triggerPrice": stop_loss,
            }
        if take_profit > 0:
            params["takeProfit"] = {
                "type": "market",
                "triggerPrice": take_profit,
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
        # Returns (stop_loss, take_profit). A value of 0.0 means the feature is disabled.
        if self.settings.futures_stop_loss_pct == 0 and self.settings.futures_take_profit_pct == 0:
            return 0.0, 0.0

        if direction == "long":
            stop_loss = (
                self._normalize_price(entry_price * (1 - self.settings.futures_stop_loss_pct))
                if self.settings.futures_stop_loss_pct > 0
                else 0.0
            )
            take_profit = (
                self._normalize_price(entry_price * (1 + self.settings.futures_take_profit_pct))
                if self.settings.futures_take_profit_pct > 0
                else 0.0
            )
        else:
            stop_loss = (
                self._normalize_price(entry_price * (1 + self.settings.futures_stop_loss_pct))
                if self.settings.futures_stop_loss_pct > 0
                else 0.0
            )
            take_profit = (
                self._normalize_price(entry_price * (1 - self.settings.futures_take_profit_pct))
                if self.settings.futures_take_profit_pct > 0
                else 0.0
            )

        return stop_loss, take_profit

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
            raise OrderRejected(f"Futures amount {normalized} is below exchange min amount {min_amount}.")
        if normalized <= 0:
            raise OrderRejected("Futures amount must be greater than 0 after precision normalization.")
        return normalized

    def _normalize_price(self, price: float) -> float:
        normalized = float(self.exchange.price_to_precision(self.symbol, price))
        if normalized <= 0:
            raise OrderRejected("Futures price must be greater than 0 after precision normalization.")
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
