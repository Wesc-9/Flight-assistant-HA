"""Pure-Python matching tests without Home Assistant installed."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

root = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant"
pkg = "flight_discovery_test"
mod = types.ModuleType(pkg)
mod.__path__ = [str(root)]
sys.modules[pkg] = mod

def load(name):
    spec = importlib.util.spec_from_file_location(pkg + "." + name, root / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

load("parsing")
discovery = load("discovery")

def flight(when, number="SK345", direction="D", destination="OSL"):
    return {"flight_number":number, "scheduled":when, "direction": direction,
            "destination":destination,"codeshares":[]}

class DiscoveryTests(unittest.TestCase):
    def test_single_norwegian_local_day(self):
        feeds={"TRD":[flight("2026-11-02T11:30:00Z")]}
        status,item=discovery.classify_match(feeds,"sk 345","2026-11-02")
        self.assertEqual(status, "found")
        self.assertEqual(item["origin"], "TRD")
    def test_no_neighbouring_day_guess(self):
        feeds={"TRD":[flight("2026-11-03T11:30:00Z")]}
        self.assertEqual(discovery.classify_match(feeds,"SK345","2026-11-02")[0],"not_found")
    def test_ambiguous_not_selected(self):
        feeds={"TRD":[flight("2026-11-02T11:30:00Z"),flight("2026-11-02T15:30:00Z")]}
        self.assertEqual(discovery.classify_match(feeds,"SK345","2026-11-02"),("ambiguous",None))
    def test_arrival_not_departure(self):
        feeds={"TRD":[flight("2026-11-02T11:30:00Z",direction="A")]}
        self.assertEqual(discovery.classify_match(feeds,"SK345","2026-11-02")[0],"not_found")
    def test_codeshare(self):
        item=flight("2026-11-02T11:30:00Z",number="DY123")
        item["codeshares"]=["SK345"]
        self.assertEqual(discovery.classify_match({"TRD":[item]},"SK345","2026-11-02")[0],"found")
    def test_utc_midnight_is_local_next_day(self):
        item=flight("2026-11-02T23:30:00Z")
        self.assertEqual(discovery.classify_match({"TRD":[item]},"SK345","2026-11-03")[0],"found")
