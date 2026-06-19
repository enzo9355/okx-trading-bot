import unittest

from backtest.engine import run_backtest

# Relaxed filter params so tests exercise ENGINE mechanics deterministically,
# not the filters (the filters have their own tests in test_strategy.py).
RELAXED = dict(
    fast=5,
    slow=20,
    rsi_period=14,
    rsi_overbought=101.0,  # never suppress
    rsi_oversold=-1.0,  # never suppress
    atr_period=14,
    min_atr_pct=0.0,
    max_atr_pct=10.0,
    min_slow_slope_pct=0.0,
)


def _flat_then_jump_then_drop() -> tuple[list[float], list[float], list[float]]:
    """30 flat bars, a jump (golden cross → buy), plateau, then a drop
    (death cross → sell). Produces exactly one full round trip."""
    closes = [100.0] * 30 + [103.0] * 10 + [97.0] * 10
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return highs, lows, closes


class EngineMechanicsTest(unittest.TestCase):
    def test_flat_market_produces_no_trades(self) -> None:
        closes = [100.0] * 80
        result = run_backtest(
            [c + 0.5 for c in closes], [c - 0.5 for c in closes], closes, window=30, **RELAXED
        )
        self.assertEqual(result.n_trades, 0)
        self.assertAlmostEqual(result.total_return_pct, 0.0)

    def test_round_trip_pnl_and_fees(self) -> None:
        highs, lows, closes = _flat_then_jump_then_drop()
        result = run_backtest(highs, lows, closes, window=25, fee_rate=0.001, **RELAXED)
        self.assertEqual(result.n_trades, 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, "signal")
        # entry 103, exit 97 → gross ≈ -5.83%, net after 2×0.1% fee ≈ -6.01%
        self.assertAlmostEqual(trade.gross_return_pct, (97 - 103) / 103, places=6)
        expected_net = (97 / 103) * (0.999**2) - 1
        self.assertAlmostEqual(result.total_return_pct, expected_net, places=6)
        expected_fees = 0.001 + 0.999 * (97 / 103) * 0.001
        self.assertAlmostEqual(result.total_fees_pct, expected_fees, places=9)

    def test_invalid_series_lengths_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "same length"):
            run_backtest([101.0] * 30, [99.0] * 29, [100.0] * 30, window=25, **RELAXED)

    def test_stop_loss_exits_at_stop_price(self) -> None:
        # Enter at 103, then crash bars whose lows pierce the 2% stop (100.94).
        closes = [100.0] * 30 + [103.0] * 8 + [95.0] * 5
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        result = run_backtest(highs, lows, closes, window=25, stop_loss_pct=0.02, **RELAXED)
        self.assertEqual(result.n_trades, 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, "stop_loss")
        self.assertAlmostEqual(trade.exit_price, trade.entry_price * 0.98, places=6)
        # Loss is bounded near the stop, not the full crash to 95.
        self.assertGreater(trade.gross_return_pct, (95 - 103) / 103)

    def test_open_position_closed_at_end_of_data(self) -> None:
        closes = [100.0] * 30 + [103.0] * 10  # buy fires, never a sell
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        result = run_backtest(highs, lows, closes, window=25, **RELAXED)
        self.assertEqual(result.n_trades, 1)
        self.assertEqual(result.trades[0].exit_reason, "end_of_data")

    def test_buy_hold_benchmark(self) -> None:
        highs, lows, closes = _flat_then_jump_then_drop()
        result = run_backtest(highs, lows, closes, window=25, **RELAXED)
        self.assertAlmostEqual(result.buy_hold_return_pct, (97 - 100) / 100, places=6)


if __name__ == "__main__":
    unittest.main()
