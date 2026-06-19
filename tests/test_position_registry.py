import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.position_registry import PositionRegistry, PositionRegistryError


def _registry() -> PositionRegistry:
    return PositionRegistry(Path(tempfile.mkdtemp()) / "positions.json")


class PositionLifecycleTest(unittest.TestCase):
    def test_open_and_get(self) -> None:
        reg = _registry()
        reg.record_open("spot", "BTC/USDT", "long", 0.001, 62000.0)
        pos = reg.get("spot", "BTC/USDT")
        assert pos is not None
        self.assertEqual(pos["side"], "long")
        self.assertAlmostEqual(pos["entry_price"], 62000.0)

    def test_accumulation_uses_weighted_average_entry(self) -> None:
        reg = _registry()
        reg.record_open("spot", "BTC/USDT", "long", 1.0, 100.0)
        reg.record_open("spot", "BTC/USDT", "long", 1.0, 200.0)
        pos = reg.get("spot", "BTC/USDT")
        assert pos is not None
        self.assertAlmostEqual(pos["amount"], 2.0)
        self.assertAlmostEqual(pos["entry_price"], 150.0)

    def test_reduce_then_dust_clears_position(self) -> None:
        reg = _registry()
        reg.record_open("spot", "ETH/USDT", "long", 1.0, 1600.0)
        reg.record_reduce("spot", "ETH/USDT", 0.5)
        pos = reg.get("spot", "ETH/USDT")
        assert pos is not None
        self.assertAlmostEqual(pos["amount"], 0.5)
        reg.record_reduce("spot", "ETH/USDT", 0.4999)
        self.assertIsNone(reg.get("spot", "ETH/USDT"))  # dust → closed

    def test_open_count_spans_spot_and_futures(self) -> None:
        reg = _registry()
        reg.record_open("spot", "BTC/USDT", "long", 1.0, 100.0)
        reg.record_open("futures", "BTC/USDT:USDT", "short", 2.0, 100.0)
        self.assertEqual(reg.open_count(), 2)
        reg.record_close("futures", "BTC/USDT:USDT")
        self.assertEqual(reg.open_count(), 1)

    def test_registry_persists_across_instances(self) -> None:
        path = Path(tempfile.mkdtemp()) / "positions.json"
        PositionRegistry(path).record_open("spot", "BTC/USDT", "long", 1.0, 100.0)
        self.assertTrue(PositionRegistry(path).has_position("spot", "BTC/USDT"))

    def test_corrupt_registry_fails_closed(self) -> None:
        path = Path(tempfile.mkdtemp()) / "positions.json"
        path.write_text('{"positions":', encoding="utf-8")
        with self.assertRaises(PositionRegistryError):
            PositionRegistry(path).open_count()

    def test_atomic_save_leaves_no_temp_file(self) -> None:
        path = Path(tempfile.mkdtemp()) / "positions.json"
        PositionRegistry(path).record_open("spot", "BTC/USDT", "long", 1.0, 100.0)
        self.assertTrue(path.exists())
        self.assertFalse(path.with_name(f".{path.name}.tmp").exists())

    def test_entry_lock_makes_global_cap_check_atomic(self) -> None:
        reg = _registry()

        def try_open(symbol: str) -> None:
            with reg.entry_lock():
                if reg.open_count() >= 1:
                    return
                time.sleep(0.02)
                reg.record_open("spot", symbol, "long", 1.0, 100.0)

        workers = [
            threading.Thread(target=try_open, args=("BTC/USDT",)),
            threading.Thread(target=try_open, args=("ETH/USDT",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(reg.open_count(), 1)


class CooldownTest(unittest.TestCase):
    def test_cooldown_active_then_zero_disabled(self) -> None:
        reg = _registry()
        reg.set_cooldown("spot", "BTC/USDT", 3600)
        self.assertTrue(reg.in_cooldown("spot", "BTC/USDT"))
        self.assertFalse(reg.in_cooldown("spot", "ETH/USDT"))
        reg2 = _registry()
        reg2.set_cooldown("spot", "BTC/USDT", 0)  # disabled
        self.assertFalse(reg2.in_cooldown("spot", "BTC/USDT"))

    def test_expired_cooldown_is_cleared(self) -> None:
        reg = _registry()
        reg.set_cooldown("spot", "BTC/USDT", 1)
        import time

        time.sleep(1.1)
        self.assertFalse(reg.in_cooldown("spot", "BTC/USDT"))


if __name__ == "__main__":
    unittest.main()
