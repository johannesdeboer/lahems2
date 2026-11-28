"""Expose read-only data status, measurements and heating proposals."""

import logging
from datetime import timedelta
from hashlib import sha256

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util.dt import utcnow

from .const import (
    AREA, BATTERIES, BINDINGS, DOMAIN, HEAT_ROLES, MODE, PELLET_ROLES, PLAN_ROLES,
    RELAY, ROLES, SETTINGS, ZONE_ROLES,
)
from .energy import watts
from .inputs import Inputs, input_bindings
from .model import numeric_value
from .planning import propose
from .shadow_plan import compare

_LOGGER = logging.getLogger(__name__)
_INPUT_GROUPS = {
    **dict.fromkeys(ZONE_ROLES, ("temperatures", "Temperature monitoring")),
    **dict.fromkeys(HEAT_ROLES, ("heat_pump", "Heat pump")),
    **dict.fromkeys((*PELLET_ROLES, RELAY, "stove_room_temperature"), ("pellet_stove", "Pellet stove")),
    **dict.fromkeys(PLAN_ROLES, ("forecasts", "Planning forecasts")),
    **dict.fromkeys(("grid_meter", "import_price", "export_price"), ("grid", "Electricity grid")),
    **dict.fromkeys(("pv_power", "solar_forecast"), ("solar", "Solar")),
    **dict.fromkeys(("ev_power", "ev_connected", "ev_soc", "ev_departure", "ev_schedule"), ("ev", "Electric vehicle")),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the read-only entities for this configuration entry."""
    coordinator = SnapshotCoordinator(hass, entry)
    coordinator.start()
    entities = [
        DataStatus(coordinator, entry),
        Actual(coordinator, entry),
        Proposed(coordinator, entry),
        EnergyBalance(coordinator, entry),
    ]
    if coordinator.profile.get(SETTINGS, {}).get("pellet_enabled"):
        entities.append(PelletStove(coordinator, entry))
    inputs = {}
    for key, label, role, binding, battery_index in input_bindings(coordinator.profile):
        if not binding:
            continue
        if battery_index is None:
            group, name = _INPUT_GROUPS[key]
        else:
            battery = coordinator.profile[BATTERIES][battery_index - 1]
            power = battery[BINDINGS].get("battery_power") or binding
            group = f"battery_{_binding_key(power)}"
            name = f"Battery {battery_index}"
        sensor = ConfiguredInput(coordinator, entry, key, label, role, group, name, binding)
        inputs.setdefault(sensor.unique_id, sensor)

    # Options edits reload the platform. Drop only mirrors that are no longer configured.
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_configured_input_"
    obsolete_devices = set()
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registered.platform == DOMAIN
            and registered.unique_id.startswith(prefix)
            and registered.unique_id not in inputs
        ):
            registry.async_remove(registered.entity_id)
            if registered.device_id:
                obsolete_devices.add(registered.device_id)
    entities.extend(inputs.values())
    devices = dr.async_get(hass)
    active_identifiers = {
        identifier for entity in entities if entity.device_info
        for identifier in entity.device_info["identifiers"]
    }
    for device_id in obsolete_devices:
        device = devices.async_get(device_id)
        if (
            device
            and device.identifiers
            and all(domain == DOMAIN and key.startswith(f"{entry.entry_id}_")
                    for domain, key in device.identifiers)
            and not device.identifiers & active_identifiers
            and not er.async_entries_for_device(registry, device_id, include_disabled_entities=True)
        ):
            devices.async_remove_device(device_id)
    async_add_entities(entities)


def _binding_key(binding: dict) -> str:
    identity = binding.get("registry_id") or binding["entity_id"]
    return sha256(identity.encode()).hexdigest()[:20]


def _device_info(entry, key, name):
    return DeviceInfo(identifiers={(DOMAIN, f"{entry.entry_id}_{key}")}, name=name)


class SnapshotCoordinator(DataUpdateCoordinator[dict]):
    """Refresh a shared snapshot when inputs change or become stale."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name="LAHEMS2", config_entry=entry)
        self.profile = entry.options or entry.data
        self._entry = entry
        self._inputs = Inputs(hass)
        self._watched_ids = set()
        self._stop_watching = None
        self._plan_request = None
        self._plan_task = None
        self._plan = {}

    @callback
    def start(self) -> None:
        for event in ("entity_registry_updated", "device_registry_updated", "area_registry_updated"):
            self._entry.async_on_unload(self.hass.bus.async_listen(event, self._refresh))
        self._entry.async_on_unload(async_track_time_interval(self.hass, self._refresh, timedelta(minutes=1)))
        self._entry.async_on_unload(self._unwatch)
        self._refresh()

    @callback
    def _unwatch(self) -> None:
        if self._stop_watching is not None:
            self._stop_watching()
            self._stop_watching = None

    @callback
    def _refresh(self, _event=None) -> None:
        snapshot = self._inputs.snapshot(self.profile)
        entity_ids = {
            reading["entity_id"] for reading in snapshot["roles"].values() if reading["entity_id"]
        }
        if entity_ids != self._watched_ids:
            self._unwatch()
            self._watched_ids = entity_ids
            if entity_ids:
                self._stop_watching = async_track_state_change_event(self.hass, entity_ids, self._refresh)

        area = self.profile.get(AREA)
        proposal = propose(snapshot, self.profile.get(SETTINGS, {}), self.hass.config.currency)
        status, reason, request = self._inputs.planning_inputs(
            self.profile, snapshot, utcnow(), self.hass.config.currency,
        )
        if request is None or request != self._plan_request:
            self._plan = {"status": status, "reason": reason}
            self._plan_request = request
            if request is not None and self._plan_task is None:
                self._plan_task = self._entry.async_create_background_task(
                    self.hass, self._calculate_plan(), "LAHEMS2 shadow plan", eager_start=False,
                )
        self.async_set_updated_data({
            "preferred_area": self._inputs.area_name(area) if area else None,
            "snapshot": snapshot,
            "proposal": proposal,
            "shadow_plan": self._plan,
        })

    async def _calculate_plan(self):
        """Coalesce updates and discard results for superseded input sets."""
        try:
            while (request := self._plan_request) is not None:
                try:
                    result = await self.hass.async_add_executor_job(compare, *request)
                except Exception:
                    _LOGGER.exception("Unable to calculate the LAHEMS2 shadow plan")
                    result = {"status": "Unavailable", "reason": "Could not calculate the proposal."}
                if request == self._plan_request:
                    self._plan = result
                    self.async_set_updated_data({**self.data, "shadow_plan": result})
                    return
        finally:
            self._plan_task = None


class SnapshotSensor(CoordinatorEntity[SnapshotCoordinator], SensorEntity):
    """A read-only view of the same current input snapshot."""

    _attr_has_entity_name = True

    @property
    def capability_attributes(self) -> dict:
        """Identify the dashboard's sources independently of entity ID changes."""
        return {"lahems2_view": self._attr_translation_key}

    def __init__(self, coordinator: SnapshotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        key = self._attr_translation_key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_suggested_object_id = f"lahems2_{key}"


class DataStatus(SnapshotSensor):
    """Report the availability and usability of every configured input."""

    _attr_translation_key = "data_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"roles", "active_indoor_temperature"})
    _attr_icon = "mdi:list-status"

    @property
    def native_value(self) -> str:
        complete = all(
            reading["status"] in {"Available", "Not configured"}
            for reading in self.coordinator.data["snapshot"]["roles"].values()
        )
        return "Ready" if complete else "Check inputs"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        snapshot = data["snapshot"]
        return {
            "mode": MODE,
            "preferred_area": data["preferred_area"],
            "roles": snapshot["roles"],
            "active_indoor_temperature": snapshot["active_indoor_temperature"],
            "active_indoor_source": snapshot["active_indoor_source"],
        }


class ConfiguredInput(CoordinatorEntity[SnapshotCoordinator], SensorEntity):
    """Show a configured source value without taking ownership of its entity."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"last_reported", "max_age_minutes"})

    def __init__(self, coordinator, entry, key, label, role, group, name, binding):
        super().__init__(coordinator)
        self._label = label
        self._numeric = role.numeric
        self._attr_name = role.label if key.startswith("battery_") else label
        self._attr_device_info = _device_info(entry, group, name)
        self._attr_unique_id = (
            f"{entry.entry_id}_configured_input_{group}_{key}_{_binding_key(binding)}"
        )

    @property
    def native_value(self):
        reading = self.coordinator.data["snapshot"]["roles"][self._label]["reading"]
        return numeric_value(reading) if self._numeric else reading

    @property
    def native_unit_of_measurement(self):
        return self.coordinator.data["snapshot"]["roles"][self._label]["unit"] if self._numeric else None

    @property
    def extra_state_attributes(self):
        reading = self.coordinator.data["snapshot"]["roles"][self._label]
        return {
            "source_entity_id": reading["entity_id"],
            **{key: reading[key] for key in (
                "status", "usable", "last_reported", "max_age_minutes",
                "comfort_target", "comfort_target_unit",
            ) if key in reading},
        }


class Actual(SnapshotSensor):
    """Display the active indoor reading and a summary of configured sources."""

    _attr_translation_key = "actual"
    _attr_icon = "mdi:home-thermometer-outline"
    _unrecorded_attributes = frozenset({"inputs", "pellet_stove"})

    @property
    def native_value(self) -> str:
        return "Monitoring" if self.coordinator.data["snapshot"]["active_indoor_temperature"] else "Indoor temperature unavailable"

    @property
    def extra_state_attributes(self) -> dict:
        snapshot = self.coordinator.data["snapshot"]
        indoor = snapshot["active_indoor_temperature"]
        thermostat = snapshot["roles"][ROLES["thermostat"].label]
        heat_pump = snapshot["roles"][ROLES["heat_pump_power"].label]
        return {
            "indoor_temperature": indoor["reading"] if indoor else None,
            "indoor_temperature_unit": indoor["unit"] if indoor else None,
            "indoor_source": snapshot["active_indoor_source"],
            "comfort_target": thermostat.get("comfort_target"),
            "comfort_target_unit": thermostat.get("comfort_target_unit"),
            "heat_pump_power_w": watts(heat_pump["reading"], heat_pump["unit"]) if heat_pump["usable"] else None,
            "pellet_stove": snapshot["pellet_stove"],
            "inputs": {
                label: {key: reading[key] for key in ("reading", "unit", "status", "usable")}
                for label, reading in snapshot["roles"].items() if reading["entity_id"]
            },
        }


class PelletStove(SnapshotSensor):
    """Expose the monitored stove as its own Home Assistant device."""

    _attr_translation_key = "pellet_stove"
    _attr_icon = "mdi:fireplace"
    _unrecorded_attributes = frozenset({"stock_kg", "rooms", "heat_request"})

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_device_info = _device_info(entry, "pellet_stove", "Pellet stove")

    @property
    def native_value(self):
        return self.coordinator.data["snapshot"]["pellet_stove"]["status"]

    @property
    def extra_state_attributes(self):
        return self.coordinator.data["snapshot"]["pellet_stove"]


class Proposed(SnapshotSensor):
    """Explain a heating recommendation without making device calls."""

    _attr_translation_key = "proposed"
    _attr_icon = "mdi:lightbulb-outline"

    @property
    def native_value(self) -> str:
        state, _ = self.coordinator.data["proposal"]
        return state

    @property
    def extra_state_attributes(self) -> dict:
        _, details = self.coordinator.data["proposal"]
        return {"mode": MODE, **details}



class EnergyBalance(SnapshotSensor):
    """Expose normalized measured flows and the external EV plan."""

    _attr_translation_key = "energy_balance"
    _attr_icon = "mdi:transmission-tower"
    _unrecorded_attributes = frozenset({"external_ev_schedule", "batteries", "shadow_plan"})

    @property
    def native_value(self) -> str:
        return self.coordinator.data["snapshot"]["energy"]["ev_schedule_status"]

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        return {**data["snapshot"]["energy"], "shadow_plan": data["shadow_plan"]}
