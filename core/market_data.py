from __future__ import annotations

from typing import Protocol


Ohlcv = list[float]


class OhlcvExchange(Protocol):
    def fetch_ohlcv(self, symbol: str, *, timeframe: str, limit: int) -> list[Ohlcv]: ...

    def fetch_time(self) -> int | None: ...

    def parse_timeframe(self, timeframe: str) -> int: ...


def fetch_closed_ohlcv(
    exchange: OhlcvExchange,
    symbol: str,
    timeframe: str,
    *,
    limit: int,
) -> list[Ohlcv]:
    """Fetch exactly ``limit`` completed candles using exchange server time."""
    if limit <= 0:
        raise ValueError("OHLCV limit must be greater than zero.")

    timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1_000)
    if timeframe_ms <= 0:
        raise ValueError(f"Invalid timeframe duration for {timeframe!r}.")

    candles = list(
        exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=limit + 1,
        )
    )
    server_time_ms = exchange.fetch_time()
    if server_time_ms is None:
        raise ValueError("Exchange server time is unavailable.")

    if candles:
        last_open_ms = int(candles[-1][0])
        if last_open_ms + timeframe_ms > int(server_time_ms):
            candles.pop()

    if len(candles) < limit:
        raise ValueError(
            f"Need {limit} closed candles for {symbol} {timeframe}; got {len(candles)}."
        )

    return candles[-limit:]
