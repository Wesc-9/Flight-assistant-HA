"""Unit tests for both free transport sources, no HA runtime needed."""
import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta
import unittest

folder = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant"
def load(name):
    spec = importlib.util.spec_from_file_location(name, folder / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
parser = load("transport_parsing")
def parse_dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s)
    except ValueError: return None

when = "2026-11-02T10:00:00+01:00"
last = "2026-11-02T11:00:00+01:00"
deadline = datetime.fromisoformat("2026-11-02T11:30:00+01:00")
leg = {
    "mode": "rail",
    "expectedStartTime": when,
    "expectedEndTime": last,
    "fromPlace": {"name": "Trondheim"},
    "toPlace": {"name": "Lufthavn"},
    "line": {"publicCode": "F6", "name": "Flytog"},
}
def payload(legs):
    return {"data":{"trip":{"tripPatterns":[{"legs":legs}]}}}

class TestWaze(unittest.TestCase):
    def test_picks_shortest_in_minutes(self):
        self.assertEqual(parser.shortest_waze_route(
            {"routes":[{"duration":44.3}, {"duration":37.8}, {"duration":"12"}]}),37.8)
    def test_rejects_missing_or_bogus(self):
        for item in [None, {}, {"routes":[]}, {"routes":[{"duration":-1}]},
                     {"routes":[{"duration":True}]}, {"routes":[{"duration":"20"}]}]:
            self.assertIsNone(parser.shortest_waze_route(item))

class TestEntur(unittest.TestCase):
    def test_arrives_before_target(self):
        result = parser.parse_entur_patterns(payload([leg]),deadline,parse_dt)
        self.assertEqual(result["minutes"],60)
        self.assertEqual(result["legs"][0]["route"],"F6")
        self.assertEqual(result["source"],"Entur")
    def test_later_than_airport_margin_is_unsafe(self):
        too_late={**leg, "expectedEndTime":"2026-11-02T12:00:00+01:00"}
        self.assertIsNone(parser.parse_entur_patterns(payload([too_late]),deadline,parse_dt))
    def test_walking_only_is_not_public_transport(self):
        walk={**leg,"mode":"foot"}
        self.assertIsNone(parser.parse_entur_patterns(payload([walk]),deadline,parse_dt))
    def test_graphql_error_is_not_a_route(self):
        self.assertIsNone(parser.parse_entur_patterns({"errors":[{"message":"x"}]},deadline,parse_dt))
    def test_absent_response_is_not_a_route(self):
        self.assertIsNone(parser.parse_entur_patterns({"data":{"trip":None}},deadline,parse_dt))
    def test_choose_latest_feasible(self):
        older={**leg,"expectedStartTime":"2026-11-02T08:30:00+01:00"}
        t=payload([older])
        t["data"]["trip"]["tripPatterns"].append({"legs":[leg]})
        result=parser.parse_entur_patterns(t,deadline,parse_dt)
        self.assertEqual(result["leave_at"],when)
