"""The API provider setting applies to one integration, not each trip."""
import importlib.util
from pathlib import Path
import unittest

root = Path(__file__).resolve().parents[1] / "custom_components/flight_assistant/provider_settings.py"
spec=importlib.util.spec_from_file_location("provider_settings", root)
p=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class TestProviderSettings(unittest.TestCase):
    def test_avinor_only_ignores_stored_key(self):
        self.assertEqual(p.effective_aerodatabox_key("avinor", "some-secret"), "")
    def test_auto_uses_key_when_present(self):
        self.assertEqual(p.effective_aerodatabox_key("auto", "  secret  "), "secret")
        self.assertEqual(p.effective_aerodatabox_key("auto", ""), "")
    def test_legacy_settings_default_to_auto(self):
        self.assertEqual(p.effective_aerodatabox_key(None, "secret"), "secret")
    def test_aerodatabox_choice_requires_key(self):
        self.assertTrue(p.source_needs_key("aerodatabox"))
        self.assertFalse(p.source_needs_key("auto"))
    def test_unknown_choice_cannot_initiate_paid_query(self):
        self.assertEqual(p.effective_aerodatabox_key("unknown", "secret"), "")
