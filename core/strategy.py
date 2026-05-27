from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Signal = Literal["buy", "sell", "hold"]


@dataclass(frozen=True)
class MovingAverageSignal:
    signal: Signal
    previous_fast: float
    previous_slow: float
    current_fast: float
    current_slow: float
    rsi: float | None = None


def simple_moving_average(values: list[float]) -> float:
    if not values:
        raise ValueError("Cannot calculate a moving average from an empty list.")
    return sum(values) / len(values)


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """Calculate the Relative Strength Index (RSI) for the given closes.

    Uses Wilder's smoothed average (same as TradingView default).
    Requires at least period + 1 data points.
    """
    if len(closes) < period + 1:
        raise ValueError(f"Need at least {period + 1} closes to calculate RSI({period}).")

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Seed with simple average over the first `period` deltas
    gains = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for remaining deltas
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def moving_average_cross_signal(closes: list[float], *, fast: int = 5, slow: int = 20) -> MovingAverageSignal:
    if fast >= slow:
        raise ValueError("fast MA period must be lower than slow MA period.")
    if len(closes) < slow + 1:
        raise ValueError(f"Need at least {slow + 1} closes to calculate crossover.")

    previous_fast = simple_moving_average(closes[-fast - 1 : -1])
    previous_slow = simple_moving_average(closes[-slow - 1 : -1])
    current_fast = simple_moving_average(closes[-fast:])
    current_slow = simple_moving_average(closes[-slow:])

    if previous_fast <= previous_slow and current_fast > current_slow:
        signal: Signal = "buy"
    elif previous_fast >= previous_slow and current_fast < current_slow:
        signal = "sell"
    else:
        signal = "hold"

    return MovingAverageSignal(
        signal=signal,
        previous_fast=previous_fast,
        previous_slow=previous_slow,
        current_fast=current_fast,
        current_slow=current_slow,
    )


def moving_average_cross_signal_with_rsi(
    closes: list[float],
    *,
    fast: int = 5,
    slow: int = 20,
    rsi_period: int = 14,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
) -> MovingAverageSignal:
    """MA crossover signal filtered by RSI.

    - Golden cross buy is suppressed when RSI >= rsi_overbought (overbought zone).
    - Death cross sell is suppressed when RSI <= rsi_oversold (oversold zone).

    This mirrors the approach used by many professional systematic traders:
    use MA to find direction, use RSI to confirm timing.
    """
    base = moving_average_cross_signal(closes, fast=fast, slow=slow)

    try:
        rsi = calculate_rsi(closes, period=rsi_period)
    except ValueError:
        # Not enough data for RSI — fall back to raw MA signal
        return base

    signal: Signal = base.signal
    if base.signal == "buy" and rsi >= rsi_overbought:
        signal = "hold"
    elif base.signal == "sell" and rsi <= rsi_oversold:
        signal = "hold"

    return MovingAverageSignal(
        signal=signal,
        previous_fast=base.previous_fast,
        previous_slow=base.previous_slow,
        current_fast=base.current_fast,
        current_slow=base.current_slow,
        rsi=rsi,
    )
