import tempfile
import unittest
from pathlib import Path

from forward.schedule import bind_slot, dates_for, monitor_target
from forward.storage import compare_and_swap, read_state


class StorageScheduleTests(unittest.TestCase):
    def test_cas_rejects_changed_base(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "state.json"
            sha = compare_and_swap(path, {"days": []}, None)
            self.assertEqual(compare_and_swap(path, {"days": []}, sha), sha)
            with self.assertRaises(ValueError):
                compare_and_swap(path, {"days": [1]}, None)
            self.assertEqual(read_state(path)[0], {"days": []})

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real")
            with self.assertRaises(ValueError):
                compare_and_swap(root / "link" / "state.json", {}, None)

    def test_cross_weekend_dates(self):
        self.assertEqual(dates_for("20260904", ["20260904", "20260907", "20260908"]),
                         ("20260904", "20260907", "20260908"))

    def test_delayed_run_stays_original_d(self):
        result = bind_slot("2026-09-07T13:15:00Z", "2026-09-07T18:16:02Z",
                           "2026-09-07T18:17:00Z", ["20260907", "20260908", "20260909"])
        self.assertEqual(result["signal_date"], "20260907")
        self.assertTrue(result["late"])

    def test_wrong_timezone_slot_rejected(self):
        with self.assertRaises(ValueError):
            bind_slot("2026-09-07T21:15:00Z", "2026-09-07T21:16:00Z",
                      "2026-09-07T21:17:00Z", ["20260907", "20260908", "20260909"])

    def test_twelve_hour_boundary_rejected(self):
        with self.assertRaises(ValueError):
            bind_slot("2026-09-07T13:15:00Z", "2026-09-08T01:15:00Z",
                      "2026-09-08T01:15:00Z", ["20260907", "20260908", "20260909"])

    def test_misfired_monitor_does_no_work(self):
        days = ["20260907", "20260908", "20260909", "20260910"]
        self.assertIsNone(monitor_target("2026-09-08T18:35:00Z", days))
        self.assertEqual(monitor_target("2026-09-08T13:35:00Z", days)["signal_date"], "20260908")

    def test_closed_day_and_midnight_rules(self):
        days = ["20260904", "20260907", "20260908", "20260909"]
        self.assertIsNone(monitor_target("2026-09-05T10:35:00Z", days))
        self.assertIsNone(monitor_target("2026-09-06T16:35:00Z", days))
        self.assertEqual(monitor_target("2026-09-07T16:35:00Z", days)["signal_date"], "20260907")


if __name__ == "__main__":
    unittest.main()
