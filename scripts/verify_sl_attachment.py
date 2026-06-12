"""Verify that the futures stop-loss actually attaches on OKX (issue C2).

The bot sends `params["stopLoss"] = {"type": "market", "triggerPrice": ...}`
through ccxt's unified API. If a ccxt/OKX version mismatch silently drops it,
a live position would be running 3x leverage with NO stop. This script proves
attachment with one tiny real order — in the DEMO account only.

    .venv/bin/python scripts/verify_sl_attachment.py

Refuses to run unless SANDBOX_MODE=true. Always attempts to close the test
position, even if the check fails.
"""

from __future__ import annotations

import logging
import sys

sys.path.insert(0, ".")  # allow running from repo root

from core.config import Settings  # noqa: E402
from futures.trader import FuturesTrader  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("verify_sl")


def main() -> int:
    settings = Settings.from_env().with_overrides(dry_run=False)
    if not settings.sandbox_mode:
        LOGGER.error("SANDBOX_MODE is not true. This script only runs against the demo account.")
        return 1
    settings.require_credentials()

    trader = FuturesTrader(settings, symbol=settings.futures_symbol)
    inst_id = trader.exchange.market(trader.symbol)["id"]

    price = trader.fetch_last_price()
    contracts = trader.contracts_for_notional(trader.risk.max_order_notional(), price)
    LOGGER.info("Opening test long: %s contracts=%s price=%s", trader.symbol, contracts, price)

    exit_code = 1
    try:
        trader.open_long(contracts, price)

        pending = trader.exchange.private_get_trade_orders_algo_pending(
            {"ordType": "conditional", "instId": inst_id}
        )
        algos = pending.get("data") or []
        with_sl = [a for a in algos if a.get("slTriggerPx")]
        if with_sl:
            LOGGER.info("PASS: stop-loss attached. slTriggerPx=%s", with_sl[0]["slTriggerPx"])
            exit_code = 0
        else:
            LOGGER.error(
                "FAIL: position opened but NO conditional stop order found on OKX. "
                "Do NOT go live until this is fixed (check ccxt version / param format)."
            )
    finally:
        LOGGER.info("Cleaning up: closing test position.")
        try:
            trader.close_all_positions(reason="sl_verification_cleanup")
        except Exception:
            LOGGER.exception("Cleanup failed — close the demo position manually in OKX.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
