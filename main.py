from __future__ import annotations

import argparse
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import monotonic
from typing import Callable

from core.config import Settings
from core.risk import RiskLimitError
from futures.trader import FuturesTrader
from spot.trader import SpotTrader


LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OKX auto trading bot")
    parser.add_argument("--mode", choices=["spot", "futures", "both"], required=True)
    parser.add_argument("--once", action="store_true", help="Run only one trading cycle.")
    parser.add_argument("--interval", type=int, default=None, help="Seconds between trading cycles.")
    parser.add_argument("--dry-run", action="store_true", help="Log intended orders without placing them.")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def run_loop(
    name: str,
    trader_factory: Callable[[Settings], object],
    settings: Settings,
    *,
    once: bool,
    stop_event: threading.Event,
) -> None:
    trader = trader_factory(settings)
    LOGGER.info(
        "%s trader started. sandbox=%s dry_run=%s interval=%ss",
        name,
        settings.sandbox_mode,
        settings.dry_run,
        settings.trade_interval_seconds,
    )

    while not stop_event.is_set():
        started = monotonic()
        try:
            trader.run_once()
        except RiskLimitError as exc:
            LOGGER.error("%s trader stopped by risk control: %s", name, exc)
            stop_event.set()
            return
        except Exception:
            LOGGER.exception("%s trader cycle failed.", name)

        if once:
            return

        elapsed = monotonic() - started
        wait_seconds = max(settings.trade_interval_seconds - elapsed, 0)
        stop_event.wait(wait_seconds)


def main() -> None:
    configure_logging()
    args = parse_args()
    settings = Settings.from_env().with_overrides(
        dry_run=True if args.dry_run else None,
        trade_interval_seconds=args.interval,
    )
    settings.require_credentials()

    stop_event = threading.Event()
    jobs: list[tuple[str, Callable[[Settings], object]]] = []
    if args.mode in {"spot", "both"}:
        jobs.append(("spot", SpotTrader))
    if args.mode in {"futures", "both"}:
        jobs.append(("futures", FuturesTrader))

    try:
        if len(jobs) == 1:
            name, factory = jobs[0]
            run_loop(name, factory, settings, once=args.once, stop_event=stop_event)
            return

        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = [
                executor.submit(
                    run_loop,
                    name,
                    factory,
                    settings,
                    once=args.once,
                    stop_event=stop_event,
                )
                for name, factory in jobs
            ]
            for future in as_completed(futures):
                future.result()
                if not args.once:
                    stop_event.set()
                    break
    except KeyboardInterrupt:
        LOGGER.info("Shutdown requested by user.")
        stop_event.set()


if __name__ == "__main__":
    main()

