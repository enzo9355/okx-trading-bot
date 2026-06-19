import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.position_registry import PositionRegistry
from core.risk import EntryHalted
from core.strategy import MovingAverageSignal
from futures.trader import ClosedPosition, FuturesTrader
from spot.trader import SpotTrader
from main import run_loop


def _signal(value: str) -> MovingAverageSignal:
    return MovingAverageSignal(
        signal=value,  # type: ignore[arg-type]
        previous_fast=99.0,
        previous_slow=100.0,
        current_fast=101.0,
        current_slow=100.0,
    )


def _spot_trader(signal: str) -> SpotTrader:
    trader = SpotTrader.__new__(SpotTrader)
    trader.symbol = "BTC/USDT"
    trader.settings = SimpleNamespace(
        spot_stop_loss_pct=0.02,
        stop_out_cooldown_seconds=900,
        max_open_positions=3,
        sandbox_mode=True,
        dry_run=False,
    )
    trader.positions = PositionRegistry(Path(tempfile.mkdtemp()) / "positions.json")
    trader.risk = MagicMock()
    trader.risk.assert_can_trade = MagicMock()
    trader.risk.amount_for_price = MagicMock(return_value=1.0)
    trader.risk.assert_order_notional = MagicMock()
    trader.trade_logger = MagicMock()
    trader.fetch_signal = MagicMock(return_value=_signal(signal))
    trader.fetch_last_price = MagicMock(return_value=100.0)
    trader.fetch_base_free_balance = MagicMock(return_value=10.0)
    trader.create_market_order = MagicMock(
        return_value={"id": "order-1", "filled": 1.0, "average": 100.0}
    )
    return trader


class SpotExitSafetyTest(unittest.TestCase):
    def test_sell_signal_never_sells_unregistered_wallet_assets(self) -> None:
        trader = _spot_trader("sell")
        trader.run_once()
        trader.create_market_order.assert_not_called()
        trader.risk.assert_can_trade.assert_not_called()

    def test_sell_signal_is_limited_to_registered_bot_amount(self) -> None:
        trader = _spot_trader("sell")
        trader.positions.record_open("spot", trader.symbol, "long", 1.0, 100.0)
        trader.run_once()
        trader.create_market_order.assert_called_once_with("sell", 1.0)
        trader.risk.assert_can_trade.assert_not_called()

    def test_breached_stop_has_priority_over_buy_signal(self) -> None:
        trader = _spot_trader("buy")
        trader.positions.record_open("spot", trader.symbol, "long", 1.0, 110.0)
        trader.fetch_last_price.return_value = 100.0
        trader.run_once()
        trader.create_market_order.assert_called_once_with("sell", 1.0)
        trader.risk.assert_can_trade.assert_not_called()


class FuturesCloseLoggingTest(unittest.TestCase):
    def test_close_result_keeps_actual_contract_count(self) -> None:
        trader = FuturesTrader.__new__(FuturesTrader)
        trader.fetch_open_positions = MagicMock(
            return_value=[{"side": "short", "contracts": 7.0, "raw": {}}]
        )
        trader._close_position = MagicMock(return_value={"id": "close-1"})
        closed = trader.close_positions_by_direction("short")
        self.assertEqual(
            closed,
            [ClosedPosition(direction="short", contracts=7.0, order={"id": "close-1"})],
        )


class WorkerRiskHandlingTest(unittest.TestCase):
    def test_daily_entry_halt_does_not_stop_protective_workers(self) -> None:
        trader = MagicMock()
        trader.run_once.side_effect = EntryHalted("daily cap")
        settings = SimpleNamespace(
            sandbox_mode=True,
            dry_run=False,
            trade_interval_seconds=60,
        )
        stop_event = threading.Event()
        run_loop(
            "spot",
            "BTC/USDT",
            lambda _settings: trader,
            settings,
            once=True,
            stop_event=stop_event,
        )
        self.assertFalse(stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
