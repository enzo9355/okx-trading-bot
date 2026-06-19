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


class OkxRiskMetricTest(unittest.TestCase):
    def test_total_equity_is_preferred_over_quote_cash(self) -> None:
        balance = {
            "total": {"USDT": 850.0},
            "free": {"USDT": 840.0},
            "info": {"data": [{"totalEq": "1000.0", "adjEq": "990.0"}]},
        }
        self.assertEqual(RiskManager.extract_equity(balance, "USDT"), 1000.0)

    def test_worst_open_position_margin_ratio_is_used(self) -> None:
        positions = [
            {"contracts": 1.0, "marginRatio": 0.08, "info": {}},
            {"contracts": 2.0, "marginRatio": 0.21, "info": {}},
            {"contracts": 0.0, "marginRatio": 0.90, "info": {}},
        ]
        self.assertEqual(RiskManager.extract_position_margin_ratio(positions), 0.21)

    def test_raw_okx_position_ratio_is_inverted_as_fallback(self) -> None:
        positions = [{"contracts": 1.0, "marginRatio": None, "info": {"mgnRatio": "5"}}]
        self.assertAlmostEqual(RiskManager.extract_position_margin_ratio(positions) or 0, 0.2)

    def test_margin_guard_trips_when_normalized_ratio_rises_to_threshold(self) -> None:
        settings = replace(_settings_with_tmp_state(), margin_ratio_threshold=0.20)

        class Exchange:
            def fetch_positions(self, symbols: list[str]) -> list[dict]:
                self.symbols = symbols
                return [{"contracts": 1.0, "marginRatio": 0.25, "info": {}}]

        risk = RiskManager(settings, Exchange())
        self.assertEqual(risk.margin_ratio_breached("BTC/USDT:USDT"), (True, 0.25))


class RiskStatePersistenceTest(unittest.TestCase):
    def test_corrupt_existing_state_fails_closed(self) -> None:
        settings = _settings_with_tmp_state()
        settings.state_file.write_text('{"date":', encoding="utf-8")
        with self.assertRaises(RiskLimitError):
            RiskManager(settings, exchange=None)

    def test_state_save_is_atomic(self) -> None:
        settings = _settings_with_tmp_state()
        risk = RiskManager(settings, exchange=None)
        risk.state = {"date": "2026-06-20", "stopped": True}
        risk._save_state()
        self.assertTrue(settings.state_file.exists())
        self.assertFalse(settings.state_file.with_name(f".{settings.state_file.name}.tmp").exists())


if __name__ == "__main__":
    unittest.main()
