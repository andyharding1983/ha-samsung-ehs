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
    c.dump = AsyncMock(return_value={"registers": {5: 50, 6: 0xFFEC}, "errors": {}})
    c.close = MagicMock()
    with patch("custom_components.samsung_ehs.EhsClient", return_value=c), patch(
        "custom_components.samsung_ehs.config_flow.EhsClient", return_value=c
    ), patch(  # keep tests independent of any real USB adapter on this machine
        "custom_components.samsung_ehs.config_flow.BY_ID_DIR", "/nonexistent"
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
    assert hass.states.get("switch.samsung_ehs_away_mode") is None
    assert climate.attributes["preset_modes"] == ["none", "boost"]


async def test_weather_compensation_always_on(hass: HomeAssistant, client) -> None:
    hass.states.async_set("sensor.room", "20.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    # outdoor 5 C on UFH default curve (-3->40, 15->25): 33.3, + room 3*(21-20)=3 -> 36.5
    client.write_indoor.assert_any_await(R.IU_WATER_OUT_SET, 365)
    wc = hass.states.get("sensor.samsung_ehs_flow_target")
    assert float(wc.state) == 36.5
    assert wc.attributes["room_offset"] == 3.0
    assert wc.attributes["base_flow"] == round(
        wc.attributes["curve_flow"] - wc.attributes["forecast_offset"], 2
    )
    assert wc.attributes["limited"] == 0
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
    ext = diag["extended_registers"]
    assert ext["requested"]["6000"] == ["0x8204", "0x8238"]
    assert ext["raw_dump"]["registers"]["6"] == {"raw": 0xFFEC, "x0.1": -2.0}


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
    assert c.adaptive.design == pytest.approx(0, abs=0.01)
    assert c.adaptive.mild == pytest.approx(0, abs=0.01)


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
    curve = sensor.attributes["curve"]
    assert curve[0]["outdoor"] == -13.0 and curve[-1]["outdoor"] == 17.0
    assert all(pt["learned"] >= pt["set"] for pt in curve)
    cold = hass.states.get("sensor.samsung_ehs_learned_flow_at_cold_end")
    mild = hass.states.get("sensor.samsung_ehs_learned_flow_at_mild_end")
    assert float(cold.state) == sensor.attributes["learned_flow_at_design"]
    assert float(mild.state) == sensor.attributes["learned_flow_at_mild"]
    assert cold.attributes["outdoor_temperature"] == -3.0

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


async def test_wind_boost_from_weather(hass: HomeAssistant, client) -> None:
    hass.states.async_set("sensor.room", "21.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(
        "weather.home", "windy",
        {"temperature": 5.0, "wind_speed": 54.0, "wind_speed_unit": "km/h"},
    )
    await _setup(hass, {"room_sensor": "sensor.room", "weather_entity": "weather.home"})
    wc = hass.states.get("sensor.samsung_ehs_flow_target")
    # 54 km/h = 15 m/s; 0.3 * (15 - 5) = 3 K (the cap)
    assert wc.attributes["wind_speed"] == 15.0
    assert wc.attributes["wind_offset"] == 3.0


async def test_boost(hass: HomeAssistant, client) -> None:
    from homeassistant.util import dt as dt_util
    from unittest.mock import patch as _patch

    hass.states.async_set("sensor.room", "23.0", {"unit_of_measurement": "°C"})
    await _setup(hass, {"room_sensor": "sensor.room"})
    await hass.services.async_call(
        "climate", "set_hvac_mode",
        {"entity_id": "climate.samsung_ehs", "hvac_mode": "off"}, blocking=True,
    )
    client.write_indoor.reset_mock()
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.samsung_ehs_boost_heating"}, blocking=True
    )
    # Boost overrides both "off" and a warm room: on, at max flow (45)
    client.write_indoor.assert_any_await(R.IU_POWER, 1)
    client.write_indoor.assert_any_await(R.IU_WATER_OUT_SET, 450)
    climate = hass.states.get("climate.samsung_ehs")
    assert climate.attributes["preset_mode"] == "boost"
    assert climate.attributes["boost_until"] is not None
    wc = hass.states.get("sensor.samsung_ehs_flow_target")
    assert float(wc.state) == 45.0
    assert wc.attributes["boost_offset"] > 0

    # An hour later it ends and heating goes back off
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    client.write_indoor.reset_mock()
    with _patch(
        "custom_components.samsung_ehs.coordinator.dt_util.utcnow",
        return_value=dt_util.utcnow() + timedelta(hours=1, seconds=1),
    ):
        await c.async_control()
    client.write_indoor.assert_any_await(R.IU_POWER, 0)
    assert c.boost_until is None


async def test_cancel_boost_via_preset(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "climate", "set_preset_mode",
        {"entity_id": "climate.samsung_ehs", "preset_mode": "boost"}, blocking=True,
    )
    assert hass.states.get("climate.samsung_ehs").attributes["preset_mode"] == "boost"
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.samsung_ehs_cancel_boost"}, blocking=True
    )
    assert hass.states.get("climate.samsung_ehs").attributes["preset_mode"] == "none"


