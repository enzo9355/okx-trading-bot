import os
import unittest
from unittest.mock import patch

from core.config import Settings, _bool_env


class BooleanConfigSafetyTest(unittest.TestCase):
    def test_explicit_false_is_accepted(self) -> None:
        with patch.dict(os.environ, {"SANDBOX_MODE": "false"}):
            self.assertFalse(_bool_env("SANDBOX_MODE", True))

    def test_typo_cannot_silently_disable_sandbox(self) -> None:
        with patch.dict(os.environ, {"SANDBOX_MODE": "treu"}):
            with self.assertRaisesRegex(ValueError, "explicit boolean"):
                _bool_env("SANDBOX_MODE", True)


class ProtectivePercentageValidationTest(unittest.TestCase):
    def test_stop_loss_at_or_above_one_is_rejected(self) -> None:
        with patch.dict(os.environ, {"SPOT_STOP_LOSS_PCT": "1.0"}):
            with self.assertRaisesRegex(ValueError, "SPOT_STOP_LOSS_PCT"):
                Settings.from_env()

    def test_short_take_profit_cannot_create_nonpositive_price(self) -> None:
        with patch.dict(os.environ, {"FUTURES_TAKE_PROFIT_PCT": "1.0"}):
            with self.assertRaisesRegex(ValueError, "FUTURES_TAKE_PROFIT_PCT"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
