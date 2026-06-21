import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import verify_sl_attachment


class _Settings:
    sandbox_mode = True
    futures_symbol = "BTC/USDT:USDT"

    def with_overrides(self, **_kwargs):
        return self

    def require_credentials(self):
        return None


class _Exchange:
    def __init__(self, pending):
        self.pending = iter(pending)
        self.cancelled = []

    def market(self, _symbol):
        return {
            "id": "BTC-USDT-SWAP",
            "limits": {"amount": {"min": 0.01}},
        }

    def private_get_trade_orders_algo_pending(self, _params):
        return {"data": next(self.pending)}

    def private_post_trade_cancel_algos(self, params):
        self.cancelled.append(params)


class _Trader:
    def __init__(self, pending, positions=None):
        self.exchange = _Exchange(pending)
        self.risk = SimpleNamespace(max_order_notional=lambda: 10.0)
        self.symbol = "BTC/USDT:USDT"
        self.positions = positions or []
        self.opened = []
        self.closed = []
        self.closed_all = False

    def fetch_open_positions(self):
        return self.positions

    def fetch_last_price(self):
        return 100.0

    def contracts_for_notional(self, _notional, _price):
        return 2.0

    def _stop_loss_take_profit(self, _direction, _price):
        return 99.0, 0.0

    def open_long(self, contracts, price):
        self.opened.append((contracts, price))
        return {"id": "entry-1"}

    def close_long(self, contracts):
        self.closed.append(contracts)
        return {"id": "close-1"}

    def close_all_positions(self, **_kwargs):
        self.closed_all = True


def _run(trader):
    with (
        patch.object(verify_sl_attachment.Settings, "from_env", return_value=_Settings()),
        patch.object(verify_sl_attachment, "FuturesTrader", return_value=trader),
    ):
        return verify_sl_attachment.main()


class StopLossVerificationSafetyTest(unittest.TestCase):
    def test_refuses_to_open_when_symbol_already_has_a_position(self):
        trader = _Trader([[]], positions=[{"side": "long", "contracts": 1.0}])

        self.assertEqual(_run(trader), 1)
        self.assertEqual(trader.opened, [])
        self.assertEqual(trader.closed, [])

    def test_matches_only_the_new_exact_stop_and_cleans_up_its_contracts(self):
        wrong_stop = {
            "algoId": "old",
            "instId": "BTC-USDT-SWAP",
            "side": "sell",
            "slTriggerPx": "98",
            "cTime": "1",
        }
        exact_stop = {
            "algoId": "new",
            "instId": "BTC-USDT-SWAP",
            "side": "sell",
            "slTriggerPx": "99.0",
            "cTime": "9999999999999",
        }
        trader = _Trader([[], [wrong_stop], [exact_stop]])

        self.assertEqual(_run(trader), 0)
        self.assertEqual(trader.opened, [(0.01, 100.0)])
        self.assertEqual(trader.closed, [0.01])
        self.assertFalse(trader.closed_all)
        self.assertEqual(
            trader.exchange.cancelled,
            [[{"algoId": "new", "instId": "BTC-USDT-SWAP"}]],
        )


if __name__ == "__main__":
    unittest.main()
