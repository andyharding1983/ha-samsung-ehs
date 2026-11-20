"""Room thermostat for Samsung EHS space heating.

The target is the room temperature. Home Assistant runs the heat pump on
weather compensation (with forecast, room influence and an adaptive curve
offset), pauses it once the room reaches target + hysteresis and restarts it
when the room falls back to target.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
    PRESET_BOOST,
    PRESET_NONE,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import EhsConfigEntry
from .const import ROOM_TARGET_MAX, ROOM_TARGET_MIN, WC_ROOM_TARGET
from .coordinator import EhsCoordinator
from .entity import EhsEntity, temperature_compat

MIN_ROOM = ROOM_TARGET_MIN
MAX_ROOM = ROOM_TARGET_MAX


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([EhsThermostat(entry.runtime_data)])


@temperature_compat(ClimateEntity)
class EhsThermostat(EhsEntity, ClimateEntity, RestoreEntity):
    _attr_name = None  # use device name
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_min_temp = MIN_ROOM
    _attr_max_temp = MAX_ROOM
    _attr_target_temperature_step = 0.5
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = [PRESET_NONE, PRESET_BOOST]

    def __init__(self, coordinator: EhsCoordinator) -> None:
        super().__init__(coordinator, "heating")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        # Only trust a real saved mode; "unavailable"/"unknown" keeps the default.
        if last.state in (HVACMode.HEAT, HVACMode.OFF):
            self.coordinator.heating_enabled = last.state == HVACMode.HEAT
        temp = last.attributes.get(ATTR_TEMPERATURE)
        if temp is not None:
            self.coordinator.wc_params[WC_ROOM_TARGET] = min(
                max(float(temp), MIN_ROOM), MAX_ROOM
            )

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.coordinator.heating_enabled else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        c = self.coordinator
        d = c.data
        if not c.heating_enabled and not c.boost_active:
            return HVACAction.OFF
        if d is None:
            return None
        if not d.power:
            return HVACAction.IDLE  # paused: room reached target + hysteresis
        if d.defrost:
            return HVACAction.DEFROSTING
        # Heating is called for. The unit may still stop its compressor between
        # runs when it can't modulate low enough; that shows on Compressor running.
        return HVACAction.HEATING

    @property
    def native_current_temperature(self) -> float | None:
        return self.coordinator.room_temp()

    @property
    def native_target_temperature(self) -> float | None:
        return self.coordinator.room_target

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        r = c.wc_result
        return {
            "heat_demand": c.thermostat.calling,
            "flow_target": r.flow_target if r else None,
            "flow_temperature": c.data.water_out if c.data else None,
            "adaptive_offset": c.adaptive_offset,
            "boost_until": c.boost_until.isoformat() if c.boost_active else None,
        }

    @property
    def preset_mode(self) -> str:
        return PRESET_BOOST if self.coordinator.boost_active else PRESET_NONE

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_BOOST:
            await self.coordinator.async_start_boost()
        else:
            await self.coordinator.async_cancel_boost()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.coordinator.async_set_room_target(float(temp))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_set_heating(hvac_mode == HVACMode.HEAT)

    async def async_turn_on(self) -> None:
        await self.coordinator.async_set_heating(True)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set_heating(False)
