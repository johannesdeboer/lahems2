"""LAHEMS2 Shadow Mode integration."""

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

_PLATFORMS = (Platform.SENSOR,)
_PANEL = "lahems2-overview"
_MODULE = "/lahems2-assets/dashboard.js"


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Serve the bundled frontend module once per Home Assistant process."""
    await hass.http.async_register_static_paths([
        StaticPathConfig(_MODULE, str(Path(__file__).parent / "dashboard.js"), False),
    ])
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up read-only entities and register the dashboard in the sidebar."""
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=_PANEL,
        webcomponent_name="lahems2-overview-panel",
        sidebar_title="LAHEMS2",
        sidebar_icon="mdi:home-lightning-bolt-outline",
        module_url=_MODULE,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the read-only sensors and remove the sidebar panel."""
    if not await hass.config_entries.async_unload_platforms(entry, _PLATFORMS):
        return False
    frontend.async_remove_panel(hass, _PANEL)
    return True
