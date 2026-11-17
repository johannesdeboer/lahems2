"""Expose Data Status without any device actions."""

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import AREA, BATTERIES, BINDINGS, MODE
from .inputs import Inputs


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the diagnostic entity for this configuration entry."""
    async_add_entities([DataStatus(hass, entry)])


class DataStatus(SensorEntity):
    """Report the availability and usability of every configured input."""

    _attr_has_entity_name = True
    _attr_translation_key = "data_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_suggested_object_id = "lahems2_data_status"
    _unrecorded_attributes = frozenset({"roles", "active_indoor_temperature"})
    _attr_icon = "mdi:list-status"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_data_status"
        self._profile = entry.options or entry.data
        self._inputs = Inputs(hass)
        self._watched_ids = set()
        self._stop_watching = None

    async def async_added_to_hass(self) -> None:
        """Start observing entity state and registry changes."""
        await super().async_added_to_hass()
        for event in ("entity_registry_updated", "device_registry_updated", "area_registry_updated"):
            self.async_on_remove(self.hass.bus.async_listen(event, self._refresh))
        self.async_on_remove(async_track_time_interval(self.hass, self._refresh, timedelta(minutes=1)))
        self.async_on_remove(self._unwatch)
        self._refresh()

    @callback
    def _unwatch(self) -> None:
        if self._stop_watching is not None:
            self._stop_watching()
            self._stop_watching = None

    @callback
    def _refresh(self, _event=None) -> None:
        snapshot = self._inputs.snapshot(self._profile)
        entity_ids = {
            reading["entity_id"] for reading in snapshot["roles"].values() if reading["entity_id"]
        }
        if entity_ids != self._watched_ids:
            self._unwatch()
            self._watched_ids = entity_ids
            if entity_ids:
                self._stop_watching = async_track_state_change_event(self.hass, entity_ids, self._refresh)

        complete = all(
            reading["status"] in {"Available", "Not configured"}
            for reading in snapshot["roles"].values()
        )
        self._attr_native_value = "Ready" if complete else "Check inputs"
        area = self._profile.get(AREA)
        self._attr_extra_state_attributes = {
            "mode": MODE,
            "preferred_area": self._inputs.area_name(area) if area else None,
            **snapshot,
        }
        self.async_write_ha_state()
