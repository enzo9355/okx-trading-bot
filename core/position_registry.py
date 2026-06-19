from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

# One lock for the whole process: spot and futures workers share one registry file.
_REGISTRY_LOCK = threading.Lock()

# The count check, exchange order, and registry update must be one process-wide
# operation. Otherwise two symbol workers can both observe one free slot and
# exceed MAX_OPEN_POSITIONS before either records its new position.
_ENTRY_LOCK = threading.Lock()

# Positions smaller than this fraction of their original size are treated as closed
# (precision rounding leaves dust after sells).
_DUST_FRACTION = 0.01


class PositionRegistryError(RuntimeError):
    """Raised when ownership/risk state cannot be read or persisted safely."""


class PositionRegistry:
    """JSON-file-backed record of positions THIS BOT opened, plus stop-out cooldowns.

    Why this exists (and why we don't just ask the exchange):
    - The exchange knows balances, but not which part of a balance the bot bought
      versus what the owner already held. The spot stop-loss must only ever sell
      what the bot itself opened.
    - The exchange doesn't know our entry price per logical position once orders
      are batched/partially filled; we track a weighted average entry here.
    - Cooldowns after a stop-out are a bot-level concept the exchange has no
      notion of.

    Keyed by "market:symbol" (e.g. "spot:BTC/USDT", "futures:BTC/USDT:USDT").
    Persisted to disk so restarts don't forget open positions or active cooldowns.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # ── position lifecycle ────────────────────────────────────────────────

    def record_open(self, market: str, symbol: str, side: str, amount: float, price: float) -> None:
        """Record a new position, or accumulate into an existing same-side one
        with a weighted-average entry price."""
        key = self._key(market, symbol)
        with _REGISTRY_LOCK:
            state = self._load()
            positions = state.setdefault("positions", {})
            existing = positions.get(key)
            if existing and existing.get("side") == side:
                old_amount = float(existing["amount"])
                old_price = float(existing["entry_price"])
                new_amount = old_amount + amount
                entry = (old_amount * old_price + amount * price) / new_amount
                positions[key] = {
                    "side": side,
                    "amount": new_amount,
                    "entry_price": entry,
                    "opened_at": existing.get("opened_at"),
                }
            else:
                positions[key] = {
                    "side": side,
                    "amount": amount,
                    "entry_price": price,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
            self._save(state)

    def record_reduce(self, market: str, symbol: str, amount: float) -> None:
        """Reduce a position by a sold/closed amount; drop it entirely once only
        rounding dust remains."""
        key = self._key(market, symbol)
        with _REGISTRY_LOCK:
            state = self._load()
            positions = state.get("positions", {})
            existing = positions.get(key)
            if not existing:
                return
            original = float(existing["amount"])
            remaining = original - amount
            if remaining <= original * _DUST_FRACTION:
                positions.pop(key, None)
            else:
                existing["amount"] = remaining
            self._save(state)

    def record_close(self, market: str, symbol: str) -> None:
        key = self._key(market, symbol)
        with _REGISTRY_LOCK:
            state = self._load()
            if state.get("positions", {}).pop(key, None) is not None:
                self._save(state)

    def get(self, market: str, symbol: str) -> dict[str, Any] | None:
        with _REGISTRY_LOCK:
            return self._load().get("positions", {}).get(self._key(market, symbol))

    def open_count(self) -> int:
        with _REGISTRY_LOCK:
            return len(self._load().get("positions", {}))

    def has_position(self, market: str, symbol: str) -> bool:
        return self.get(market, symbol) is not None

    @contextmanager
    def entry_lock(self) -> Iterator[None]:
        """Serialize entry admission through order placement and persistence.

        The max-open-position limit is shared by every symbol worker. Holding
        this lock across the check, exchange call, and registry write prevents
        a check-then-act race from opening more positions than configured.
        """
        with _ENTRY_LOCK:
            yield

    # ── stop-out cooldown ─────────────────────────────────────────────────

    def set_cooldown(self, market: str, symbol: str, seconds: int) -> None:
        """Start a cooldown after a stop-out. New entries on this symbol are
        blocked until it expires — this is the anti-revenge-trading brake."""
        if seconds <= 0:
            return
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        key = self._key(market, symbol)
        with _REGISTRY_LOCK:
            state = self._load()
            state.setdefault("cooldowns", {})[key] = until.isoformat()
            self._save(state)

    def in_cooldown(self, market: str, symbol: str) -> bool:
        key = self._key(market, symbol)
        with _REGISTRY_LOCK:
            state = self._load()
            raw = state.get("cooldowns", {}).get(key)
            if not raw:
                return False
            try:
                until = datetime.fromisoformat(raw)
            except ValueError:
                state["cooldowns"].pop(key, None)
                self._save(state)
                return False
            if datetime.now(timezone.utc) >= until:
                state["cooldowns"].pop(key, None)
                self._save(state)
                return False
            return True

    # ── persistence ───────────────────────────────────────────────────────

    @staticmethod
    def _key(market: str, symbol: str) -> str:
        return f"{market}:{symbol}"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PositionRegistryError(
                f"Position registry {self.path} is unreadable; refusing to trade without ownership state."
            ) from exc
        if not isinstance(state, dict):
            raise PositionRegistryError(f"Position registry {self.path} must contain a JSON object.")
        for section in ("positions", "cooldowns"):
            if section in state and not isinstance(state[section], dict):
                raise PositionRegistryError(
                    f"Position registry {self.path} has an invalid {section!r} section."
                )
        return state

    def _save(self, state: dict[str, Any]) -> None:
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PositionRegistryError(
                f"Failed to persist position registry to {self.path}; refusing to continue unsafely."
            ) from exc
