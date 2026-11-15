"""Binary sensors for Samsung EHS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EhsConfigEntry
from .coordinator import EhsCoordinator
from .entity import EhsEntity
from .registers import EhsData


@dataclass(frozen=True, kw_only=True)
class EhsBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[EhsData], bool]


BINARY_SENSORS: tuple[EhsBinaryDescription, ...] = (
    EhsBinaryDescription(
        key="defrost",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.defrost,
    ),
    EhsBinaryDescription(
        key="compressor_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: bool((d.extended or {}).get("compressor_freq")),
    ),
    EhsBinaryDescription(
        key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.outdoor_error or d.indoor_error or d.module_error),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(EhsBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class EhsBinarySensor(EhsEntity, BinarySensorEntity):
    entity_description: EhsBinaryDescription

    def __init__(self, coordinator: EhsCoordinator, description: EhsBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
