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


def simple_moving_average(values: list[float]) -> float:
    if not values:
        raise ValueError("Cannot calculate a moving average from an empty list.")
    return sum(values) / len(values)


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

