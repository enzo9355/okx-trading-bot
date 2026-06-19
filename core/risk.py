from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import Settings


class RiskLimitError(RuntimeError):
    """Raised when the whole bot must stop because risk state is not trustworthy."""


class EntryHalted(RiskLimitError):
    """Raised when new exposure is blocked but protective workers must keep running.

    Daily loss limits should stop entries, not stop spot-loss monitoring or
    futures reconciliation. The worker loop catches this subtype without setting
    the process shutdown event.
    """


class OrderRejected(RuntimeError):
    """Raised when a SINGLE order cannot be placed (notional too large after
    precision rounding, amount below the exchange minimum, price temporarily
    unavailable, etc.). The bot logs it, skips this one order, and keeps running.

    This is deliberately separate from RiskLimitError so that a per-order problem
    on one symbol never takes the entire bot (all symbols, spot + futures) offline."""


_STATE_LOCK = threading.Lock()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RiskManager:
    def __init__(self, settings: Settings, exchange: Any, *, state_file: Path | None = None) -> None:
        self.settings = settings
        self.exchange = exchange
        self.state_file = state_file or settings.state_file
        self.state: dict[str, Any] = self._load_state()

    def assert_can_trade(self) -> float:
        equity = self.fetch_equity()
        self._sync_daily_loss_state(equity)
        return equity

    def assert_order_notional(self, notional: float, equity: float | None = None) -> None:
        equity = equity if equity is not None else self.assert_can_trade()
        percentage_cap = equity * self.settings.max_position_pct
        # The sized amount is rounded to the exchange's precision before the order
        # is sent, which can nudge `amount * price` a hair above max_notional even
        # when it was sized to be exactly equal. Allow a tiny relative tolerance so
        # that this rounding dust does not reject (and previously crash) the order.
        tolerance = max(percentage_cap * 1e-3, 1e-8)
        if notional > percentage_cap + tolerance:
            raise OrderRejected(
                f"Order notional {notional:.4f} exceeds max allowed {percentage_cap:.4f} "
                f"({self.settings.max_position_pct:.2%} of equity)."
            )
        # Optional absolute cap in quote currency. Unlike the % cap, this does not
        # grow with equity — it is a hard ceiling per order regardless of account
        # size. 0 disables it.
        hard_cap = self.settings.max_order_notional_usdt
        if hard_cap > 0 and notional > hard_cap:
            raise OrderRejected(
                f"Order notional {notional:.4f} exceeds hard cap MAX_ORDER_NOTIONAL_USDT={hard_cap:.2f}."
            )

    def max_order_notional(self, equity: float | None = None) -> float:
        equity = equity if equity is not None else self.fetch_equity()
        percentage_cap = equity * self.settings.max_position_pct
        hard_cap = self.settings.max_order_notional_usdt
        if hard_cap > 0:
            return min(percentage_cap, hard_cap)
        return percentage_cap

    def amount_for_price(self, price: float, equity: float | None = None) -> float:
        if price <= 0:
            raise OrderRejected("Cannot size a position because price is not greater than 0.")
        return self.max_order_notional(equity) / price

    def fetch_equity(self) -> float:
        balance = self.exchange.fetch_balance()
        equity = self.extract_equity(balance, self.settings.quote_currency)
        if equity is None or equity <= 0:
            raise OrderRejected("Unable to read a positive account equity from OKX balance.")
        return equity

    def fetch_margin_ratio(self) -> float | None:
        balance = self.exchange.fetch_balance()
        return self.extract_margin_ratio(balance)

    def margin_ratio_breached(self) -> tuple[bool, float | None]:
        ratio = self.fetch_margin_ratio()
        if ratio is None:
            return False, None
        return ratio < self.settings.margin_ratio_threshold, ratio

    @staticmethod
    def extract_equity(balance: dict[str, Any], quote_currency: str) -> float | None:
        # OKX `totalEq` is account-wide equity expressed in USD terms. Prefer it
        # over ccxt's `total[USDT]`, which is only the USDT currency balance and
        # falls when USDT is exchanged for spot assets. Using the currency balance
        # would misclassify asset allocation as a trading loss.
        info = balance.get("info") or {}
        for account in info.get("data") or []:
            for key in ("totalEq", "adjEq"):
                value = _safe_float(account.get(key))
                if value is not None and value > 0:
                    return value

        for section in ("total", "free"):
            values = balance.get(section) or {}
            value = _safe_float(values.get(quote_currency))
            if value is not None and value > 0:
                return value

        for account in info.get("data") or []:
            for detail in account.get("details") or []:
                if detail.get("ccy") == quote_currency:
                    for key in ("eq", "cashBal", "availEq"):
                        value = _safe_float(detail.get(key))
                        if value is not None and value > 0:
                            return value

        return None

    @staticmethod
    def extract_margin_ratio(balance: dict[str, Any]) -> float | None:
        info = balance.get("info") or {}
        for account in info.get("data") or []:
            for key in ("mgnRatio", "marginRatio"):
                value = _safe_float(account.get(key))
                if value is not None:
                    return value

            for detail in account.get("details") or []:
                for key in ("mgnRatio", "marginRatio"):
                    value = _safe_float(detail.get(key))
                    if value is not None:
                        return value

        return None

    def _sync_daily_loss_state(self, equity: float) -> None:
        with _STATE_LOCK:
            self.state = self._load_state()
            today = datetime.now().date().isoformat()
            if self.state.get("date") != today:
                self.state = {
                    "date": today,
                    "starting_equity": equity,
                    "stopped": False,
                }
                self._save_state()

            starting_equity = _safe_float(self.state.get("starting_equity")) or equity
            if starting_equity <= 0:
                self.state["starting_equity"] = equity
                self.state["stopped"] = False
                self._save_state()
                return

            drawdown = max((starting_equity - equity) / starting_equity, 0.0)
            self.state["last_equity"] = equity
            self.state["daily_drawdown"] = drawdown

            if drawdown >= self.settings.daily_max_loss_pct:
                self.state["stopped"] = True
                self._save_state()
                raise EntryHalted(
                    f"Daily loss limit reached: {drawdown:.2%} >= "
                    f"{self.settings.daily_max_loss_pct:.2%}. New entries stopped."
                )

            if self.state.get("stopped"):
                raise EntryHalted(
                    "New entries are stopped for today because the daily loss limit was reached."
                )

            self._save_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RiskLimitError(
                f"Risk state file {self.state_file} is unreadable; refusing to trade."
            ) from exc
        if not isinstance(state, dict):
            raise RiskLimitError(
                f"Risk state file {self.state_file} has an invalid root value; refusing to trade."
            )
        return state

    def _save_state(self) -> None:
        """Atomically persist the circuit-breaker state.

        A crash during an in-place write must not erase the daily-loss baseline,
        because restarting from an empty state would silently re-enable trading.
        """
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_name(f".{self.state_file.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_file)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RiskLimitError(
                f"Risk state file {self.state_file} could not be persisted; refusing to trade."
            ) from exc
