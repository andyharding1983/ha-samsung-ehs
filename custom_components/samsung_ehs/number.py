"""Weather-compensation tuning parameters as number entities."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EhsConfigEntry
from .const import (
    WC_DEFAULTS,
    WC_FLOW_DESIGN,
    WC_FLOW_MAX,
    WC_FLOW_MILD,
    WC_FLOW_MIN,
    WC_FORECAST_HOURS,
    WC_FORECAST_WEIGHT,
    WC_HEATING_CUTOFF,
    WC_OUTDOOR_DESIGN,
    WC_OUTDOOR_MILD,
    WC_ADAPT_LIMIT,
    WC_ADAPT_RATE,
    WC_HYSTERESIS,
    WC_RESTART_DROP,
    WC_MIN_OFF,
    WC_MIN_ON,
    WC_ROOM_GAIN,
    WC_ROOM_MAX_OFFSET,
    WC_WIND_GAIN,
    WC_WIND_THRESHOLD,
)
from .coordinator import EhsCoordinator
from .entity import EhsEntity


@dataclass(frozen=True, kw_only=True)
class WcNumberDescription(NumberEntityDescription):
    pass


def _t(key: str, lo: float, hi: float, step: float = 0.5):
    return WcNumberDescription(
        key=key,
        native_min_value=lo,
        native_max_value=hi,
        native_step=step,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


NUMBERS: tuple[WcNumberDescription, ...] = (
    WcNumberDescription(
        key=WC_WIND_GAIN,
        native_min_value=0,
        native_max_value=1,
        native_step=0.05,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_WIND_THRESHOLD,
        native_min_value=0,
        native_max_value=20,
        native_step=0.5,
        native_unit_of_measurement="m/s",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    _t(WC_OUTDOOR_DESIGN, -20, 5),
    _t(WC_FLOW_DESIGN, 25, 55),
    _t(WC_OUTDOOR_MILD, 5, 20),
    _t(WC_FLOW_MILD, 15, 40),
    _t(WC_FLOW_MIN, 15, 40),
    _t(WC_FLOW_MAX, 25, 55),
    _t(WC_HEATING_CUTOFF, 10, 25),
    WcNumberDescription(
        key=WC_ROOM_GAIN,
        native_min_value=0,
        native_max_value=10,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_ROOM_MAX_OFFSET,
        native_min_value=0,
        native_max_value=15,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_FORECAST_HOURS,
        native_min_value=0,
        native_max_value=24,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.HOURS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_FORECAST_WEIGHT,
        native_min_value=0,
        native_max_value=1,
        native_step=0.05,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_HYSTERESIS,
        native_min_value=0.2,
        native_max_value=3,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_RESTART_DROP,
        native_min_value=0.1,
        native_max_value=3,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_MIN_ON,
        native_min_value=0,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_MIN_OFF,
        native_min_value=0,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_ADAPT_RATE,
        native_min_value=0,
        native_max_value=2,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    WcNumberDescription(
        key=WC_ADAPT_LIMIT,
        native_min_value=0,
        native_max_value=10,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    async_add_entities(WcNumber(c, d) for d in NUMBERS)


class WcNumber(EhsEntity, RestoreNumber):
    entity_description: WcNumberDescription

    def __init__(self, coordinator: EhsCoordinator, description: WcNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.wc_params[self.entity_description.key] = last.native_value

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float:
        key = self.entity_description.key
        return self.coordinator.wc_params.get(key, WC_DEFAULTS[key])

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_wc_param(self.entity_description.key, value)
