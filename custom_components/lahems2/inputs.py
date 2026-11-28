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
    PELLET_ROLES,
    PRIMARY,
    RELAY,
    ROLES,
    SETTINGS,
    Role,
)
from .energy import QUARTER, energy_snapshot
from .model import active_temperature, numeric_value
from .shadow_plan import Battery, QuarterInput, forecast
from .pellet import Pellet, stock_kg

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


def input_bindings(profile: dict):
    """Yield role keys, display labels, bindings and the owning battery index."""
    bindings = profile.get(BINDINGS, {})
    for key, role in ROLES.items():
        if role.multiple:
            configured = bindings.get(key, [])
            for index, binding in enumerate(configured or [None], 1):
                label = f"{role.label} {index}" if configured else role.label
                yield key, label, role, binding, None
        else:
            yield key, role.label, role, bindings.get(key), None
    for index, battery in enumerate(profile.get(BATTERIES, []), 1):
        for key, role in BATTERY_ROLES.items():
            yield key, f"Battery {index} · {role.label}", role, battery[BINDINGS].get(key), index


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
        result["entity_name"] = candidate.name
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
        if max_age_minutes is not None:
            result["max_age_minutes"] = max_age_minutes
        if state is not None:
            result["last_reported"] = state.last_reported.isoformat()
        if state is None or state.state == "unavailable":
            result["status"] = "Unavailable"
            return result
        if state.state == "unknown":
            result["status"] = "Unknown"
            return result

        result["reading"] = state.state
        result["unit"] = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
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
            if key in {"thermostat", "pellet_room_thermostats"}:
                result["comfort_target"] = state.attributes.get("temperature")
                result["comfort_target_unit"] = state.attributes.get(
                    "temperature_unit", self.hass.config.units.temperature_unit
                )
        return result

    def snapshot(self, profile: dict) -> dict:
        """Produce role-level data status and the active indoor measurement."""
        prefs = FRESHNESS_DEFAULTS | profile.get(SETTINGS, {})
        readings = {
            label: self.reading(
                key, role, binding, max_age_minutes=prefs.get(role.freshness_setting)
            )
            for key, label, role, binding, _ in input_bindings(profile)
        }
        active = active_temperature(readings[ROLES[PRIMARY].label], readings[ROLES[BACKUP].label])
        schedule = readings[ROLES["ev_schedule"].label]
        schedule_state = self.hass.states.get(schedule["entity_id"]) if schedule["usable"] else None
        pellet = None
        if prefs.get("pellet_enabled"):
            fuel = stock_kg(readings[PELLET_ROLES["pellet_stock"].label], prefs.get("pellet_hopper_capacity_kg"))
            pellet = {
                "status": "Stock unknown" if fuel is None else "Stock estimate: empty" if fuel == 0 else "Stock estimate available",
                "stock_kg": fuel,
                "rooms": {
                    label: {
                        **reading,
                        "name": candidate.name if (candidate := self.candidate(reading["entity_id"])) else reading["entity_id"],
                    } for label, reading in readings.items()
                    if label.startswith(("Pellet stove room temperature", "Pellet stove room thermostat"))
                    and reading["entity_id"]
                },
                "heat_request": readings[ROLES[RELAY].label]["reading"],
            }
        return {
            "energy": energy_snapshot(
                readings, profile, schedule_state.attributes.get("intervals") if schedule_state else None, utcnow()
            ),
            "roles": readings,
            "pellet_stove": pellet,
            "active_indoor_temperature": active,
            "active_indoor_source": (
                "Primary" if active is readings[ROLES[PRIMARY].label]
                else "Backup" if active else None
            ),
        }

    def planning_inputs(self, profile, snapshot, now, currency):
        """Build an immutable comparison request from current Home Assistant states."""
        bindings = profile.get(BINDINGS, {})
        if "demand_forecast" not in bindings:
            return "Not configured", "Choose forecast sources in Configure → Shadow planning.", None

        def read(binding, field, *, power=False):
            entity_id = self.entity_id(binding)
            entry = self.entities.async_get(entity_id)
            if binding.get("registry_id") and (not entry or entry.id != binding["registry_id"]):
                return {}
            candidate = self.candidate(entity_id)
            state = self.hass.states.get(entity_id)
            if not state or not candidate or candidate.disabled or state.state in {"unknown", "unavailable"}:
                return {}
            if not power:
                units = {f"{currency}/kWh"} | ({"€/kWh"} if currency == "EUR" else set())
                if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) not in units:
                    return {}
            return forecast(state.attributes.get("intervals"), field, nonnegative=power)

        demand = read(bindings["demand_forecast"], "power_w", power=True)
        heat_pump_binding = bindings.get("heat_pump_demand_forecast")
        heat_pump = read(heat_pump_binding, "power_w", power=True) if heat_pump_binding else {}
        tariffs = []
        for key in ("import", "export"):
            binding = bindings.get(f"{key}_tariff_forecast") or bindings.get(f"{key}_price")
            tariffs.append(read(binding, "price_per_kwh") if binding else {})
        solar_sources = bindings.get("solar_forecast", [])
        if bindings.get("pv_power") and not solar_sources:
            return "Waiting for forecasts", "Select a PV forecast for the configured solar production.", None
        solar = [read(binding, "power_w", power=True) for binding in solar_sources]

        energy = snapshot["energy"]
        settings = profile.get(SETTINGS, {})
        stove = snapshot["pellet_stove"]
        pellet = (
            Pellet.from_settings(stove["stock_kg"], settings)
            if stove and settings.get("pellet_serves_forecast_load")
            and any(reading["usable"] for reading in stove["rooms"].values())
            else None
        )
        batteries = tuple(Battery.from_readings(battery) for battery in energy["batteries"])
        if None in batteries or (not batteries and not pellet):
            return "Waiting for battery inputs", "Stationary batteries need confirmed limits and a usable charge level at or above reserve.", None
        has_ev = any(key in bindings for key in ("ev_power", "ev_connected", "ev_schedule"))
        if has_ev and energy["ev_connected"] is None:
            return "Waiting for EV connection", "The EV connection sensor needs to be available for planning.", None
        needs_ev = has_ev and energy["ev_connected"] is True
        ev = forecast(energy["external_ev_schedule"], "power_w") if needs_ev else {}

        start = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        if start < now:
            start += QUARTER
        slots = []
        for index in range(96):
            quarter = start + index * QUARTER
            load = demand.get(quarter)
            prices = [series.get(quarter) for series in tariffs]
            pv = [series.get(quarter) for series in solar]
            ev_w = ev.get(quarter) if needs_ev else 0
            if None in [load, *prices, *pv, ev_w]:
                break
            heat_w = heat_pump.get(quarter)
            slots.append(QuarterInput(
                quarter, load, sum(pv), ev_w, *prices,
                heat_pump_w=heat_w if heat_w is not None and heat_w <= load else None,
            ))
        if not slots:
            return "Waiting for forecasts", "The next full quarter needs demand and buy/sell prices, plus PV or an accepted EV schedule when applicable.", None
        efficiency = settings.get("planning_efficiency_percent", 90) / 100
        return "Calculating", "Comparing future quarters in Shadow Mode.", (
            tuple(slots), batteries, currency, efficiency, energy["import_limit_w"], energy["export_limit_w"], pellet,
        )


def matches_role(candidate: Candidate, role: Role) -> bool:
    """Check the backend type independently of frontend selector filters."""
    domains = (role.domain,) if isinstance(role.domain, str) else role.domain
    classes = (role.device_class,) if isinstance(role.device_class, str) else role.device_class
    return candidate.entity_id.partition(".")[0] in domains and (
        classes is None or candidate.device_class in classes
    )
