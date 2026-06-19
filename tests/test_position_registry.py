import tempfile
import unittest
from pathlib import Path

from core.position_registry import PositionRegistry
from core.risk import RiskLimitError


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
        path.write_text("{partial", encoding="utf-8")
        with self.assertRaisesRegex(RiskLimitError, "refusing to trade"):
            PositionRegistry(path).open_count()

    def test_dry_run_uses_a_separate_registry_file(self) -> None:
        path = Path(tempfile.mkdtemp()) / "positions.json"
        live = PositionRegistry.for_mode(path, dry_run=False)
        simulated = PositionRegistry.for_mode(path, dry_run=True)
        simulated.record_open("spot", "BTC/USDT", "long", 1.0, 100.0)
        self.assertNotEqual(live.path, simulated.path)
        self.assertEqual(live.open_count(), 0)
        self.assertEqual(simulated.open_count(), 1)

    def test_entry_slot_enforces_the_global_cap(self) -> None:
        reg = _registry()
        reg.record_open("spot", "BTC/USDT", "long", 1.0, 100.0)
        with reg.entry_slot("spot", "ETH/USDT", max_open_positions=1) as allowed:
            self.assertFalse(allowed)
        with reg.entry_slot("spot", "BTC/USDT", max_open_positions=1) as allowed:
            self.assertTrue(allowed)


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
