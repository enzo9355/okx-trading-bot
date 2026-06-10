import unittest
from unittest.mock import patch

import ccxt

from futures.trader import _is_rate_limit_error, _retry_on_rate_limit


class IsRateLimitErrorTest(unittest.TestCase):
    def test_ccxt_rate_limit_exceeded_is_detected(self) -> None:
        self.assertTrue(_is_rate_limit_error(ccxt.RateLimitExceeded("nope")))

    def test_okx_50011_string_is_detected(self) -> None:
        self.assertTrue(
            _is_rate_limit_error(Exception('okx {"msg":"Too Many Requests","code":"50011"}'))
        )

    def test_unrelated_error_is_not_a_rate_limit(self) -> None:
        self.assertFalse(_is_rate_limit_error(ValueError("bad value")))


class RetryOnRateLimitTest(unittest.TestCase):
    def test_first_call_succeeds_immediately(self) -> None:
        calls = []

        def op() -> str:
            calls.append(1)
            return "ok"

        self.assertEqual(_retry_on_rate_limit("test", op), "ok")
        self.assertEqual(len(calls), 1)

    def test_rate_limit_then_success_retries(self) -> None:
        calls = []

        def op() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise ccxt.RateLimitExceeded("50011")
            return "ok"

        with patch("futures.trader.time.sleep") as sleep_mock:
            result = _retry_on_rate_limit("test", op)

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        # Two failures => two backoff sleeps (the third call succeeds).
        self.assertEqual(sleep_mock.call_count, 2)

    def test_non_rate_limit_error_does_not_retry(self) -> None:
        calls = []

        def op() -> None:
            calls.append(1)
            raise ValueError("unrelated")

        with self.assertRaises(ValueError):
            _retry_on_rate_limit("test", op)
        # Must be exactly 1 — we must NOT retry non-rate-limit errors.
        self.assertEqual(len(calls), 1)

    def test_persistent_rate_limit_eventually_raises(self) -> None:
        calls = []

        def op() -> None:
            calls.append(1)
            raise ccxt.RateLimitExceeded("50011")

        with patch("futures.trader.time.sleep"):
            with self.assertRaises(ccxt.RateLimitExceeded):
                _retry_on_rate_limit("test", op)
        # 5 total attempts before giving up.
        self.assertEqual(len(calls), 5)


if __name__ == "__main__":
    unittest.main()
