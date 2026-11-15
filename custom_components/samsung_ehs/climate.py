"""Climate entity (space heating/cooling) for Samsung EHS.

With weather compensation enabled, the target temperature is the *room*
target that feeds the room-influence term. With it disabled, the target is
the flow-water setpoint written straight to the unit.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EhsConfigEntry
from . import registers as R
from .const import WC_ROOM_TARGET
from .coordinator import EhsCoordinator
from .entity import EhsEntity, temperature_compat

MODE_TO_HVAC = {
    R.Mode.AUTO: HVACMode.AUTO,
    R.Mode.COOL: HVACMode.COOL,
    R.Mode.HEAT: HVACMode.HEAT,
}
HVAC_TO_MODE = {v: k for k, v in MODE_TO_HVAC.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([EhsClimate(entry.runtime_data)])


@temperature_compat(ClimateEntity)
class EhsClimate(EhsEntity, ClimateEntity):
    _attr_name = None  # use device name
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "heating")

    @property
    def _room_control(self) -> bool:
        return self.coordinator.wc_enabled

    @property
    def target_temperature_step(self) -> float:
        return 0.5

    @property
    def min_temp(self) -> float:
        return 10.0 if self._room_control else R.FLOW_SET_MIN

    @property
    def max_temp(self) -> float:
        return 28.0 if self._room_control else R.FLOW_SET_MAX

    @property
    def hvac_mode(self) -> HVACMode | None:
        d = self.coordinator.data
        if d is None:
            return None
        if not d.power:
            return HVACMode.OFF
        return MODE_TO_HVAC.get(d.mode, HVACMode.HEAT)

    @property
    def hvac_action(self) -> HVACAction | None:
        d = self.coordinator.data
        if d is None:
            return None
        if not d.power:
            return HVACAction.OFF
        if d.defrost:
            return HVACAction.DEFROSTING
        running = bool((d.extended or {}).get("compressor_freq"))
        if not running:
            return HVACAction.IDLE
        return HVACAction.COOLING if d.mode == R.Mode.COOL else HVACAction.HEATING

    @property
    def native_current_temperature(self) -> float | None:
        if self._room_control:
            return self.coordinator.room_temp()
        return self.coordinator.data.water_out if self.coordinator.data else None

    @property
    def native_target_temperature(self) -> float | None:
        if self._room_control:
            return self.coordinator.wc_params[WC_ROOM_TARGET]
        return self.coordinator.data.water_out_set if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "control": "room (weather compensation)" if self._room_control else "flow",
            "flow_temperature": self.coordinator.data.water_out
            if self.coordinator.data
            else None,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        if self._room_control:
            await self.coordinator.async_set_wc_param(WC_ROOM_TARGET, float(temp))
        else:
            await self.coordinator.async_write(R.IU_WATER_OUT_SET, R.encode_temp(temp))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_write(R.IU_POWER, 0)
            return
        await self.coordinator.async_write(R.IU_MODE, int(HVAC_TO_MODE[hvac_mode]))
        if not (self.coordinator.data and self.coordinator.data.power):
            await self.coordinator.async_write(R.IU_POWER, 1)
        await self.coordinator.async_run_wc()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