async def test_dhw_schedule(hass: HomeAssistant, client) -> None:
    hass.states.async_set("schedule.hot_water", "off")
    await _setup(hass, {"dhw_schedule": "schedule.hot_water"})
    # Out of a block at startup: hot water turned off
    client.write_indoor.assert_any_await(R.IU_DHW_POWER, 0)
    dhw = hass.states.get("water_heater.samsung_ehs_hot_water")
    assert dhw.attributes["schedule"] == "schedule.hot_water"
    assert dhw.attributes["schedule_state"] == "off"

    client.write_indoor.reset_mock()
    hass.states.async_set("schedule.hot_water", "on")
    await hass.async_block_till_done()
    client.write_indoor.assert_any_await(R.IU_DHW_POWER, 1)

    client.write_indoor.reset_mock()
    hass.states.async_set("schedule.hot_water", "unavailable")
    await hass.async_block_till_done()
    client.write_indoor.assert_not_awaited()


async def test_dhw_schedule_ignored_when_heating_only(hass: HomeAssistant, client) -> None:
    hass.states.async_set("schedule.hot_water", "off")
    await _setup(hass, {"dhw_schedule": "schedule.hot_water", "hot_water": False})
    assert not any(
        c.args[0] == R.IU_DHW_POWER for c in client.write_indoor.await_args_list
    )


async def test_dhw_boost_restores_schedule(hass: HomeAssistant, client) -> None:
    from homeassistant.util import dt as dt_util
    from unittest.mock import patch as _patch

    client.read_all.return_value = _snapshot(dhw_power=False)
    hass.states.async_set("schedule.hot_water", "off")
    await _setup(hass, {"dhw_schedule": "schedule.hot_water"})
    client.write_indoor.reset_mock()
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.samsung_ehs_boost_hot_water"}, blocking=True
    )
    client.write_indoor.assert_any_await(R.IU_DHW_POWER, 1)
    assert hass.states.get("water_heater.samsung_ehs_hot_water").attributes["boost_until"]

    # A schedule "off" edge during the boost is ignored
    client.write_indoor.reset_mock()
    hass.states.async_set("schedule.hot_water", "on")
    await hass.async_block_till_done()
    hass.states.async_set("schedule.hot_water", "off")
    await hass.async_block_till_done()
    assert not any(c.args == (R.IU_DHW_POWER, 0) for c in client.write_indoor.await_args_list)

    # After the hour it follows the schedule again (off)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    with _patch(
        "custom_components.samsung_ehs.coordinator.dt_util.utcnow",
        return_value=dt_util.utcnow() + timedelta(hours=1, seconds=1),
    ):
        await c.async_control()
    client.write_indoor.assert_any_await(R.IU_DHW_POWER, 0)
    assert c.dhw_boost_until is None


