import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from core.config import Settings
from core.risk import OrderRejected, RiskLimitError, RiskManager


def _settings_with_tmp_state() -> Settings:
    settings = Settings.from_env()
    tmp = Path(tempfile.mkdtemp()) / "risk_state.json"
    return replace(settings, state_file=tmp, max_position_pct=0.05)


class RiskNotionalTest(unittest.TestCase):
    def setUp(self) -> None:
        # exchange is unused for assert_order_notional when equity is passed in.
        self.risk = RiskManager(_settings_with_tmp_state(), exchange=None)

    def test_notional_exactly_at_limit_is_allowed(self) -> None:
        # equity 1000 * 5% = 50.0 max. Exactly at the limit must not raise.
        self.risk.assert_order_notional(50.0, equity=1000.0)

    def test_rounding_dust_above_limit_is_allowed(self) -> None:
        # A hair over the limit (precision rounding) must not raise — this is the
        # exact situation that previously crashed the whole bot.
        self.risk.assert_order_notional(50.0000001, equity=1000.0)

    def test_clearly_over_limit_is_rejected(self) -> None:
        with self.assertRaises(OrderRejected):
            self.risk.assert_order_notional(60.0, equity=1000.0)

    def test_hard_usdt_cap_rejects_independently_of_equity(self) -> None:
        settings = replace(_settings_with_tmp_state(), max_order_notional_usdt=30.0)
        risk = RiskManager(settings, exchange=None)
        # 5% of 1000 = 50 allowed by %, but the 30 USDT hard cap rejects 40.
        with self.assertRaises(OrderRejected):
            risk.assert_order_notional(40.0, equity=1000.0)
        risk.assert_order_notional(25.0, equity=1000.0)  # under both caps: OK


class ExceptionSeparationTest(unittest.TestCase):
    def test_order_rejected_is_not_a_risk_limit_error(self) -> None:
        # main.py catches OrderRejected (skip one order) separately from
        # RiskLimitError (halt everything). If OrderRejected were a subclass of
        # RiskLimitError, the halt branch would swallow it and stop the bot.
        self.assertFalse(issubclass(OrderRejected, RiskLimitError))
        self.assertTrue(issubclass(OrderRejected, RuntimeError))
        self.assertTrue(issubclass(RiskLimitError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
