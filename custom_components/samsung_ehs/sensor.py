"""Sensors for Samsung EHS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EhsConfigEntry
from .coordinator import EhsCoordinator
from .entity import EhsEntity
from .weather_comp import curve_points


@dataclass(frozen=True, kw_only=True)
class EhsSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EhsCoordinator], Any]
    attrs_fn: Callable[[EhsCoordinator], dict[str, Any] | None] | None = None


def _ext(key: str) -> Callable[[EhsCoordinator], Any]:
    return lambda c: (c.data.extended or {}).get(key) if c.data else None


TEMP = {
    "device_class": SensorDeviceClass.TEMPERATURE,
    "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
    "state_class": SensorStateClass.MEASUREMENT,
}


def _wc_attrs(c: EhsCoordinator) -> dict[str, Any] | None:
    r = c.wc_result
    if r is None:
        return None
    return {
        "base_flow": round(r.curve_flow - r.forecast_offset, 2),
        "curve_flow": r.curve_flow,
        "adaptive_offset": r.curve_offset,
        "room_offset": r.room_offset,
        "wind_offset": r.wind_offset,
        "boost_offset": round(c.boost_flow - r.flow_target, 2) if c.boost_active else 0.0,
        "forecast_offset": r.forecast_offset,
        "wind_speed": r.wind_speed,
        "effective_outdoor": r.effective_outdoor,
        "forecast_mean": r.forecast_mean,
        "limited": r.limited,
        "heating_needed": r.heating_needed,
        "heat_demand": c.thermostat.calling,
        "last_write": c.wc_last_write.isoformat() if c.wc_last_write else None,
        "boost_until": c.boost_until.isoformat() if c.boost_active else None,
    }


HOT_WATER_SENSORS = {"dhw_temp"}

def _adaptive_attrs(c: EhsCoordinator) -> dict[str, Any]:
    a, p = c.adaptive, c.wc_params
    return {
        "design_end_offset": round(a.design, 2),
        "mild_end_offset": round(a.mild, 2),
        "learned_flow_at_design": round(p["wc_flow_design"] + a.design, 1),
        "learned_flow_at_mild": round(p["wc_flow_mild"] + a.mild, 1),
        "design_weight": round(c.wc_result.design_weight, 2) if c.wc_result else None,
        "curve": curve_points(c.params(), a.design, a.mild),
    }


def _learned_end(
    end: str,
) -> tuple[Callable[[EhsCoordinator], Any], Callable[[EhsCoordinator], dict[str, Any]]]:
    flow_key, out_key = (
        ("wc_flow_design", "wc_outdoor_design")
        if end == "design"
        else ("wc_flow_mild", "wc_outdoor_mild")
    )

    def value(c: EhsCoordinator) -> float:
        return round(c.wc_params[flow_key] + getattr(c.adaptive, end), 1)

    def attrs(c: EhsCoordinator) -> dict[str, Any]:
        return {
            "outdoor_temperature": c.wc_params[out_key],
            "set_flow": c.wc_params[flow_key],
            "learned_offset": round(getattr(c.adaptive, end), 2),
        }

    return value, attrs


SENSORS: tuple[EhsSensorDescription, ...] = (
    EhsSensorDescription(key="flow_temp", **TEMP, value_fn=lambda c: c.data.water_out),
    EhsSensorDescription(key="return_temp", **TEMP, value_fn=lambda c: c.data.water_in),
    EhsSensorDescription(
        key="flow_setpoint",
        **TEMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.water_out_set,
    ),
    EhsSensorDescription(
        key="delta_t",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda c: c.data.delta_t,
    ),
    EhsSensorDescription(key="dhw_temp", **TEMP, value_fn=lambda c: c.data.dhw_temp),
    EhsSensorDescription(
        key="unit_room_temp",
        **TEMP,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.data.room_temp,
    ),
    EhsSensorDescription(key="outdoor_temp", **TEMP, value_fn=_ext("outdoor_temp")),
    EhsSensorDescription(
        key="flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_ext("flow_rate"),
    ),
    EhsSensorDescription(
        key="compressor_freq",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_ext("compressor_freq"),
    ),
    EhsSensorDescription(
        key="heat_output",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.heat_w,
    ),
    EhsSensorDescription(
        key="electrical_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.power_w,
    ),
    EhsSensorDescription(
        key="cop",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda c: c.cop,
    ),
    EhsSensorDescription(
        key="cop_today",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda c: c.energy.cop_today,
    ),
    EhsSensorDescription(
        key="scop",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda c: c.energy.cop_lifetime,
    ),
    EhsSensorDescription(
        key="heat_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda c: round(c.energy.heat_kwh, 3),
    ),
    EhsSensorDescription(
        key="heat_energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda c: round(c.energy.heat_today_kwh, 3),
    ),
    EhsSensorDescription(
        key="electrical_energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda c: round(c.energy.elec_today_kwh, 3),
    ),
    EhsSensorDescription(
        key="wc_target",
        **TEMP,
        value_fn=lambda c: c.commanded_flow,
        attrs_fn=_wc_attrs,
    ),
    EhsSensorDescription(
        key="adaptive_offset",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda c: c.adaptive_offset,
        attrs_fn=_adaptive_attrs,
    ),
    EhsSensorDescription(
        key="learned_flow_design",
        **TEMP,
        suggested_display_precision=1,
        value_fn=_learned_end("design")[0],
        attrs_fn=_learned_end("design")[1],
    ),
    EhsSensorDescription(
        key="learned_flow_mild",
        **TEMP,
        suggested_display_precision=1,
        value_fn=_learned_end("mild")[0],
        attrs_fn=_learned_end("mild")[1],
    ),
    EhsSensorDescription(
        key="outdoor_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.outdoor_error,
    ),
    EhsSensorDescription(
        key="indoor_error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.indoor_error,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EhsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EhsSensor(coordinator, d)
        for d in SENSORS
        if coordinator.hot_water or d.key not in HOT_WATER_SENSORS
    )


class EhsSensor(EhsEntity, SensorEntity):
    entity_description: EhsSensorDescription
    # The sampled curve is for live plotting only; keep it out of the database.
    _unrecorded_attributes = frozenset({"curve"})

    def __init__(self, coordinator: EhsCoordinator, description: EhsSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator) if fn else None
