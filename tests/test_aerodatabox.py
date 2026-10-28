"""AeroDataBox parser and request gating, no credentials or HA required."""
import sys, types, importlib.util
from pathlib import Path
from datetime import datetime, timezone
import unittest
base = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant"
pkg = types.ModuleType("aero_test")
pkg.__path__ = [str(base)]
sys.modules["aero_test"] = pkg
# To test pure parsing logic, stub only the two Home Assistant imports.
for name in ["homeassistant","homeassistant.helpers","homeassistant.helpers.aiohttp_client"]:
    stub=types.ModuleType(name)
    sys.modules[name]=stub
sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession=lambda hass:None
spec=importlib.util.spec_from_file_location("aero_test.aerodatabox",base/"aerodatabox.py")
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)

raw=[{
"number":"SK345", "status":"Scheduled",
"departure":{"airport":{"iata":"TRD"},"scheduledTime":{"local":"2027-03-15T08:15:00","utc":"2027-03-15T07:15:00"},"gate":"A12"},
"arrival":{"airport":{"iata":"OSL"},"scheduledTime":{"local":"2027-03-15T09:10:00","utc":"2027-03-15T08:10:00"}}
}]

class TestAero(unittest.TestCase):
    def test_future_flight(self):
        status,flight=m.parse_flights(raw,"SK 345","2027-03-15")
        self.assertEqual(status,"found")
        self.assertEqual(flight["origin"],"TRD")
        self.assertEqual(flight["scheduled"],"2027-03-15T07:15:00+00:00")
        self.assertFalse(flight["airline_verified"])
    def test_prevent_wrong_date(self):
        self.assertEqual(m.parse_flights(raw,"SK345","2027-03-16")[0],"not_found")
    def test_prevent_wrong_origin(self):
        self.assertEqual(m.parse_flights(raw,"SK345","2027-03-15","OSL")[0],"not_found")
    def test_ambiguity(self):
        x={**raw[0], "departure":{**raw[0]["departure"],"airport":{"iata":"BGO"}}}
        self.assertEqual(m.parse_flights(raw+[x],"SK345","2027-03-15")[0],"ambiguous")
    def test_no_invented_schedule(self):
        broken={**raw[0],"departure":{**raw[0]["departure"],"scheduledTime":{}}}
        self.assertEqual(m.parse_flights([broken],"SK345","2027-03-15")[0],"not_found")
    def test_no_bogus_response(self):
        with self.assertRaises(ValueError):
            m.parse_flights({"flights":[]}, "SK345", "2027-03-15")
