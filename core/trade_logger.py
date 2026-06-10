from __future__ import annotations

import csv
import logging
import threading
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Lock so concurrent spot/futures worker threads don't interleave CSV rows.
_WRITE_LOCK = threading.Lock()

FIELDNAMES = [
    "timestamp",
    "market",
    "symbol",
    "side",
    "reason",
    "amount",
    "price",
    "notional",
    "rsi",
    "atr_pct",
    "slow_slope_pct",
    "order_id",
    "sandbox",
    "dry_run",
]


class TradeLogger:
    """Appends one row per order to a CSV file.

    This is the foundation for any future performance reporting or multi-strategy
    evaluation: before you can decide whether a new strategy is better, you need a
    record of what the current one actually did. Records both real and dry-run
    orders (distinguished by the `dry_run` column) so the dataset keeps growing
    even while you are still observing in dry-run mode.

    Note: `data/` is git-ignored, so this file stays local and is never pushed to
    GitHub. That is intentional — trade records can be sensitive and the repo is public.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(self, **fields: Any) -> None:
        row = {name: self._format(fields.get(name)) for name in FIELDNAMES}
        try:
            with _WRITE_LOCK:
                is_new = not self.path.exists()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
                    if is_new:
                        writer.writeheader()
                    writer.writerow(row)
        except OSError as exc:
            # Logging a trade must never crash the trader; just warn and move on.
            LOGGER.warning("Failed to write trade record to %s: %s", self.path, exc)

    @staticmethod
    def _format(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return value
