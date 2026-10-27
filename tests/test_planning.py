"""Run with: python -m unittest discover -s tests -v."""
import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timezone, timedelta

p = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant/planning.py"
spec = importlib.util.spec_from_file_location("flight_planning", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class TestPlanning(unittest.TestCase):
    def setUp(self):
        self.when = datetime(2026, 11, 2, 12, 30, tzinfo=timezone.utc)

    def test_two_standard_margins(self):
        self.assertEqual(m.default_margin("domestic"), 90)
        self.assertEqual(m.default_margin("international"), 120)

    def test_three_options_domestic(self):
        windows = m.arrival_windows(self.when, 90, "domestic", True)
        self.assertEqual([item["margin_minutes"] for item in windows], [120, 90, 60])
        self.assertEqual(windows[1]["kind"], "recommended")

    def test_late_option_is_dropped_when_too_tight(self):
        windows = m.arrival_windows(self.when, 60, "domestic", True)
        self.assertEqual([item["number"] for item in windows], [1, 2])

    def test_user_selected_margin_is_never_suppressed(self):
        windows = m.arrival_windows(self.when, 45, "international", True)
        self.assertEqual([item["number"] for item in windows], [1, 2])

    def test_travel_time_departure(self):
        arrive = self.when - timedelta(minutes=90)
        self.assertEqual(m.driving_leave_at(arrive, 40, 15),
                         (self.when - timedelta(minutes=145)).isoformat())

    def test_no_gps_claim(self):
        arrival = self.when + timedelta(hours=1)
        self.assertEqual(m.time_progress_percent(self.when, arrival,
                         self.when + timedelta(minutes=30)), 50)
        self.assertIsNone(m.time_progress_percent(self.when, self.when, self.when))

    def test_invalid_margin(self):
        with self.assertRaises(ValueError):
            m.arrival_windows(self.when, 53, "domestic", False)

if __name__ == "__main__":
    unittest.main()
