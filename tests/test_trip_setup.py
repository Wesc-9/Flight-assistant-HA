"""Pure tests for the API-first manual itinerary wizard."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

folder = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant"
package = "trip_setup_test"
pkg = types.ModuleType(package)
pkg.__path__ = [str(folder)]
sys.modules[package] = pkg

def load(name):
    spec = importlib.util.spec_from_file_location(
        package + "." + name, folder / (name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

load("parsing")
m = load("trip_setup")

class TestTripSetup(unittest.TestCase):
    def test_auto_company(self):
        for number, expected in [
            ("SK 123", "sas"), ("DY701", "norwegian"),
            ("D8123", "norwegian"), ("WF200", "wideroe"),
        ]:
            with self.subTest(number=number):
                self.assertEqual(m.airline_from_number(number), expected)

    def test_segment_normalizes_and_leaves_airport_optional(self):
        segment = m.make_segment("sk 345", "2027-05-12", "")
        self.assertEqual(segment, {
            "flight_number": "SK345",
            "flight_date": "2027-05-12",
            "departure_airport": None
        })
        self.assertEqual(m.make_segment("DY123", "2027-05-12", "trd")[
            "departure_airport"], "TRD")

    def test_bad_date_airport_or_number_fails(self):
        for args in [
            ("SK345", "2027-15-12", ""),
            ("FOO", "2027-05-12", ""),
            ("DY123", "2027-05-12", "NO"),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                m.make_segment(*args)

    def test_airline_label(self):
        self.assertEqual(m.airline_display("norwegian"), "Norwegian")
        self.assertEqual(m.airline_display("wideroe"), "Widerøe")
