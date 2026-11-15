"""End-to-end tests against a real Home Assistant core with a mocked bus."""

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
    await _setup(hass, {"power_sensor": "sensor.hp_power"})

    assert hass.states.get("sensor.samsung_ehs_flow_temperature").state == "35.0"
    assert hass.states.get("sensor.samsung_ehs_outdoor_temperature").state == "5.0"
    # 15 L/min * 5 K -> 5225 W, / 1500 W
    assert float(hass.states.get("sensor.samsung_ehs_heat_output").state) == pytest.approx(5225)
    assert float(hass.states.get("sensor.samsung_ehs_cop").state) == pytest.approx(3.48, abs=0.01)

    climate = hass.states.get("climate.samsung_ehs")
    assert climate.state == "heat"
    assert climate.attributes["hvac_action"] == "heating"
    assert climate.attributes["temperature"] == 35.0  # flow control while WC is off

    dhw = hass.states.get("water_heater.samsung_ehs_hot_water")
    assert dhw.state == "standard"
    assert dhw.attributes["current_temperature"] == 45.5


async def test_controls_write_registers(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "climate", "set_temperature",
        {"entity_id": "climate.samsung_ehs", "temperature": 40}, blocking=True,
    )
    client.write_indoor.assert_any_await(R.IU_WATER_OUT_SET, 400)

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

    await hass.services.async_call(
        "climate", "set_hvac_mode",
        {"entity_id": "climate.samsung_ehs", "hvac_mode": "off"}, blocking=True,
    )
    client.write_indoor.assert_any_await(R.IU_POWER, 0)


async def test_weather_compensation_writes_flow(hass: HomeAssistant, client) -> None:
    hass.states.async_set("sensor.room", "20.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    client.write_indoor.reset_mock()

    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.samsung_ehs_weather_compensation"}, blocking=True,
    )
    # outdoor 5 C on default curve (-3->45, 15->25): 36.1, + room 3*(21-20)=3 -> 39.0
    client.write_indoor.assert_any_await(R.IU_WATER_OUT_SET, 390)
    wc = hass.states.get("sensor.samsung_ehs_weather_compensation_target")
    assert float(wc.state) == 39.0
    assert wc.attributes["room_offset"] == 3.0
    assert wc.attributes["last_write"] is not None

    climate = hass.states.get("climate.samsung_ehs")
    assert climate.attributes["temperature"] == 21.0  # now the room target
    assert climate.attributes["current_temperature"] == 20.0


async def test_wc_skips_when_heating_off(hass: HomeAssistant, client) -> None:
    client.read_all.return_value = _snapshot(power=False)
    await _setup(hass)
    client.write_indoor.reset_mock()
    await hass.services.async_call(
        "switch", "turn_on",
        {"entity_id": "switch.samsung_ehs_weather_compensation"}, blocking=True,
    )
    client.write_indoor.assert_not_awaited()


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
    wc = hass.states.get("sensor.samsung_ehs_weather_compensation_target")
    assert wc.state not in ("unknown", "unavailable")
    assert wc.attributes["effective_outdoor"] == 4.2


async def test_implausible_room_temp_ignored(hass: HomeAssistant, client) -> None:
    # No room sensor configured and unit reports 0.0 C (no Samsung sensor)
    client.read_all.return_value = _snapshot(room_temp=0.0)
    await _setup(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert coordinator.room_temp() is None
    await coordinator.async_run_wc()
    assert coordinator.wc_result.room_offset == 0.0


async def test_diagnostics(hass: HomeAssistant, client) -> None:
    from custom_components.samsung_ehs.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["data"]["water_out"] == 35.0
    assert diag["weather_compensation"]["outdoor_used"] == 5.0
