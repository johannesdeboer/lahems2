"""Read Home Assistant inputs and resolve entity and device area assignments."""

import re
from dataclasses import dataclass

from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

from .const import (
    BACKUP,
    BATTERIES,
    BATTERY_ROLES,
    BINDINGS,
    ENERGY_ROLES,
    FRESHNESS_DEFAULTS,
    PRIMARY,
    RELAY,
    ROLES,
    SETTINGS,
    Role,
)
from .energy import energy_snapshot
from .model import active_temperature, numeric_value

_INDOOR = {"indoor", "room", "living", "bedroom", "kitchen", "hall", "ambient", "lounge"}
_EQUIPMENT = {
    "battery", "batterij", "boiler", "water", "coolant", "flow", "return", "inlet",
    "outlet", "cpu", "device", "equipment", "inverter", "compressor", "tank", "outdoor",
    "outside", "buiten", "heatsink", "pellet", "bms",
}
_SOURCE_HINTS = {
    "grid_meter": ({"grid", "net", "p1", "slimmelezer"}, set()),
    "import_price": ({"import", "buy", "electricity", "energy", "tibber"}, {"export", "sell", "feed"}),
    "export_price": ({"export", "sell", "feed", "feedin", "teruglevering"}, {"import", "buy"}),
    "pv_power": ({"solar", "pv", "enphase"}, {"forecast", "predicted"}),
    "solar_forecast": ({"solar", "pv"}, set()),
}
_TARIFF_WORDS = {"price", "tariff", "rate", "prijs", "tarief", "cost"}


@dataclass(frozen=True)
class Candidate:
    """A state or registry entity available for selection."""

    entity_id: str
    name: str
    area: str | None
    device_class: str | None
    disabled: bool

    @property
    def words(self) -> set[str]:
        """Return words used to rank suggestions."""
        return set(re.findall(r"[^\W_]+", f"{self.entity_id} {self.name}".lower()))


