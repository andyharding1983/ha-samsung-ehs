"""Buttons: fold the learned curve into the curve points, or reset learning."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EhsConfigEntry
from .coordinator import EhsCoordinator
from .entity import EhsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    async_add_entities([ApplyLearnedCurve(c), ResetLearning(c)])


class ApplyLearnedCurve(EhsEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "apply_learned_curve")

    async def async_press(self) -> None:
        await self.coordinator.async_apply_learned_curve()


class ResetLearning(EhsEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "reset_learning")

    async def async_press(self) -> None:
        await self.coordinator.async_reset_learning()
