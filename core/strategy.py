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
    atr_pct: float | None = None
    slow_slope_pct: float | None = None
    reason: str = "ma_cross"


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

    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_atr_pct(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Return Average True Range as a percentage of the latest close."""
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise ValueError("High, low, and close lists must have the same length.")
    if len(closes) < period + 1:
        raise ValueError(f"Need at least {period + 1} candles to calculate ATR({period}).")

    true_ranges = []
    for index in range(1, len(closes)):
        high = highs[index]
        low = lows[index]
        previous_close = closes[index - 1]
        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    atr = sum(true_ranges[-period:]) / period
    latest_close = closes[-1]
    if latest_close <= 0:
        raise ValueError("Latest close must be greater than 0 to calculate ATR percentage.")
    return atr / latest_close


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
        # Not enough data for RSI; fall back to raw MA signal.
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
        reason="rsi_filter" if signal == "hold" and base.signal != "hold" else base.reason,
    )


def filtered_ma_cross_signal(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    fast: int = 5,
    slow: int = 20,
    rsi_period: int = 14,
    rsi_overbought: float = 70.0,
    rsi_oversold: float = 30.0,
    atr_period: int = 14,
    min_atr_pct: float = 0.001,
    max_atr_pct: float = 0.05,
    min_slow_slope_pct: float = 0.0001,
) -> MovingAverageSignal:
    """MA crossover with RSI, volatility, and trend-quality filters."""
    signal = moving_average_cross_signal_with_rsi(
        closes,
        fast=fast,
        slow=slow,
        rsi_period=rsi_period,
        rsi_overbought=rsi_overbought,
        rsi_oversold=rsi_oversold,
    )

    try:
        atr_pct = calculate_atr_pct(highs, lows, closes, period=atr_period)
    except ValueError:
        atr_pct = None

    slow_slope_pct = None
    if signal.previous_slow > 0:
        slow_slope_pct = (signal.current_slow - signal.previous_slow) / signal.previous_slow

    filtered_signal: Signal = signal.signal
    reason = signal.reason
    if filtered_signal != "hold" and atr_pct is not None:
        if atr_pct < min_atr_pct:
            filtered_signal = "hold"
            reason = "low_volatility"
        elif atr_pct > max_atr_pct:
            filtered_signal = "hold"
            reason = "high_volatility"

    if filtered_signal == "buy" and slow_slope_pct is not None and slow_slope_pct < min_slow_slope_pct:
        filtered_signal = "hold"
        reason = "weak_uptrend"
    elif filtered_signal == "sell" and slow_slope_pct is not None and slow_slope_pct > -min_slow_slope_pct:
        filtered_signal = "hold"
        reason = "weak_downtrend"

    return MovingAverageSignal(
        signal=filtered_signal,
        previous_fast=signal.previous_fast,
        previous_slow=signal.previous_slow,
        current_fast=signal.current_fast,
        current_slow=signal.current_slow,
        rsi=signal.rsi,
        atr_pct=atr_pct,
        slow_slope_pct=slow_slope_pct,
        reason=reason,
    )
