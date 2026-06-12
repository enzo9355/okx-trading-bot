"""Pair entries with exits in data/trades.csv and report realized round trips.

    .venv/bin/python scripts/report.py [path/to/trades.csv]

Spot: FIFO-pairs buys against sells per symbol.
Futures: pairs long/short opens against close_long/close_short rows (exit
logging was added at the same time as this script; older rows lack closes).
Gross PnL only — add ~0.1% taker fee per side mentally, or compare against the
fee column once exchange fills are recorded.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict, deque
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "trades.csv"

ENTRY_SIDES = {"buy": "sell", "long": "close_long", "short": "close_short"}


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"No trade log at {path}")
        return 1

    with path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle)]

    open_entries: dict[tuple[str, str], deque] = defaultdict(deque)
    round_trips: list[dict] = []

    for row in rows:
        key = (row["market"], row["symbol"])
        side = row["side"]
        price = float(row["price"] or 0)
        if side in ENTRY_SIDES:
            open_entries[key].append(row)
        else:
            # exit row: match against oldest entry whose exit side == this side
            queue = open_entries[key]
            matched = None
            for candidate in list(queue):
                if ENTRY_SIDES.get(candidate["side"]) == side:
                    matched = candidate
                    queue.remove(candidate)
                    break
            if not matched:
                continue
            entry_price = float(matched["price"] or 0)
            if entry_price <= 0 or price <= 0:
                continue
            direction = 1 if matched["side"] in ("buy", "long") else -1
            pnl_pct = direction * (price - entry_price) / entry_price
            round_trips.append(
                {
                    "market": row["market"],
                    "symbol": row["symbol"],
                    "entry": entry_price,
                    "exit": price,
                    "pnl_pct": pnl_pct,
                    "exit_reason": row.get("reason", ""),
                }
            )

    if not round_trips:
        print(f"Parsed {len(rows)} rows; no completed round trips yet.")
        return 0

    wins = [t for t in round_trips if t["pnl_pct"] > 0]
    total = sum(t["pnl_pct"] for t in round_trips)
    print(f"rows={len(rows)} round_trips={len(round_trips)} win_rate={len(wins)/len(round_trips):.1%}")
    print(f"sum_gross_pnl={total:+.2%} avg={total/len(round_trips):+.3%} (before fees ~0.2%/round trip)")
    print()
    per_symbol: dict[str, list[float]] = defaultdict(list)
    for t in round_trips:
        per_symbol[f"{t['market']}:{t['symbol']}"].append(t["pnl_pct"])
    for key in sorted(per_symbol):
        values = per_symbol[key]
        print(f"  {key:28s} n={len(values):3d} sum={sum(values):+.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
