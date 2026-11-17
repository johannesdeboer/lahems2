"""Check input fallbacks independently of Home Assistant."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import TestCase

_source = Path(__file__).resolve().parents[1] / "custom_components/lahems2/model.py"
_spec = spec_from_file_location("lahems2_model", _source)
assert _spec and _spec.loader
model = module_from_spec(_spec)
_spec.loader.exec_module(model)


class TestInputs(TestCase):
    """Verify the fallback rules without connecting to a device."""

    def test_numeric_readings(self):
        for value in (None, "unavailable", "unknown", "NaN", "inf"):
            with self.subTest(value=value):
                self.assertIsNone(model.numeric_value(value))
        self.assertEqual(model.numeric_value("20.5"), 20.5)
        self.assertEqual(model.numeric_value("0"), 0)

    def test_primary_takes_priority(self):
        primary = {"usable": True, "entity_id": "sensor.primary"}
        backup = {"usable": True, "entity_id": "sensor.backup"}
        self.assertIs(model.active_temperature(primary, backup), primary)

    def test_backup_must_be_usable(self):
        unavailable = {"usable": False}
        backup = {"usable": True, "entity_id": "sensor.backup"}
        self.assertIs(model.active_temperature(unavailable, backup), backup)
        self.assertIsNone(model.active_temperature(unavailable, unavailable))
