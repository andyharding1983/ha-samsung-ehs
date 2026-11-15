"""Samsung EHS heat pump integration (Modbus via MIM-B19N)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import EhsClient
from .const import (
    CONF_BAUDRATE,
    CONF_INDOOR_INDEX,
    CONF_PORT,
    CONF_SLAVE,
    DEFAULT_BAUDRATE,
    DEFAULT_INDOOR_INDEX,
)
from .coordinator import EhsCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

type EhsConfigEntry = ConfigEntry[EhsCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EhsConfigEntry) -> bool:
    """Set up Samsung EHS from a config entry."""
    client = EhsClient(
        port=entry.data[CONF_PORT],
        slave=entry.data[CONF_SLAVE],
        baudrate=entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
        indoor_index=entry.data.get(CONF_INDOOR_INDEX, DEFAULT_INDOOR_INDEX),
    )
    coordinator = EhsCoordinator(hass, entry, client)
    await coordinator.async_setup()
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: EhsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EhsConfigEntry) -> bool:
    """Unload a config entry."""
    # The coordinator's async_shutdown is called by Home Assistant on unload.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
