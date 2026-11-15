"""Switches for Samsung EHS: quiet, away and weather compensation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EhsConfigEntry
from . import registers as R
from .coordinator import EhsCoordinator
from .entity import EhsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    async_add_entities(
        [
            EhsRegisterSwitch(c, "quiet", R.IU_QUIET, lambda d: d.quiet),
            EhsRegisterSwitch(c, "away", R.IU_AWAY, lambda d: d.away),
            WeatherCompSwitch(c),
        ]
    )


class EhsRegisterSwitch(EhsEntity, SwitchEntity):
    """A 0/1 register on the indoor unit."""

    def __init__(self, coordinator: EhsCoordinator, key: str, offset: int, fn) -> None:
        super().__init__(coordinator, key)
        self._offset = offset
        self._fn = fn

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.data
        return self._fn(d) if d else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(self._offset, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(self._offset, 0)


class WeatherCompSwitch(EhsEntity, SwitchEntity, RestoreEntity):
    """Hands flow-temperature control to Home Assistant."""

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "weather_compensation")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self.coordinator.wc_enabled = last.state == STATE_ON

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.wc_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_wc_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_wc_enabled(False)
