from __future__ import annotations

import argparse
import logging
import threading
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from time import monotonic
from typing import Callable

from core.config import Settings
from core.risk import EntryHalted, OrderRejected, RiskLimitError
from futures.trader import FuturesTrader
from spot.trader import SpotTrader


LOGGER = logging.getLogger(__name__)
TraderFactory = Callable[[Settings], object]


@dataclass(frozen=True)
class WorkerJob:
    kind: str
    symbol: str
    factory: TraderFactory


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
    kind: str,
    symbol: str,
    trader_factory: TraderFactory,
    settings: Settings,
    *,
    once: bool,
    stop_event: threading.Event,
) -> None:
    try:
        trader = trader_factory(settings)
        LOGGER.info(
            "%s trader started. symbol=%s sandbox=%s dry_run=%s interval=%ss",
            kind,
            symbol,
            settings.sandbox_mode,
            settings.dry_run,
            settings.trade_interval_seconds,
        )

        while not stop_event.is_set():
            started = monotonic()
            try:
                trader.run_once()
            except OrderRejected as exc:
                # A single order could not be placed (rounding, min size, transient
                # API hiccup). Skip just this order and keep the worker running.
                LOGGER.info("%s order skipped: symbol=%s reason=%s", kind, symbol, exc)
            except EntryHalted as exc:
                # Keep workers alive so existing spot stops, futures margin
                # protection, and exchange-side stop reconciliation still run.
                LOGGER.warning(
                    "%s entry blocked by account risk control: symbol=%s reason=%s",
                    kind,
                    symbol,
                    exc,
                )
            except RiskLimitError as exc:
                # Persistent risk-state or registry failures are fail-closed: the
                # bot cannot prove its limits are intact, so stop every worker.
                LOGGER.error("%s trader stopped by risk control: symbol=%s error=%s", kind, symbol, exc)
                stop_event.set()
                return
            except Exception:
                LOGGER.exception("%s trader cycle failed: symbol=%s", kind, symbol)

            if once:
                return

            elapsed = monotonic() - started
            wait_seconds = max(settings.trade_interval_seconds - elapsed, 0)
            stop_event.wait(wait_seconds)
    except Exception as exc:
        LOGGER.exception("%s worker crashed: %s", kind, exc)
        stop_event.set()
        raise


def submit_worker(
    executor: ThreadPoolExecutor,
    job: WorkerJob,
    settings: Settings,
    *,
    once: bool,
    stop_event: threading.Event,
) -> Future[None]:
    LOGGER.info("starting %s worker: symbol=%s", job.kind, job.symbol)
    return executor.submit(
        run_loop,
        job.kind,
        job.symbol,
        job.factory,
        settings,
        once=once,
        stop_event=stop_event,
    )


def build_jobs(mode: str, settings: Settings) -> list[WorkerJob]:
    jobs: list[WorkerJob] = []
    if mode in {"spot", "both"}:
        for symbol in settings.spot_symbols:
            jobs.append(
                WorkerJob(
                    kind="spot",
                    symbol=symbol,
                    factory=lambda settings, symbol=symbol: SpotTrader(settings, symbol=symbol),
                )
            )
    if mode in {"futures", "both"}:
        for symbol in settings.futures_symbols:
            jobs.append(
                WorkerJob(
                    kind="futures",
                    symbol=symbol,
                    factory=lambda settings, symbol=symbol: FuturesTrader(settings, symbol=symbol),
                )
            )
    return jobs


def main() -> None:
    configure_logging()
    args = parse_args()
    settings = Settings.from_env().with_overrides(
        dry_run=True if args.dry_run else None,
        trade_interval_seconds=args.interval,
    )
    settings.require_credentials()

    stop_event = threading.Event()
    jobs = build_jobs(args.mode, settings)
    LOGGER.info(
        "configured workers: spot=%s futures=%s",
        list(settings.spot_symbols) if args.mode in {"spot", "both"} else [],
        list(settings.futures_symbols) if args.mode in {"futures", "both"} else [],
    )

    try:
        if len(jobs) == 1:
            job = jobs[0]
            LOGGER.info("starting %s worker: symbol=%s", job.kind, job.symbol)
            run_loop(job.kind, job.symbol, job.factory, settings, once=args.once, stop_event=stop_event)
            return

        if args.mode == "both":
            LOGGER.info("both mode initialized")
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = {
                submit_worker(
                    executor,
                    job,
                    settings,
                    once=args.once,
                    stop_event=stop_event,
                ): job
                for job in jobs
            }
            for future in as_completed(futures):
                job = futures[future]
                try:
                    future.result()
                except Exception:
                    stop_event.set()
                    raise SystemExit(1)
                if not args.once:
                    LOGGER.warning("%s worker exited unexpectedly: symbol=%s", job.kind, job.symbol)
                    stop_event.set()
                    break
    except KeyboardInterrupt:
        LOGGER.info("Shutdown requested by user.")
        stop_event.set()


if __name__ == "__main__":
    main()