class Inputs:
    """Resolve configured input identities and take a read-only snapshot."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.entities = er.async_get(hass)
        self.devices = dr.async_get(hass)
        self.areas = ar.async_get(hass)

    def effective_area(self, entry: er.RegistryEntry | None) -> str | None:
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        device = self.devices.async_get(entry.device_id) if entry.device_id else None
        return device.area_id if device else None

    def area_name(self, area_id: str | None) -> str:
        area = self.areas.async_get_area(area_id) if area_id else None
        return area.name if area else area_id or "Unassigned"

    def candidate(self, entity_id: str) -> Candidate | None:
        entry = self.entities.async_get(entity_id)
        state = self.hass.states.get(entity_id)
        if entry is None and state is None:
            return None
        if not state or not state.attributes.get(ATTR_DEVICE_CLASS):
            device_class = (entry.device_class or entry.original_device_class) if entry else None
        else:
            device_class = state.attributes[ATTR_DEVICE_CLASS]
        return Candidate(
            entity_id,
            state.name if state else entry.name or entry.original_name or entity_id,
            self.effective_area(entry),
            device_class,
            bool(entry and entry.disabled_by),
        )

    def candidates(self, role: Role) -> list[Candidate]:
        domains = (role.domain,) if isinstance(role.domain, str) else role.domain
        entity_ids = set(self.hass.states.async_entity_ids(domains))
        entity_ids.update(
            entity_id for entity_id in self.entities.entities
            if entity_id.partition(".")[0] in domains
        )
        return [
            candidate for entity_id in sorted(entity_ids)
            if (candidate := self.candidate(entity_id))
            and not candidate.disabled
            and matches_role(candidate, role)
        ]

    def indoor_suggestions(self, area_id: str) -> list[Candidate]:
        """Rank room sensors first while keeping other temperature sensors selectable."""
        def rank(candidate: Candidate) -> tuple:
            words = candidate.words
            state = self.hass.states.get(candidate.entity_id)
            unavailable = state is None or numeric_value(state.state) is None
            return bool(words & _EQUIPMENT), unavailable, not bool(words & _INDOOR), candidate.entity_id

        return sorted(
            (candidate for candidate in self.candidates(ROLES[PRIMARY]) if candidate.area == area_id),
            key=rank,
        )

    def energy_suggestions(self) -> dict:
        """Offer conservative name and device-class matches for resident confirmation."""
        suggestions = {}
        for key, (wanted, unwanted) in _SOURCE_HINTS.items():
            role = ENERGY_ROLES[key]
            matches = []
            for candidate in self.candidates(role):
                words = candidate.words
                if not words & wanted or words & unwanted:
                    continue
                if key.endswith("_price") and not words & _TARIFF_WORDS:
                    continue
                if key == "solar_forecast" and not words & {"forecast", "predicted"}:
                    continue
                if key == "grid_meter" and words & (_TARIFF_WORDS | {"export", "solar", "pv"}):
                    continue
                matches.append(candidate.entity_id)
            if role.multiple and matches:
                suggestions[key] = matches
            elif len(matches) == 1:
                suggestions[key] = matches[0]
        if suggestions.get("import_price") == suggestions.get("export_price"):
            suggestions.pop("export_price", None)
        return suggestions

    def bind(self, entity_id: str, previous: dict | None = None) -> dict:
        """Capture the registry identity and current area."""
        candidate = self.candidate(entity_id)
        entry = self.entities.async_get(entity_id)
        if previous and (not candidate or candidate.disabled):
            return previous
        return {
            "entity_id": entity_id,
            "registry_id": entry.id if entry else None,
            "area": candidate.area if candidate else None,
        }

    def entity_id(self, binding: dict) -> str:
        """Follow a registry rename while keeping the last configured entity ID."""
        registry_id = binding.get("registry_id")
        entry = self.entities.entities.get_entry(registry_id) if registry_id else None
        return entry.entity_id if entry else binding["entity_id"]

    def reading(
        self, key: str, role: Role, binding: dict | None,
        *, max_age_minutes: float | None = None,
    ) -> dict:
        """Read one role and explain any data problem."""
        result = {
            "entity_id": None, "reading": None, "unit": None, "status": "Not configured",
            "usable": False, "area_changed": False, "entity_renamed": False,
        }
        if not binding:
            if role.required or role.report_missing:
                result["status"] = "Missing source"
            return result

        entity_id = result["entity_id"] = self.entity_id(binding)
        result["entity_renamed"] = entity_id != binding["entity_id"]
        entry = self.entities.async_get(entity_id)
        if binding.get("registry_id") and (not entry or entry.id != binding["registry_id"]):
            result["status"] = "Entity removed"
            return result
        candidate = self.candidate(entity_id)
        if not candidate:
            result["status"] = "Entity missing"
            return result
        result["area_changed"] = candidate.area != binding.get("area")
        if result["area_changed"]:
            result["previous_area"] = self.area_name(binding.get("area"))
            result["current_area"] = self.area_name(candidate.area)
        if result["entity_renamed"]:
            result["previous_entity_id"] = binding["entity_id"]
        if candidate.disabled:
            result["status"] = "Entity disabled"
            return result

        state = self.hass.states.get(entity_id)
        if state is None or state.state == "unavailable":
            result["status"] = "Unavailable"
            return result
        if state.state == "unknown":
            result["status"] = "Unknown"
            return result

        result["reading"] = state.state
        result["unit"] = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if max_age_minutes is not None:
            result["last_reported"] = state.last_reported.isoformat()
            result["max_age_minutes"] = max_age_minutes
        if not matches_role(candidate, role):
            result["status"] = "Unexpected entity type"
        elif (role.numeric and numeric_value(state.state) is None) or (
            key == RELAY and state.state not in {"on", "off"}
        ):
            result["status"] = "Invalid reading"
        elif max_age_minutes is not None and (
            utcnow() - state.last_reported
        ).total_seconds() >= max_age_minutes * 60:
            result["status"] = "Stale reading"
        else:
            result["status"] = (
                "Moved to a different area" if result["area_changed"]
                else "Entity renamed" if result["entity_renamed"]
                else "Available"
            )
            result["usable"] = True
            if key == "thermostat":
                result["comfort_target"] = state.attributes.get("temperature")
                result["comfort_target_unit"] = state.attributes.get(
                    "temperature_unit", self.hass.config.units.temperature_unit
                )
        return result

    def snapshot(self, profile: dict) -> dict:
        """Produce role-level data status and the active indoor measurement."""
        readings = {}
        bindings = profile.get(BINDINGS, {})
        prefs = FRESHNESS_DEFAULTS | profile.get(SETTINGS, {})
        for key, role in ROLES.items():
            if role.multiple:
                configured = bindings.get(key, [])
                for index, binding in enumerate(configured or [None], 1):
                    label = f"{role.label} {index}" if configured else role.label
                    readings[label] = self.reading(key, role, binding)
            else:
                readings[role.label] = self.reading(
                    key, role, bindings.get(key), max_age_minutes=prefs.get(role.freshness_setting)
                )
        for index, battery in enumerate(profile.get(BATTERIES, []), 1):
            for key, role in BATTERY_ROLES.items():
                readings[f"Battery {index} · {role.label}"] = self.reading(
                    key, role, battery[BINDINGS].get(key)
                )
        active = active_temperature(readings[ROLES[PRIMARY].label], readings[ROLES[BACKUP].label])
        schedule = readings[ROLES["ev_schedule"].label]
        schedule_state = self.hass.states.get(schedule["entity_id"]) if schedule["usable"] else None
        return {
            "energy": energy_snapshot(
                readings, profile, schedule_state.attributes.get("intervals") if schedule_state else None, utcnow()
            ),
            "roles": readings,
            "active_indoor_temperature": active,
            "active_indoor_source": (
                "Primary" if active is readings[ROLES[PRIMARY].label]
                else "Backup" if active else None
            ),
        }


def matches_role(candidate: Candidate, role: Role) -> bool:
    """Check the backend type independently of frontend selector filters."""
    domains = (role.domain,) if isinstance(role.domain, str) else role.domain
    classes = (role.device_class,) if isinstance(role.device_class, str) else role.device_class
    return candidate.entity_id.partition(".")[0] in domains and (
        classes is None or candidate.device_class in classes
    )
