"""Read Home Assistant Energy and suggest compatible LAHEMS2 inputs."""

from homeassistant.components.energy import async_get_manager
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.helpers import entity_registry as er

from .const import BATTERY_ROLES, ROLES
from .inputs import Inputs, matches_role
from .model import numeric_value

V2G_INPUTS = {
    "ev_power": "sensor.charger_real_charging_power",
    "ev_connected": "binary_sensor.v2g_liberty_ev_plugged_in",
    "ev_soc": "sensor.car_state_of_charge",
    "ev_schedule": "sensor.v2g_liberty_accepted_ev_schedule",
}
_V2G_POWER = "sensor.v2g_liberty_charger_real_charging_power_energ"


class Sources:
    """Discover sources without saving Energy preferences."""

    def __init__(self, inputs: Inputs, prefs: dict):
        self.inputs = inputs
        self.energy = prefs.get("energy_sources", [])
        self.consumers = {
            row["stat_consumption"]: row for row in prefs.get("device_consumption", [])
            if row.get("stat_consumption")
        }

    @classmethod
    async def load(cls, inputs: Inputs):
        """Read the preferences through Home Assistant's Energy manager."""
        manager = await async_get_manager(inputs.hass)
        return cls(inputs, manager.data or {})

    def _compatible(self, entity_id, key):
        if not entity_id:
            return False
        candidate = self.inputs.candidate(entity_id)
        if not candidate or candidate.disabled or not matches_role(candidate, (ROLES | BATTERY_ROLES)[key]):
            return False
        state = self.inputs.hass.states.get(entity_id)
        unit = state and state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if key in {"battery_soc", "ev_soc"}:
            return unit == "%"
        if key.endswith("_power"):
            return unit in {"W", "kW"}
        return True

    def _ids(self, rows, field, key):
        return list(dict.fromkeys(
            entity_id for row in rows if self._compatible(entity_id := row.get(field), key)
        ))

    def defaults(self):
        """Use one net power source and all distinct measured solar sources."""
        values = {}
        grid = [row for row in self.energy if row["type"] == "grid"]
        combined = [
            row for row in grid
            if (row.get("power_config") or {}).get("stat_rate_from")
            and (row.get("power_config") or {}).get("stat_rate_to")
        ]
        meters = self._ids(grid, "stat_rate", "grid_meter")
        # Separate tariff counters on one device can repeat the same grid
        # connection. Prefer its combined import/export reading when present.
        devices = [self.device_for_source(row.get("stat_energy_from")) for row in grid]
        if devices and all(devices) and len(set(devices)) == 1:
            meters = self._ids(combined, "stat_rate", "grid_meter") or meters
        if not meters:
            meters = self._ids(grid, "stat_energy_from", "grid_meter")
        if len(meters) == 1:
            values["grid_meter"] = meters[0]
        for key, field in (("import_price", "entity_energy_price"), ("export_price", "entity_energy_price_export")):
            candidates = self._ids(grid, field, key)
            if len(candidates) == 1:
                values[key] = candidates[0]
        solar = self._ids([row for row in self.energy if row["type"] == "solar"], "stat_rate", "pv_power")
        if solar:
            values["pv_power"] = solar
        return values

    def heat_defaults(self, source):
        row = self.consumers.get(source, {})
        return {
            key: row[field]
            for key, field in (("heat_pump_power", "stat_rate"), ("heat_pump_energy", "stat_consumption"))
            if self._compatible(row.get(field), key)
        }

    def device_defaults(self, device_id, roles):
        """Fill only unique, type-compatible siblings on the chosen device."""
        if not device_id:
            return {}
        entities = er.async_entries_for_device(self.inputs.entities, device_id)
        defaults = {}
        for key in roles:
            matches = [
                entry.entity_id for entry in entities
                if self._compatible(entry.entity_id, key)
                and self._matches_device_role(entry.entity_id, key)
            ]
            if len(matches) == 1:
                defaults[key] = matches[0]
        return defaults

    def _matches_device_role(self, entity_id, key):
        candidate = self.inputs.candidate(entity_id)
        if key == "ev_connected":
            return candidate.device_class == "plug" or bool(candidate.words & {"plugged", "aangesloten"})
        if key in {"battery_soc", "ev_soc"}:
            if candidate.words & {"max", "min", "minimum", "maximum", "limit", "target", "reserve", "required"}:
                return False
            return candidate.device_class == "battery" or bool(candidate.words & {"soc", "battery", "batterij"})
        if key.endswith("_power"):
            return not candidate.words & {"max", "min", "maximum", "minimum", "limit", "setpoint", "scheduled", "target"}
        return True

    def device_for_source(self, entity_id):
        entry = self.inputs.entities.async_get(entity_id) if entity_id else None
        return entry.device_id if entry else None

    def storage(self):
        """Return the Energy storage rows with stable sensor references."""
        return {
            identity: row for row in self.energy if row["type"] == "battery"
            if (identity := row.get("stat_energy_from") or row.get("stat_energy_to") or row.get("stat_rate"))
        }

    def storage_label(self, identity, row):
        label = row.get("name")
        for entity_id in (row.get("stat_rate"), identity):
            entry = self.inputs.entities.async_get(entity_id) if entity_id else None
            device = self.inputs.devices.async_get(entry.device_id) if entry and entry.device_id else None
            if device:
                label = device.name_by_user or device.name or label
                break
        return f"{label} ({identity})" if label else identity

    def battery_defaults(self, identity, device_id):
        row = self.storage().get(identity, {})
        defaults = {}
        for key, field in (("battery_power", "stat_rate"), ("battery_soc", "stat_soc")):
            if self._compatible(row.get(field), key):
                defaults[key] = row[field]
        device_id = device_id or self.device_for_source(row.get("stat_rate")) or self.device_for_source(identity)
        defaults = self.device_defaults(device_id, BATTERY_ROLES) | defaults
        if defaults.get("battery_power") and row.get("stat_rate") == defaults["battery_power"]:
            # HA Energy uses positive discharge for its normalized battery power.
            defaults["power_sign"] = "positive_discharge"
        if (capacity := numeric_value(row.get("capacity"))) is not None and capacity > 0:
            defaults["capacity"] = capacity
        return defaults

    def ev_defaults(self, use_v2g, identity, device_id):
        row = self.storage().get(identity, {})
        defaults = self.device_defaults(device_id, ("ev_power", "ev_connected", "ev_soc", "ev_departure"))
        if self._compatible(row.get("stat_rate"), "ev_power"):
            defaults.update(ev_power=row["stat_rate"], ev_positive_discharge=True)
        if self._compatible(row.get("stat_soc"), "ev_soc"):
            defaults["ev_soc"] = row["stat_soc"]
        if use_v2g:
            # The accepted schedule and V2G Liberty's raw charger power use positive charging.
            defaults.pop("ev_power", None)
            defaults.pop("ev_soc", None)
            defaults.pop("ev_positive_discharge", None)
            for key, entity_id in V2G_INPUTS.items():
                if self._compatible(entity_id, key):
                    defaults[key] = entity_id
            if "ev_power" not in defaults and self._compatible(_V2G_POWER, "ev_power"):
                defaults["ev_power"] = _V2G_POWER
            if "ev_power" in defaults:
                defaults["ev_positive_discharge"] = False
        return defaults

    def storage_identity(self, entity_id):
        if not entity_id:
            return None
        matches = []
        for identity, row in self.storage().items():
            power_config = row.get("power_config") or {}
            aliases = (identity, row.get("stat_rate"), *power_config.values())
            if entity_id in aliases or (
                entity_id == V2G_INPUTS["ev_power"] and _V2G_POWER in aliases
            ):
                matches.append(identity)
        return matches[0] if len(matches) == 1 else None

    def same_storage(self, first, second, ev_source=None):
        if not first:
            return False
        identity = self.storage_identity(first)
        return first == second or bool(identity and identity in {ev_source, self.storage_identity(second)})
