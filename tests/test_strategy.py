import unittest

from core.strategy import calculate_atr_pct, calculate_rsi, filtered_ma_cross_signal


class StrategyTest(unittest.TestCase):
    def test_flat_market_rsi_is_neutral(self) -> None:
        self.assertEqual(calculate_rsi([100, 100, 100, 100], period=3), 50.0)

    def test_gain_only_rsi_is_one_hundred(self) -> None:
        self.assertEqual(calculate_rsi([100, 101, 102, 103], period=3), 100.0)

    def test_loss_only_rsi_is_zero(self) -> None:
        self.assertEqual(calculate_rsi([103, 102, 101, 100], period=3), 0.0)

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

    def test_rsi_filter_suppresses_overbought_buy(self) -> None:
        # 20 flat closes then a jump up => MA5 crosses above MA20 (buy),
        # but the only up-move makes RSI hit 100 => suppressed as rsi_filter.
        closes = [10.00] * 20 + [10.10]
        highs = [close + 0.001 for close in closes]
        lows = [close - 0.001 for close in closes]

        signal = filtered_ma_cross_signal(
            highs,
            lows,
            closes,
            rsi_overbought=70,
            atr_period=14,
            min_atr_pct=0.0,
            max_atr_pct=1.0,
            min_slow_slope_pct=0,
        )

        self.assertEqual(signal.signal, "hold")
        self.assertEqual(signal.reason, "rsi_filter")

    def test_high_volatility_filters_cross_signal(self) -> None:
        # Same buy cross, but very wide candles => large ATR => suppressed.
        closes = [10.00] * 20 + [10.10]
        highs = [close + 5.0 for close in closes]
        lows = [close - 5.0 for close in closes]

        signal = filtered_ma_cross_signal(
            highs,
            lows,
            closes,
            rsi_overbought=101,  # do not let RSI suppress the buy
            atr_period=14,
            min_atr_pct=0.001,
            max_atr_pct=0.05,
            min_slow_slope_pct=0,
        )

        self.assertEqual(signal.signal, "hold")
        self.assertEqual(signal.reason, "high_volatility")

    def test_weak_slow_ma_slope_filters_downtrend(self) -> None:
        # 20 flat closes then a drop => MA5 crosses below MA20 (sell),
        # but the slow MA barely slopes down => suppressed as weak_downtrend.
        closes = [10.00] * 20 + [9.90]
        highs = [close + 0.50 for close in closes]
        lows = [close - 0.50 for close in closes]

        signal = filtered_ma_cross_signal(
            highs,
            lows,
            closes,
            rsi_oversold=-1,  # do not let RSI suppress the sell
            atr_period=14,
            min_atr_pct=0.001,
            max_atr_pct=0.20,
            min_slow_slope_pct=0.01,
        )

        self.assertEqual(signal.signal, "hold")
        self.assertEqual(signal.reason, "weak_downtrend")


if __name__ == "__main__":
    unittest.main()
