"""End-to-end tests against a real Home Assistant core with a mocked bus."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsung_ehs import registers as R
from custom_components.samsung_ehs.api import EhsConnectionError
from custom_components.samsung_ehs.const import DOMAIN

DATA = {"port": "/dev/ttyUSB0", "slave": 1, "baudrate": 9600, "indoor_index": 0}


def _snapshot(**kw) -> R.EhsData:
    base = dict(
        power=True, mode=R.Mode.HEAT, water_in=30.0, water_out=35.0,
        water_out_set=35.0, dhw_power=True, dhw_mode=R.DhwMode.STANDARD,
        dhw_set=48.0, dhw_temp=45.5, room_temp=20.0,
        extended={"outdoor_temp": 5.0, "compressor_freq": 40, "flow_rate": 15.0},
    )
    base.update(kw)
    return R.EhsData(**base)


@pytest.fixture
def client():
    c = MagicMock()
    c.read_all = AsyncMock(return_value=_snapshot())
    c.write_indoor = AsyncMock()
    c.write = AsyncMock()
    c.close = MagicMock()
    with patch("custom_components.samsung_ehs.EhsClient", return_value=c), patch(
        "custom_components.samsung_ehs.config_flow.EhsClient", return_value=c
    ):
        yield c


async def _setup(hass: HomeAssistant, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA


async def test_config_flow_cannot_connect(hass: HomeAssistant, client) -> None:
    client.read_all.side_effect = EhsConnectionError("nope")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_entities(hass: HomeAssistant, client) -> None:
    hass.states.async_set("sensor.hp_power", "1500", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.room", "20.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"power_sensor": "sensor.hp_power", "room_sensor": "sensor.room"})

    assert hass.states.get("sensor.samsung_ehs_flow_temperature").state == "35.0"
    assert hass.states.get("sensor.samsung_ehs_outdoor_temperature").state == "5.0"
    # 15 L/min * 5 K -> 5225 W, / 1500 W
    assert float(hass.states.get("sensor.samsung_ehs_heat_output").state) == pytest.approx(5225)
    assert float(hass.states.get("sensor.samsung_ehs_cop").state) == pytest.approx(3.48, abs=0.01)

    climate = hass.states.get("climate.samsung_ehs")
    assert climate.state == "heat"
    assert climate.attributes["hvac_modes"] == ["off", "heat"]
    assert climate.attributes["hvac_action"] == "heating"
    assert climate.attributes["temperature"] == 21.0  # room target
    assert climate.attributes["current_temperature"] == 20.0

    dhw = hass.states.get("water_heater.samsung_ehs_hot_water")
    assert dhw.state == "standard"
    assert dhw.attributes["current_temperature"] == 45.5
    assert hass.states.get("switch.samsung_ehs_weather_compensation") is None


async def test_weather_compensation_always_on(hass: HomeAssistant, client) -> None:
    hass.states.async_set("sensor.room", "20.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    # outdoor 5 C on UFH default curve (-3->40, 15->25): 33.3, + room 3*(21-20)=3 -> 36.5
    client.write_indoor.assert_any_await(R.IU_WATER_OUT_SET, 365)
    wc = hass.states.get("sensor.samsung_ehs_flow_target")
    assert float(wc.state) == 36.5
    assert wc.attributes["room_offset"] == 3.0
    assert wc.attributes["last_write"] is not None


async def test_target_change_rewrites_flow(hass: HomeAssistant, client) -> None:
    hass.states.async_set("sensor.room", "20.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    await hass.services.async_call(
        "climate", "set_temperature",
        {"entity_id": "climate.samsung_ehs", "temperature": 21.5}, blocking=True,
    )
    # room offset now 3*1.5=4.5 -> 33.3+4.5 = 37.8 -> 38.0
    client.write_indoor.assert_any_await(R.IU_WATER_OUT_SET, 380)
    assert hass.states.get("climate.samsung_ehs").attributes["temperature"] == 21.5


async def test_overshoot_pauses_after_min_on(hass: HomeAssistant, client) -> None:
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from unittest.mock import patch as _patch
    import time as _time

    hass.states.async_set("sensor.room", "22.2", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    # Just started: min on time (20 min) protects the compressor
    assert not any(c.args == (R.IU_POWER, 0) for c in client.write_indoor.await_args_list)

    later = _time.monotonic() + 21 * 60
    with _patch("custom_components.samsung_ehs.coordinator.time.monotonic", return_value=later):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done()
    client.write_indoor.assert_any_await(R.IU_POWER, 0)
    climate = hass.states.get("climate.samsung_ehs")
    assert climate.state == "heat"
    assert climate.attributes["hvac_action"] == "idle"
    assert hass.states.get("binary_sensor.samsung_ehs_heat_demand").state == "off"


async def test_paused_unit_restarts_at_target(hass: HomeAssistant, client) -> None:
    client.read_all.return_value = _snapshot(power=False)
    hass.states.async_set("sensor.room", "21.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    client.write_indoor.assert_any_await(R.IU_POWER, 1)


async def test_user_off_and_on(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "climate", "set_hvac_mode",
        {"entity_id": "climate.samsung_ehs", "hvac_mode": "off"}, blocking=True,
    )
    client.write_indoor.assert_any_await(R.IU_POWER, 0)  # overrides min on time
    assert hass.states.get("climate.samsung_ehs").attributes["hvac_action"] == "off"


async def test_controls_write_registers(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "water_heater", "set_operation_mode",
        {"entity_id": "water_heater.samsung_ehs_hot_water", "operation_mode": "eco"},
        blocking=True,
    )
    client.write_indoor.assert_any_await(R.IU_DHW_MODE, int(R.DhwMode.ECO))
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.samsung_ehs_quiet_mode"}, blocking=True
    )
    client.write_indoor.assert_any_await(R.IU_QUIET, 1)


async def test_unload(hass: HomeAssistant, client) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    client.close.assert_called_once()


async def test_simulator_entry(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "cloudy", {"temperature": 4.2})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**DATA, "port": "simulator"},
        options={"weather_entity": "weather.home"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.samsung_ehs_outdoor_temperature").state == "4.2"
    assert hass.states.get("climate.samsung_ehs").state == "heat"
    # WC preview is calculated at startup even while disabled
    wc = hass.states.get("sensor.samsung_ehs_flow_target")
    assert wc.state not in ("unknown", "unavailable")
    assert wc.attributes["effective_outdoor"] == 4.2


async def test_implausible_room_temp_ignored(hass: HomeAssistant, client) -> None:
    # No room sensor configured and unit reports 0.0 C (no Samsung sensor)
    client.read_all.return_value = _snapshot(room_temp=0.0)
    await _setup(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert coordinator.room_temp() is None
    await coordinator.async_control(force_flow=True)
    assert coordinator.wc_result.room_offset == 0.0


async def test_diagnostics(hass: HomeAssistant, client) -> None:
    from custom_components.samsung_ehs.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["data"]["water_out"] == 35.0
    assert diag["weather_compensation"]["outdoor_used"] == 5.0


async def test_emitter_select_applies_preset(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    sel = hass.states.get("select.samsung_ehs_heat_emitter_type")
    assert sel.state == "underfloor"
    assert hass.states.get("number.samsung_ehs_maximum_flow_temperature").state == "45.0"
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": "select.samsung_ehs_heat_emitter_type", "option": "radiators"},
        blocking=True,
    )
    assert hass.states.get("number.samsung_ehs_maximum_flow_temperature").state == "55.0"
    assert hass.states.get("number.samsung_ehs_minimum_flow_temperature").state == "30.0"
    assert hass.states.get("number.samsung_ehs_adaptive_learning_rate").state == "0.75"
    # radiator curve at 5 C outdoor: 50 + (30-50)/18*8 = 41.1, + unit room 20.0 -> +3 = 44.0
    client.write_indoor.assert_any_await(R.IU_WATER_OUT_SET, 440)


async def test_no_learning_during_hot_water(hass: HomeAssistant, client) -> None:
    ext = {"outdoor_temp": 5.0, "compressor_freq": 40, "flow_rate": 15.0, "dhw_valve": 1}
    client.read_all.return_value = _snapshot(extended=ext)
    hass.states.async_set("sensor.room", "19.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    c._last_control -= 1800  # pretend half an hour passed
    await c.async_control()
    assert (c.adaptive.design, c.adaptive.mild) == (0.0, 0.0)


async def test_heating_only_hides_hot_water(hass: HomeAssistant, client) -> None:
    from homeassistant.helpers import entity_registry as er

    entry = await _setup(hass)
    assert hass.states.get("water_heater.samsung_ehs_hot_water") is not None
    hass.config_entries.async_update_entry(entry, options={"hot_water": False})
    await hass.async_block_till_done()  # update listener reloads the entry
    reg = er.async_get(hass)
    assert hass.states.get("water_heater.samsung_ehs_hot_water") is None
    assert reg.async_get("water_heater.samsung_ehs_hot_water") is None
    assert reg.async_get("sensor.samsung_ehs_hot_water_temperature") is None
    assert hass.states.get("climate.samsung_ehs") is not None


async def test_config_flow_heating_only(hass: HomeAssistant, client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "hot_water": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    assert result["options"] == {"hot_water": False}


async def test_curve_learning_and_apply(hass: HomeAssistant, client) -> None:
    hass.states.async_set("sensor.room", "20.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    c._last_control -= 1800  # half an hour 1 K cold at 5 C outdoor
    await c.async_control()
    # 5 C on -3..15: design weight 10/18; learning split between both ends
    assert c.adaptive.design > c.adaptive.mild > 0
    applied_offset = c.wc_result.curve_offset
    assert applied_offset == pytest.approx(0.125, abs=0.01)  # rate 0.25 * 1 K * 0.5 h

    sensor = hass.states.get("sensor.samsung_ehs_learned_curve_offset")
    assert sensor.attributes["learned_flow_at_design"] > 40.0

    d, m = c.adaptive.design, c.adaptive.mild
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.samsung_ehs_apply_learned_curve"}, blocking=True,
    )
    assert c.adaptive.design == pytest.approx(0.0, abs=1e-3)
    assert c.adaptive.mild == pytest.approx(0.0, abs=1e-3)
    assert hass.states.get(
        "number.samsung_ehs_curve_flow_at_design_temperature"
    ).state == str(round(40.0 + d, 1))
    assert hass.states.get(
        "number.samsung_ehs_curve_flow_at_mild_temperature"
    ).state == str(round(25.0 + m, 1))
