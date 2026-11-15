"""Heat emitter type: applies sensible flow limits, curve and learning rate."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EhsConfigEntry
from .const import EMITTER_PRESETS
from .coordinator import EhsCoordinator
from .entity import EhsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([EmitterSelect(entry.runtime_data)])


class EmitterSelect(EhsEntity, SelectEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(EMITTER_PRESETS)

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "emitter_type")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        # Restore the choice only; the tuned numbers restore themselves.
        if last is not None and last.state in EMITTER_PRESETS:
            self.coordinator.emitter = last.state

    @property
    def available(self) -> bool:
        return True

    @property
    def current_option(self) -> str:
        return self.coordinator.emitter

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_emitter(option)
