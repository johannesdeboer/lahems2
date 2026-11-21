"""Expose read-only data status, measurements and heating proposals."""

import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import AREA, MODE, ROLES, SETTINGS
from .inputs import Inputs
from .planning import propose


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the read-only entities for this configuration entry."""
    coordinator = SnapshotCoordinator(hass, entry)
    coordinator.start()
    async_add_entities([
        DataStatus(coordinator, entry),
        Actual(coordinator, entry),
        Proposed(coordinator, entry),
    ])


class SnapshotCoordinator(DataUpdateCoordinator[dict]):
    """Refresh a shared snapshot when inputs change or become stale."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, logging.getLogger(__name__), name="LAHEMS2", config_entry=entry)
        self.profile = entry.options or entry.data
        self._entry = entry
        self._inputs = Inputs(hass)
        self._watched_ids = set()
        self._stop_watching = None

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
        self.async_set_updated_data({
            "preferred_area": self._inputs.area_name(area) if area else None,
            "snapshot": snapshot,
            "proposal": proposal,
        })


class SnapshotSensor(CoordinatorEntity[SnapshotCoordinator], SensorEntity):
    """A read-only view of the same current input snapshot."""

    _attr_has_entity_name = True

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
        return {"mode": MODE, "preferred_area": data["preferred_area"], **data["snapshot"]}


class Actual(SnapshotSensor):
    """Display the active indoor reading and a summary of configured sources."""

    _attr_translation_key = "actual"
    _attr_icon = "mdi:home-thermometer-outline"
    _unrecorded_attributes = frozenset({"inputs"})

    @property
    def native_value(self) -> str:
        return "Monitoring" if self.coordinator.data["snapshot"]["active_indoor_temperature"] else "Indoor temperature unavailable"

    @property
    def extra_state_attributes(self) -> dict:
        snapshot = self.coordinator.data["snapshot"]
        indoor = snapshot["active_indoor_temperature"]
        thermostat = snapshot["roles"][ROLES["thermostat"].label]
        return {
            "indoor_temperature": indoor["reading"] if indoor else None,
            "indoor_temperature_unit": indoor["unit"] if indoor else None,
            "indoor_source": snapshot["active_indoor_source"],
            "comfort_target": thermostat.get("comfort_target"),
            "comfort_target_unit": thermostat.get("comfort_target_unit"),
            "inputs": {
                label: {key: reading[key] for key in ("reading", "unit", "status", "usable")}
                for label, reading in snapshot["roles"].items() if reading["entity_id"]
            },
        }


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
