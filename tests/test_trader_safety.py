import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from core.position_registry import PositionRegistry
from core.strategy import MovingAverageSignal
from futures.trader import FuturesTrader
from spot.trader import SpotTrader


def _signal(value: str) -> MovingAverageSignal:
    return MovingAverageSignal(
        signal=value,  # type: ignore[arg-type]
        previous_fast=1.0,
        previous_slow=1.0,
        current_fast=1.0,
        current_slow=1.0,
    )


def _spot_trader(signal: str) -> SpotTrader:
    trader = object.__new__(SpotTrader)
    trader.symbol = "BTC/USDT"
    trader.settings = SimpleNamespace(
        spot_stop_loss_pct=0.02,
        stop_out_cooldown_seconds=900,
        max_open_positions=3,
        sandbox_mode=True,
        dry_run=False,
    )
    trader.positions = PositionRegistry(Path(tempfile.mkdtemp()) / "positions.json")
    trader.fetch_signal = Mock(return_value=_signal(signal))
    trader.risk = Mock()
    trader.risk.assert_can_trade = Mock()
    trader._record_trade = Mock()
    trader.exchange = Mock()
    trader.exchange.amount_to_precision.side_effect = lambda symbol, amount: str(amount)
    trader.exchange.market.return_value = {"base": "BTC", "limits": {"amount": {"min": 0.0001}}}
    return trader


class SpotExitSafetyTest(unittest.TestCase):
    def test_stop_loss_runs_before_a_buy_signal(self) -> None:
        trader = _spot_trader("buy")
        stop_order = {"id": "stop"}
        trader._check_stop_loss = Mock(return_value=stop_order)

        self.assertEqual(trader.run_once(), stop_order)
        trader.risk.assert_can_trade.assert_not_called()

    def test_signal_sell_never_sells_unregistered_manual_holdings(self) -> None:
        trader = _spot_trader("sell")
        trader._check_stop_loss = Mock(return_value=None)
        trader.fetch_base_free_balance = Mock(return_value=10.0)
        trader.create_market_order = Mock()

        self.assertIsNone(trader.run_once())
        trader.create_market_order.assert_not_called()
        trader.risk.assert_can_trade.assert_not_called()

    def test_signal_sell_closes_registered_amount_without_entry_gate(self) -> None:
        trader = _spot_trader("sell")
        trader.positions.record_open("spot", trader.symbol, "long", 2.0, 100.0)
        trader._check_stop_loss = Mock(return_value=None)
        trader.fetch_last_price = Mock(return_value=110.0)
        trader.fetch_base_free_balance = Mock(return_value=5.0)
        trader.create_market_order = Mock(return_value={"id": "exit"})

        self.assertEqual(trader.run_once(), {"id": "exit"})
        trader.create_market_order.assert_called_once_with("sell", 2.0)
        trader.risk.assert_can_trade.assert_not_called()
        self.assertIsNone(trader.positions.get("spot", trader.symbol))

    def test_repeated_buy_signal_does_not_stack_same_symbol(self) -> None:
        trader = _spot_trader("buy")
        trader.positions.record_open("spot", trader.symbol, "long", 1.0, 100.0)
        trader._check_stop_loss = Mock(return_value=None)
        trader.create_market_order = Mock()

        self.assertIsNone(trader.run_once())
        trader.create_market_order.assert_not_called()
        trader.risk.assert_can_trade.assert_not_called()

    def test_stop_with_no_free_balance_keeps_registry_position(self) -> None:
        trader = _spot_trader("hold")
        trader.positions.record_open("spot", trader.symbol, "long", 2.0, 100.0)
        trader.fetch_last_price = Mock(return_value=95.0)
        trader.fetch_base_free_balance = Mock(return_value=0.0)

        with self.assertRaisesRegex(RuntimeError, "no registered amount is free"):
            trader._check_stop_loss(_signal("hold"))
        self.assertIsNotNone(trader.positions.get("spot", trader.symbol))


class FuturesCloseLoggingTest(unittest.TestCase):
    def test_close_by_direction_returns_actual_closed_contracts(self) -> None:
        trader = object.__new__(FuturesTrader)
        trader.fetch_open_positions = Mock(
            return_value=[
                {"side": "short", "contracts": 7.0},
                {"side": "long", "contracts": 3.0},
            ]
        )
        trader._close_position = Mock(return_value={"id": "close"})

        self.assertEqual(trader.close_positions_by_direction("short"), [(7.0, {"id": "close"})])
        trader._close_position.assert_called_once_with("short", 7.0)


if __name__ == "__main__":
    unittest.main()
