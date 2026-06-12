"""Run a backtest against OKX historical klines.

Usage (on a machine with OKX network access, e.g. the GCP VM):

    .venv/bin/python -m backtest.run --symbol BTC/USDT --timeframe 15m --days 30
    .venv/bin/python -m backtest.run --symbol BTC/USDT --timeframe 1m --days 7 --stop-loss 0.02

Uses the PUBLIC kline endpoint — no API keys required. Compares the strategy
against buy & hold over the same window, which is the number that actually
matters: a strategy that underperforms holding is paying fees for nothing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import ccxt

from backtest.engine import DEFAULT_FEE_RATE, run_backtest
from core.config import Settings

LOGGER = logging.getLogger(__name__)

_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def fetch_klines(symbol: str, timeframe: str, days: int) -> tuple[list[float], list[float], list[float]]:
    """Paginate OKX public klines back `days` from now. ~1.4k bars/day at 1m."""
    if timeframe not in _TIMEFRAME_MS:
        raise SystemExit(f"Unsupported timeframe {timeframe}. Use one of {sorted(_TIMEFRAME_MS)}.")
    exchange = ccxt.okx({"enableRateLimit": True})
    span_ms = days * 86_400_000
    since = exchange.milliseconds() - span_ms
    step_ms = _TIMEFRAME_MS[timeframe]

    candles: list[list[float]] = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=100)
        if not batch:
            break
        candles.extend(batch)
        since = int(batch[-1][0]) + step_ms
        if since >= exchange.milliseconds():
            break
        time.sleep(exchange.rateLimit / 1000)

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    return highs, lows, closes


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the live strategy on OKX klines.")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--fee", type=float, default=DEFAULT_FEE_RATE, help="taker fee per side")
    parser.add_argument("--stop-loss", type=float, default=0.0, help="e.g. 0.02 for 2%% intrabar stop")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = Settings.from_env()
    LOGGER.info("Fetching %s %s klines for %d days...", args.symbol, args.timeframe, args.days)
    highs, lows, closes = fetch_klines(args.symbol, args.timeframe, args.days)
    LOGGER.info("Fetched %d candles. Running backtest...", len(closes))

    result = run_backtest(
        highs,
        lows,
        closes,
        window=settings.ohlcv_limit,
        fee_rate=args.fee,
        stop_loss_pct=args.stop_loss,
        fast=5,
        slow=20,
        rsi_period=settings.rsi_period,
        rsi_overbought=settings.rsi_overbought,
        rsi_oversold=settings.rsi_oversold,
        atr_period=settings.atr_period,
        min_atr_pct=settings.atr_min_pct,
        max_atr_pct=settings.atr_max_pct,
        min_slow_slope_pct=settings.ma_min_trend_slope_pct,
    )

    print(f"\n=== {args.symbol} {args.timeframe} last {args.days}d (fee {args.fee:.3%}/side) ===")
    print(result.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
