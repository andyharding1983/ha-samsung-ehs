"""Base entity for Samsung EHS."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EhsCoordinator


class EhsEntity(CoordinatorEntity[EhsCoordinator]):
    """Common device info and unique-id handling."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EhsCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model="EHS heat pump (MIM-B19N)",
            name="Samsung EHS",
        )


def temperature_compat(base: type):
    """Class decorator: map the 2026.11+ native_* temperature API onto the
    legacy property names when running on an older Home Assistant."""

    def deco(cls):
        if not hasattr(base, "native_target_temperature"):
            cls.temperature_unit = property(lambda s: s._attr_native_temperature_unit)
            cls.current_temperature = property(lambda s: s.native_current_temperature)
            cls.target_temperature = property(lambda s: s.native_target_temperature)
        return cls

    return deco
