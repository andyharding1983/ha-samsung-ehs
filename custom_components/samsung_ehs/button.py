"""Buttons: boost heating, and fold in or reset the learned curve."""

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
    async_add_entities(
        [BoostHeating(c), CancelBoost(c), ApplyLearnedCurve(c), ResetLearning(c)]
    )


class BoostHeating(EhsEntity, ButtonEntity):
    """Maximum flow for an hour, whatever the thermostat says."""

    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "boost")

    async def async_press(self) -> None:
        await self.coordinator.async_start_boost()


class CancelBoost(EhsEntity, ButtonEntity):
    _attr_icon = "mdi:fire-off"

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "cancel_boost")

    async def async_press(self) -> None:
        await self.coordinator.async_cancel_boost()


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
