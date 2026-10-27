"""Pure-Python tests: python -m unittest discover -s tests -v."""
import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timezone, timedelta

p = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant/parsing.py"
spec = importlib.util.spec_from_file_location("flight_parsing", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

XML = """<airport><flights>
<flight uniqueId="1"><flight_id>SK345</flight_id><schedule_time>2026-11-02T11:30:00Z</schedule_time>
<arr_dep>D</arr_dep><airport>OSL</airport><gate>A12</gate><check_in>1 2</check_in>
<status code="E" time="2026-11-02T12:00:00Z"/></flight>
</flights></airport>"""

class TestParsing(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(m.normalize_flight("sk 345"), "SK345")
        with self.assertRaises(ValueError):
            m.normalize_flight("sk;123")
        self.assertEqual(m.normalize_airport("trd"), "TRD")
    def test_xml_match(self):
        data = m.parse_avinor(XML)
        self.assertEqual(data[0]["gate"], "A12")
        scheduled = datetime(2026, 11, 2, 12, 30, tzinfo=timezone(timedelta(hours=1)))
        self.assertEqual(m.match_departure(data, "SK345", scheduled)["destination"], "OSL")
        self.assertIsNone(m.match_departure(data, "SK345", scheduled + timedelta(days=1)))
    def test_margin_ignores_delay(self):
        date = datetime(2026, 11, 2, 12, 30, tzinfo=timezone.utc)
        self.assertEqual(m.recommended_drive_departure(date, 120, 35, 15),
                         datetime(2026, 11, 2, 9, 40, tzinfo=timezone.utc))
    def test_bad_datetime(self):
        self.assertIsNone(m.parse_datetime("2026-11-02T12:00:00"))
        with self.assertRaises(ValueError):
            m.parse_avinor("<!DOCTYPE foo><airport/>")

if __name__ == "__main__":
    unittest.main()
