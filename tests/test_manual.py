"""Manual itinerary validation tests, without Home Assistant dependencies."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from datetime import datetime

folder = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant"
pkg = "flight_assistant_test"
sys.modules[pkg] = types.ModuleType(pkg)
sys.modules[pkg].__path__ = [str(folder)]
def load(module):
    spec = importlib.util.spec_from_file_location(pkg + "." + module, folder / (module + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded

load("parsing")
m = load("manual")

class TestManualItinerary(unittest.TestCase):
    def setUp(self):
        self.data = {
            "flight_number": "SK 345", "origin": "trd", "destination": "osl",
            "scheduled": "2026-11-02T12:30:00",
            "arrival_scheduled": "2026-11-02T13:25:00"
        }

    def test_manual_uses_timezone_and_no_pnr(self):
        result = m.manual_flight(self.data, "Europe/Oslo")
        self.assertEqual(result["flight_number"], "SK345")
        self.assertEqual(result["origin"], "TRD")
        self.assertEqual(result["destination"], "OSL")
        self.assertEqual(result["scheduled"], "2026-11-02T11:30:00+00:00")
        self.assertEqual(result["source"], "manual")
        self.assertFalse(result["airline_verified"])
        self.assertNotIn("booking_reference", result)

    def test_explicit_offsets(self):
        self.data["scheduled"] = "2026-11-02T12:30:00+01:00"
        self.data["arrival_scheduled"] = "2026-11-02T13:25:00+01:00"
        self.assertEqual(m.manual_flight(self.data, "Europe/Oslo")["scheduled"], "2026-11-02T11:30:00+00:00")

    def test_overnight(self):
        self.data["scheduled"] = "2026-11-02T23:30:00"
        self.data["arrival_scheduled"] = "2026-11-03T01:30:00"
        self.assertLess(m.manual_flight(self.data, "Europe/Oslo")["scheduled"],
                        m.manual_flight(self.data, "Europe/Oslo")["arrival_scheduled"])

    def test_rejects_invalid(self):
        for key,value in [("flight_number", "???"), ("destination", "TRD"),
                          ("arrival_scheduled", "2026-11-02T10:30:00")]:
            broken = {**self.data, key:value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.manual_flight(broken, "Europe/Oslo")

if __name__ == "__main__":
    unittest.main()
