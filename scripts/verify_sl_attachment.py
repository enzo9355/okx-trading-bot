"""Verify that the futures stop-loss actually attaches on OKX (issue C2).

The bot sends `params["stopLoss"] = {"type": "market", "triggerPrice": ...}`
through ccxt's unified API. If a ccxt/OKX version mismatch silently drops it,
a live position would be running 3x leverage with NO stop. This script proves
attachment with one tiny real order — in the DEMO account only.

    .venv/bin/python scripts/verify_sl_attachment.py

Refuses to run unless SANDBOX_MODE=true and the symbol has no existing
position or pending conditional order. Only closes the position opened here.
"""

from __future__ import annotations

import logging
import sys
import time
from decimal import Decimal, InvalidOperation

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
    market = trader.exchange.market(trader.symbol)
    inst_id = market["id"]

    if trader.fetch_open_positions():
        LOGGER.error("Refusing to run: %s already has an open position.", trader.symbol)
        return 1
    pending = trader.exchange.private_get_trade_orders_algo_pending(
        {"ordType": "conditional", "instId": inst_id}
    )
    if any(algo.get("instId") == inst_id for algo in pending.get("data") or []):
        LOGGER.error("Refusing to run: %s already has a pending conditional order.", trader.symbol)
        return 1

    price = trader.fetch_last_price()
    contracts = (market.get("limits", {}).get("amount", {}).get("min"))
    if contracts is None or contracts <= 0:
        LOGGER.error("Exchange did not provide a valid minimum order amount.")
        return 1
    stop_loss, _ = trader._stop_loss_take_profit("long", price)
    if stop_loss <= 0:
        LOGGER.error("FUTURES_STOP_LOSS_PCT must be greater than zero.")
        return 1
    LOGGER.info("Opening test long: %s contracts=%s price=%s", trader.symbol, contracts, price)

    exit_code = 1
    matched = None
    opened = False
    opened_at = int(time.time() * 1000)
    try:
        trader.open_long(contracts, price)
        opened = True

        for attempt in range(10):
            pending = trader.exchange.private_get_trade_orders_algo_pending(
                {"ordType": "conditional", "instId": inst_id}
            )
            for algo in pending.get("data") or []:
                try:
                    exact_stop = Decimal(str(algo.get("slTriggerPx"))) == Decimal(str(stop_loss))
                    new_order = int(algo.get("cTime", 0)) >= opened_at
                except (InvalidOperation, TypeError, ValueError):
                    continue
                if (
                    algo.get("instId") == inst_id
                    and algo.get("side") == "sell"
                    and exact_stop
                    and new_order
                ):
                    matched = algo
                    break
            if matched:
                break
            if attempt < 9:
                time.sleep(1)

        if matched:
            LOGGER.info("PASS: exact new stop-loss attached. slTriggerPx=%s", matched["slTriggerPx"])
            exit_code = 0
        else:
            LOGGER.error(
                "FAIL: position opened but no matching new conditional stop order found on OKX. "
                "Do NOT go live until this is fixed (check ccxt version / param format)."
            )
    finally:
        if opened:
            LOGGER.info("Cleaning up: closing only the test position.")
            try:
                trader.close_long(contracts)
            except Exception:
                LOGGER.exception(
                    "Cleanup failed — the matched stop remains active; close the demo position manually."
                )
            else:
                if matched:
                    try:
                        trader.exchange.private_post_trade_cancel_algos(
                            [{"algoId": matched["algoId"], "instId": inst_id}]
                        )
                    except Exception:
                        LOGGER.warning(
                            "Test position closed, but cancelling its stop failed or it was already cancelled.",
                            exc_info=True,
                        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
