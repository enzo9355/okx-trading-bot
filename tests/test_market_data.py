import unittest

from core.market_data import fetch_closed_ohlcv


class FakeExchange:
    def __init__(self, candles: list[list[float]], now_ms: int) -> None:
        self.candles = candles
        self.now_ms = now_ms
        self.fetch_args: tuple[str, str, int] | None = None

    def fetch_ohlcv(self, symbol: str, *, timeframe: str, limit: int) -> list[list[float]]:
        self.fetch_args = (symbol, timeframe, limit)
        return self.candles[-limit:]

    def fetch_time(self) -> int:
        return self.now_ms

    def parse_timeframe(self, timeframe: str) -> int:
        if timeframe != "1m":
            raise AssertionError(f"unexpected timeframe: {timeframe}")
        return 60


def _candles(count: int) -> list[list[float]]:
    return [
        [index * 60_000, 100.0, 101.0, 99.0, 100.5, 10.0]
        for index in range(count)
    ]


class ClosedOhlcvTest(unittest.TestCase):
    def test_open_last_candle_is_removed(self) -> None:
        candles = _candles(51)
        last_open_ms = int(candles[-1][0])
        exchange = FakeExchange(candles, now_ms=last_open_ms + 59_999)

        result = fetch_closed_ohlcv(exchange, "BTC/USDT", "1m", limit=50)

        self.assertEqual(exchange.fetch_args, ("BTC/USDT", "1m", 51))
        self.assertEqual(result, candles[:-1])

    def test_candle_is_closed_at_exact_end_boundary(self) -> None:
        candles = _candles(51)
        last_open_ms = int(candles[-1][0])
        exchange = FakeExchange(candles, now_ms=last_open_ms + 60_000)

        result = fetch_closed_ohlcv(exchange, "BTC/USDT", "1m", limit=50)

        self.assertEqual(result, candles[-50:])

    def test_insufficient_closed_candles_fails_closed(self) -> None:
        candles = _candles(50)
        last_open_ms = int(candles[-1][0])
        exchange = FakeExchange(candles, now_ms=last_open_ms + 30_000)

        with self.assertRaisesRegex(ValueError, "50 closed candles"):
            fetch_closed_ohlcv(exchange, "BTC/USDT", "1m", limit=50)


if __name__ == "__main__":
    unittest.main()
