import unittest

from core.strategy import calculate_atr_pct, filtered_ma_cross_signal


class StrategyTest(unittest.TestCase):
    def test_atr_pct_uses_latest_close(self) -> None:
        highs = [11, 12, 13, 14]
        lows = [9, 10, 11, 12]
        closes = [10, 11, 12, 13]

        self.assertAlmostEqual(calculate_atr_pct(highs, lows, closes, period=3), 2 / 13)

    def test_low_volatility_filters_cross_signal(self) -> None:
        closes = [
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.10,
        ]
        highs = [close + 0.001 for close in closes]
        lows = [close - 0.001 for close in closes]

        signal = filtered_ma_cross_signal(
            highs,
            lows,
            closes,
            rsi_overbought=101,
            atr_period=14,
            min_atr_pct=0.01,
            max_atr_pct=0.05,
            min_slow_slope_pct=0,
        )

        self.assertEqual(signal.signal, "hold")
        self.assertEqual(signal.reason, "low_volatility")

    def test_weak_slow_ma_slope_filters_cross_signal(self) -> None:
        closes = [
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.00,
            10.10,
        ]
        highs = [close + 0.50 for close in closes]
        lows = [close - 0.50 for close in closes]

        signal = filtered_ma_cross_signal(
            highs,
            lows,
            closes,
            rsi_overbought=101,
            atr_period=14,
            min_atr_pct=0.001,
            max_atr_pct=0.20,
            min_slow_slope_pct=0.01,
        )

        self.assertEqual(signal.signal, "hold")
        self.assertEqual(signal.reason, "weak_uptrend")


if __name__ == "__main__":
    unittest.main()