async def test_dhw_boost_cancel_restores_previous(hass: HomeAssistant, client) -> None:
    client.read_all.return_value = _snapshot(dhw_power=False)
    await _setup(hass)
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.samsung_ehs_boost_hot_water"}, blocking=True
    )
    client.write_indoor.reset_mock()
    await hass.services.async_call(
        "button", "press",
        {"entity_id": "button.samsung_ehs_cancel_hot_water_boost"}, blocking=True,
    )
    client.write_indoor.assert_any_await(R.IU_DHW_POWER, 0)  # was off before


async def test_register_dump_falls_back_to_single_reads() -> None:
    from custom_components.samsung_ehs.api import EhsClient, EhsConnectionError

    c = EhsClient.__new__(EhsClient)
    import asyncio

    c._lock = asyncio.Lock()

    async def fake_read(addr, count):
        if addr <= 3 < addr + count:  # address 3 is unmapped
            raise EhsConnectionError("illegal address")
        return [addr + i for i in range(count)]

    c._read = fake_read
    out = await c.dump(0, 12, chunk=5)
    assert out["errors"].keys() == {3}
    assert out["registers"][4] == 4 and out["registers"][12] == 12
    assert 3 not in out["registers"]


async def test_reconfigure_changes_port(hass: HomeAssistant, client) -> None:
    from homeassistant.config_entries import SOURCE_RECONFIGURE

    entry = await _setup(hass, {"hot_water": False})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["step_id"] == "reconfigure"
    new = {**DATA, "port": "/dev/serial/by-id/usb-test"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["port"] == "/dev/serial/by-id/usb-test"
    assert entry.options["hot_water"] is False


def test_stable_port(tmp_path, monkeypatch) -> None:
    from custom_components.samsung_ehs import config_flow as cf

    dev = tmp_path / "ttyUSB0"
    dev.touch()
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    link = by_id / "usb-FTDI_FT232R_USB_UART_X-if00-port0"
    link.symlink_to(dev)
    monkeypatch.setattr(cf, "BY_ID_DIR", str(by_id))
    assert cf.stable_port(str(dev)) == str(link)
    assert cf.stable_port(str(link)) == str(link)
    assert cf.stable_port("simulator") == "simulator"
    assert cf.stable_port(str(tmp_path / "ttyUSB9")) == str(tmp_path / "ttyUSB9")


async def test_extended_read_skips_unmapped_registers() -> None:
    import asyncio

    from custom_components.samsung_ehs.api import EhsClient, EhsIllegalAddress

    c = EhsClient.__new__(EhsClient)
    c._lock = asyncio.Lock()
    c._base = 50
    c._ext_missing = set()
    live = {4: 53, 82: 0, 83: 1}  # pretend 5 is not mapped
    calls = []

    async def fake_read(addr, count):
        calls.append((addr, count))
        if any(a not in live for a in range(addr, addr + count)):
            raise EhsIllegalAddress("exception 2")
        return [live[a] for a in range(addr, addr + count)]

    c._read = fake_read
    assert await c._read_extended() == [53, None, 0, 1]
    assert c._ext_missing == {5}
    calls.clear()
    assert await c._read_extended() == [53, None, 0, 1]
    assert calls == [(4, 1), (82, 2)]  # unmapped register not retried


# --- Write path against a stubbed pymodbus client -------------------------


class _Resp:
    def __init__(self, registers=None, exception_code=None):
        self.registers = registers or []
        self.exception_code = exception_code

    def isError(self):  # noqa: N802 - pymodbus API
        return self.exception_code is not None


class _FakeModbus:
    """Mimics pymodbus >= 3.10 (device_id=)."""

    def __init__(self, **kw):
        self.connected = True
        self.calls = []
        self.regs: dict[int, int] = {}

    async def connect(self):
        return True

    def close(self):
        pass

    async def read_holding_registers(self, address, *, count=1, device_id=0):
        self.calls.append(("read", address, count, device_id))
        if any(a not in self.regs for a in range(address, address + count)):
            return _Resp(exception_code=2)
        return _Resp([self.regs[a] for a in range(address, address + count)])

    async def write_register(self, address, value, *, device_id=0):
        self.calls.append(("write", address, value, device_id))
        return _Resp([])

    async def write_registers(self, address, values, *, device_id=0):
        self.calls.append(("write_many", address, list(values), device_id))
        return _Resp([])


class _OldFakeModbus(_FakeModbus):
    """Mimics pymodbus < 3.10 (slave=)."""

    async def read_holding_registers(self, address, *, count=1, slave=0):
        return await super().read_holding_registers(address, count=count, device_id=slave)

    async def write_register(self, address, value, *, slave=0):
        return await super().write_register(address, value, device_id=slave)


def _real_client(fake_cls, slave=3):
    from custom_components.samsung_ehs import api

    with patch.object(api, "AsyncModbusSerialClient", fake_cls), patch.object(
        api, "INTER_REQUEST_DELAY", 0
    ):
        c = api.EhsClient("/dev/null", slave)
    return c


@pytest.mark.parametrize("fake_cls", [_FakeModbus, _OldFakeModbus])
async def test_write_path_addresses_and_encoding(fake_cls) -> None:
    from custom_components.samsung_ehs import api

    c = _real_client(fake_cls)
    with patch.object(api, "INTER_REQUEST_DELAY", 0):
        await c.write_indoor(R.IU_WATER_OUT_SET, R.encode_temp(-2.5))
        await c.write(R.IU_DHW_SET, R.encode_temp(48.0))
    assert c._client.calls == [
        ("write", 50 + R.IU_WATER_OUT_SET, 0xFFE7, 3),  # -25 as 16-bit
        ("write", R.IU_DHW_SET, 480, 3),
    ]


async def test_enable_extended_writes_nasa_ids() -> None:
    from custom_components.samsung_ehs import api

    c = _real_client(_FakeModbus, slave=1)
    with patch.object(api, "INTER_REQUEST_DELAY", 0):
        await c.enable_extended()
    assert c._client.calls == [
        ("write_many", R.EXT_OUTDOOR_ENABLE, [v.nasa_id for v in R.EXT_OUTDOOR], 1),
        ("write_many", R.EXT_INDOOR_ENABLE, [v.nasa_id for v in R.EXT_INDOOR], 1),
    ]


async def test_illegal_address_is_distinguished() -> None:
    from custom_components.samsung_ehs import api

    c = _real_client(_FakeModbus)
    c._client.regs = {4: 53}
    with patch.object(api, "INTER_REQUEST_DELAY", 0):
        assert await c._read(4, 1) == [53]
        with pytest.raises(api.EhsIllegalAddress):
            await c._read(5, 1)


async def test_extended_glitch_is_retried_next_poll() -> None:
    import asyncio

    from custom_components.samsung_ehs.api import EhsClient, EhsConnectionError

    c = EhsClient.__new__(EhsClient)
    c._lock = asyncio.Lock()
    c._base = 50
    c._ext_missing = set()
    glitch = {5}

    async def fake_read(addr, count):
        if any(a in glitch for a in range(addr, addr + count)):
            raise EhsConnectionError("timeout")
        return [1] * count

    c._read = fake_read
    assert await c._read_extended() == [1, None, 1, 1]
    assert c._ext_missing == set()  # a timeout doesn't blacklist it
    glitch.clear()
    assert await c._read_extended() == [1, 1, 1, 1]


# --- Review fixes ----------------------------------------------------------


def _entity(hass, domain, entity_id):
    return hass.data[domain].get_entity(entity_id)


async def test_dhw_setpoint_clamped(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    wh = _entity(hass, "water_heater", "water_heater.samsung_ehs_hot_water")
    await wh.async_set_temperature(temperature=95)
    client.write_indoor.assert_any_await(R.IU_DHW_SET, 700)
    await wh.async_set_temperature(temperature=-10)
    client.write_indoor.assert_any_await(R.IU_DHW_SET, 300)


async def test_room_target_clamped(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await c.async_set_room_target(100.0)
    assert c.room_target == 28.0


async def test_restore_unavailable_keeps_default(hass: HomeAssistant, client) -> None:
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(hass, [State("climate.samsung_ehs", "unavailable", {"temperature": 99})])
    await _setup(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert c.heating_enabled is True
    assert c.room_target == 28.0


async def test_restore_off_stays_off(hass: HomeAssistant, client) -> None:
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache

    mock_restore_cache(hass, [State("climate.samsung_ehs", "off", {"temperature": 20})])
    await _setup(hass)
    assert hass.states.get("climate.samsung_ehs").state == "off"


async def test_flow_min_above_max_rejected(hass: HomeAssistant, client) -> None:
    from homeassistant.exceptions import ServiceValidationError

    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": "number.samsung_ehs_minimum_flow_temperature", "value": 50},
            blocking=True,
        )


async def test_emitter_change_resets_learning(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    c.adaptive.design, c.adaptive.mild = 4.0, 2.0
    await c.async_set_emitter("underfloor")  # same type: kept
    assert c.adaptive.design == pytest.approx(4.0, abs=0.01)
    await c.async_set_emitter("radiators")
    assert c.adaptive.design == pytest.approx(0, abs=0.01)
    assert c.adaptive.mild == pytest.approx(0, abs=0.01)


async def test_apply_learned_curve_clamped(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    c.wc_params["wc_flow_design"] = 54.0
    c.adaptive.design, c.adaptive.mild = 5.0, -30.0
    await c.async_apply_learned_curve()
    assert c.wc_params["wc_flow_design"] == 55.0
    assert c.wc_params["wc_flow_mild"] == 15.0


async def test_control_request_during_pass_is_rerun(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    seen = []

    async def fake(force):
        seen.append(force)
        if len(seen) == 1:  # Boost pressed while this pass holds the lock
            await c.async_control(force_flow=True)

    c._async_control = fake
    await c.async_control()
    assert seen == [False, True]


async def test_heat_energy_today_is_total_with_reset(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    st = hass.states.get("sensor.samsung_ehs_heat_energy_today")
    assert st.attributes["state_class"] == "total"
    assert st.attributes["last_reset"]


async def test_diagnostics_redacted(hass: HomeAssistant, client) -> None:
    from custom_components.samsung_ehs.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = await _setup(hass, {"room_sensor": "sensor.bedroom"})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["port"] == "**REDACTED**"
    assert diag["entry"]["options"]["room_sensor"] == "**REDACTED**"
    assert diag["entry"]["data"]["slave"] == 1


async def test_second_entry_on_same_port_aborts(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "slave": 2, "hot_water": True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "port_in_use"


async def test_reconfigure_onto_other_entrys_port_aborts(hass: HomeAssistant, client) -> None:
    from homeassistant.config_entries import SOURCE_RECONFIGURE

    await _setup(hass)
    other = MockConfigEntry(domain=DOMAIN, data={**DATA, "port": "simulator"})
    other.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": other.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["reason"] == "port_in_use"


async def test_simulator_entry_is_titled(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DATA, "port": "simulator", "hot_water": True}
    )
    assert result["title"] == "Samsung EHS (simulator)"


async def test_learned_end_held_within_flow_limits(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    c.wc_params["wc_flow_mild"] = 25.0
    c.wc_params["wc_flow_min"] = 25.0
    c.adaptive.mild = -1.54
    c.async_update_listeners()
    await hass.async_block_till_done()
    mild = hass.states.get("sensor.samsung_ehs_learned_flow_at_mild_end")
    assert float(mild.state) == 25.0
    assert mild.attributes["unclamped_flow"] == 23.5
    offset = hass.states.get("sensor.samsung_ehs_learned_curve_offset")
    assert offset.attributes["learned_flow_at_mild"] == 25.0


async def test_hvac_action_follows_thermostat_not_compressor(hass: HomeAssistant, client) -> None:
    await _setup(hass)
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    assert hass.states.get("climate.samsung_ehs").attributes["hvac_action"] == "heating"
    # Unit cycles its compressor off while heating is still called for.
    c.data.extended["compressor_freq"] = 0
    c.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("climate.samsung_ehs").attributes["hvac_action"] == "heating"
