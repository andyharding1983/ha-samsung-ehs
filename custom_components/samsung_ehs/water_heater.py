"""Domestic hot water for Samsung EHS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HIGH_DEMAND,
    STATE_PERFORMANCE,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EhsConfigEntry
from . import registers as R
from .coordinator import EhsCoordinator
from .entity import EhsEntity, temperature_compat

STATE_STANDARD = "standard"

DHW_TO_STATE = {
    R.DhwMode.ECO: STATE_ECO,
    R.DhwMode.STANDARD: STATE_STANDARD,
    R.DhwMode.POWER: STATE_PERFORMANCE,
    R.DhwMode.FORCE: STATE_HIGH_DEMAND,
}
STATE_TO_DHW = {v: k for k, v in DHW_TO_STATE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.hot_water:
        async_add_entities([EhsWaterHeater(entry.runtime_data)])


@temperature_compat(WaterHeaterEntity)
class EhsWaterHeater(EhsEntity, WaterHeaterEntity):
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = R.DHW_SET_MIN
    _attr_max_temp = R.DHW_SET_MAX
    _attr_target_temperature_step = 1
    _attr_operation_list = [STATE_OFF, *DHW_TO_STATE.values()]
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "hot_water")

    @property
    def native_current_temperature(self) -> float | None:
        return self.coordinator.data.dhw_temp if self.coordinator.data else None

    @property
    def native_target_temperature(self) -> float | None:
        return self.coordinator.data.dhw_set if self.coordinator.data else None

    @property
    def current_operation(self) -> str | None:
        d = self.coordinator.data
        if d is None:
            return None
        if not d.dhw_power:
            return STATE_OFF
        return DHW_TO_STATE.get(d.dhw_mode, STATE_STANDARD)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        c = self.coordinator
        attrs: dict[str, Any] = {
            "boost_until": c.dhw_boost_until.isoformat() if c.dhw_boost_active else None
        }
        if c.dhw_schedule:
            st = c.hass.states.get(c.dhw_schedule)
            attrs |= {
                "schedule": c.dhw_schedule,
                "schedule_state": st.state if st else None,
                "next_schedule_change": st.attributes.get("next_event") if st else None,
            }
        return attrs

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.coordinator.async_write(R.IU_DHW_SET, R.encode_temp(temp))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode == STATE_OFF:
            self.coordinator.clear_dhw_boost()
            await self.coordinator.async_write(R.IU_DHW_POWER, 0)
            return
        await self.coordinator.async_write(R.IU_DHW_MODE, int(STATE_TO_DHW[operation_mode]))
        if not (self.coordinator.data and self.coordinator.data.dhw_power):
            await self.coordinator.async_write(R.IU_DHW_POWER, 1)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_write(R.IU_DHW_POWER, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.clear_dhw_boost()
        await self.coordinator.async_write(R.IU_DHW_POWER, 0)
