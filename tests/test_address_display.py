"""Pure tests for user-facing durations; no HA installation necessary."""
from datetime import datetime
import importlib.util
from pathlib import Path
import unittest
p = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant/display.py"
spec = importlib.util.spec_from_file_location("display", p)
display = importlib.util.module_from_spec(spec)
spec.loader.exec_module(display)

class TestTimeStrings(unittest.TestCase):
    def test_short(self):
        self.assertEqual(display.minutes_text(15), "15 min")
    def test_hour(self):
        self.assertEqual(display.minutes_text(60), "1 t")
    def test_hour_and_half(self):
        self.assertEqual(display.minutes_text(90), "1 t 30 min")
    def test_two_hours(self):
        self.assertEqual(display.minutes_text(120), "2 t")
    def test_invalid(self):
        with self.assertRaises(ValueError):
            display.minutes_text(-1)
