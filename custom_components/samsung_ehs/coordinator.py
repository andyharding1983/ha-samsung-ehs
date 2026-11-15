"""Data coordinator: polling, COP tracking and weather compensation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import registers as R
from .api import EhsClient, EhsConnectionError
from .const import (
    CONF_DHW_SCHEDULE,
    BOOST_DURATION_S,
    WC_WIND_GAIN,
    WC_WIND_THRESHOLD,
    CONF_EXTENDED,
    CONF_HOT_WATER,
    CONF_OUTDOOR_SENSOR,
    CONF_POWER_SENSOR,
    CONF_ROOM_SENSOR,
    CONF_SCAN_INTERVAL,
    CONF_WC_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_EXTENDED,
    DEFAULT_HOT_WATER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WC_INTERVAL,
    DEFAULT_EMITTER,
    DOMAIN,
    EMITTER_PRESETS,
    WC_ADAPT_LIMIT,
    WC_ADAPT_RATE,
    WC_DEFAULTS,
    WC_HYSTERESIS,
    WC_MIN_OFF,
    WC_MIN_ON,
    WC_ROOM_TARGET,
    WC_WRITE_DEADBAND,
)
from .control import AdaptiveCurve, Thermostat
from .cop import EnergyTracker, heat_output_w, instant_cop
from .weather_comp import WcParams, WcResult, compute, round_half

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
FORECAST_REFRESH = timedelta(minutes=30)
SAVE_EVERY = timedelta(minutes=5)


OUTDOOR_RANGE = (-40.0, 50.0)
ROOM_RANGE = (5.0, 35.0)


_WIND_TO_MS = {
    "m/s": 1.0,
    "km/h": 1 / 3.6,
    "mph": 0.44704,
    "kn": 0.514444,
    "ft/s": 0.3048,
}


def _wind_ms(value: Any, unit: str | None) -> float | None:
    """Convert a weather wind speed to m/s (HA weather default is km/h)."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    factor = _WIND_TO_MS.get(unit or "km/h")
    if factor is None or not 0 <= val < 100 / factor:
        return None
    return val * factor


def _plausible(val: float | None, rng: tuple[float, float]) -> float | None:
    if val is None or not rng[0] <= val <= rng[1]:
        return None
    return val


def _float_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        val = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit == "kW":
        val *= 1000.0
    return val


class EhsCoordinator(DataUpdateCoordinator[R.EhsData]):
    """Polls the MIM-B19N and runs the HA-side control logic."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: EhsClient) -> None:
        opts = entry.options
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.extended = opts.get(CONF_EXTENDED, DEFAULT_EXTENDED)
        self.hot_water: bool = opts.get(CONF_HOT_WATER, DEFAULT_HOT_WATER)
        self.room_sensor: str | None = opts.get(CONF_ROOM_SENSOR)
        self.power_sensor: str | None = opts.get(CONF_POWER_SENSOR)
        self.weather_entity: str | None = opts.get(CONF_WEATHER_ENTITY)
        self.outdoor_sensor: str | None = opts.get(CONF_OUTDOOR_SENSOR)
        self.dhw_schedule: str | None = (
            opts.get(CONF_DHW_SCHEDULE) if self.hot_water else None
        )
        self._unsub_schedule = None
        self.wc_interval = timedelta(
            seconds=opts.get(CONF_WC_INTERVAL, DEFAULT_WC_INTERVAL)
        )

        # Control state. Tuning params are pushed in by the (restored) number
        # entities; room target and on/off by the (restored) climate entity.
        self.wc_params: dict[str, float] = dict(WC_DEFAULTS)
        self.heating_enabled = True
        self.emitter = DEFAULT_EMITTER
        self.wc_result: WcResult | None = None
        self.wc_last_write: datetime | None = None
        self.thermostat = Thermostat()
        self.adaptive = AdaptiveCurve()
        self._thermostat_synced = False
        # Set once entities have restored target/params, so we never act
        # on defaults right after a restart.
        self.ready = False
        self._last_control: float | None = None
        self._last_flow_write: float | None = None
        self._control_lock = asyncio.Lock()
        self._forecast: list[tuple[datetime, float, float | None]] | None = None
        self.boost_until: datetime | None = None
        self._forecast_at: datetime | None = None

        # COP
        self.heat_w: float | None = None
        self.power_w: float | None = None
        self.cop: float | None = None
        self.energy = EnergyTracker()
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._last_save: datetime | None = None
        self._unsub_listener = None
        self._closed = False

    # ------------------------------------------------------------------ setup
    async def async_setup(self) -> None:
        stored = await self._store.async_load() or {}
        if "energy" not in stored:  # pre-0.2 layout: energy at top level
            stored = {"energy": stored}
        self.energy = EnergyTracker.from_dict(stored.get("energy"))
        if "adaptive" in stored:
            self.adaptive.design = float(stored["adaptive"].get("design", 0.0))
            self.adaptive.mild = float(stored["adaptive"].get("mild", 0.0))
        else:  # single offset from earlier versions applies to both ends
            self.adaptive.design = self.adaptive.mild = float(
                stored.get("adaptive_offset", 0.0)
            )
        # Run the control loop after every successful poll.
        self._unsub_listener = self.async_add_listener(self._schedule_control)

    def _store_data(self) -> dict[str, Any]:
        return {
            "energy": self.energy.as_dict(),
            "adaptive": {"design": self.adaptive.design, "mild": self.adaptive.mild},
        }

    def _schedule_control(self) -> None:
        if self.ready and self.data is not None and not self._control_lock.locked():
            self.config_entry.async_create_background_task(
                self.hass, self.async_control(), "samsung_ehs control"
            )

    async def async_shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._unsub_listener:
            self._unsub_listener()
            self._unsub_listener = None
        if self._unsub_schedule:
            self._unsub_schedule()
            self._unsub_schedule = None
        await self._store.async_save(self._store_data())
        await super().async_shutdown()
        self.client.close()

    # ------------------------------------------------------ hot water schedule
    async def async_start_dhw_schedule(self) -> None:
        """Follow the schedule helper: hot water on in a block, off outside.

        Acts on block start/end (and once at startup), so hot water switched
        on or off by hand stays that way until the next block edge.
        """
        if not self.dhw_schedule:
            return
        self._unsub_schedule = async_track_state_change_event(
            self.hass, self.dhw_schedule, self._on_schedule_change
        )
        st = self.hass.states.get(self.dhw_schedule)
        if st is not None:
            await self._async_apply_schedule(st.state)

    @callback
    def _on_schedule_change(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        old = event.data["old_state"]
        if new is None or (old is not None and old.state == new.state):
            return
        self.config_entry.async_create_background_task(
            self.hass, self._async_apply_schedule(new.state), "samsung_ehs dhw schedule"
        )

    async def _async_apply_schedule(self, state: str) -> None:
        if state not in ("on", "off"):
            return  # unavailable/unknown: leave hot water alone
        on = state == "on"
        if self.data is not None and self.data.dhw_power == on:
            return
        _LOGGER.info("Hot water schedule: %s", "on" if on else "off")
        try:
            await self.client.write_indoor(R.IU_DHW_POWER, 1 if on else 0)
        except EhsConnectionError as err:
            _LOGGER.warning("Could not switch hot water: %s", err)
            return
        if self.data is not None:
            self.data.dhw_power = on
        self.async_update_listeners()

    # ---------------------------------------------------------------- polling
    async def _async_update_data(self) -> R.EhsData:
        try:
            data = await self.client.read_all(self.extended)
        except EhsConnectionError as err:
            raise UpdateFailed(f"MIM-B19N not responding: {err}") from err
        self._update_cop(data)
        return data

    def _update_cop(self, data: R.EhsData) -> None:
        flow = (data.extended or {}).get("flow_rate")
        self.heat_w = heat_output_w(flow, data.delta_t)
        self.power_w = _float_state(self.hass, self.power_sensor)
        if self.power_w is None and not self.power_sensor:
            # Simulator provides its own electrical power.
            self.power_w = (data.extended or {}).get("sim_power")
        self.cop = instant_cop(self.heat_w, self.power_w)
        now = dt_util.now()
        self.energy.update(now, self.heat_w, self.power_w)
        if self._last_save is None or now - self._last_save >= SAVE_EVERY:
            self._last_save = now
            self._store.async_delay_save(self._store_data, 10)

    # ----------------------------------------------------------- unit writes
    async def async_write(self, offset: int, value: int) -> None:
        try:
            await self.client.write_indoor(offset, value)
        except EhsConnectionError as err:
            raise HomeAssistantError(f"Write to heat pump failed: {err}") from err
        await self.async_request_refresh()

    async def async_write_absolute(self, address: int, value: int) -> None:
        try:
            await self.client.write(address, value)
        except EhsConnectionError as err:
            raise HomeAssistantError(f"Write to heat pump failed: {err}") from err
        await self.async_request_refresh()

    # ------------------------------------------------- weather compensation
    def outdoor_temp(self) -> float | None:
        val = _plausible(_float_state(self.hass, self.outdoor_sensor), OUTDOOR_RANGE)
        if val is None and self.data and self.data.extended:
            val = _plausible(self.data.extended.get("outdoor_temp"), OUTDOOR_RANGE)
        return val

    def room_temp(self) -> float | None:
        # A unit without a Samsung room sensor can report 0 here; reject
        # anything implausible rather than drive a maximum room boost.
        val = _plausible(_float_state(self.hass, self.room_sensor), ROOM_RANGE)
        if val is None and self.data:
            val = _plausible(self.data.room_temp, ROOM_RANGE)
        return val

    async def _async_refresh_forecast(self) -> None:
        if not self.weather_entity:
            self._forecast = None
            return
        now = dt_util.utcnow()
        if self._forecast_at and now - self._forecast_at < FORECAST_REFRESH:
            return
        try:
            resp: dict[str, Any] = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "hourly"},
                target={"entity_id": self.weather_entity},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("forecast unavailable: %s", err)
            self._forecast = None
            return
        items = (resp or {}).get(self.weather_entity, {}).get("forecast", [])
        unit = self._wind_unit()
        parsed: list[tuple[datetime, float, float | None]] = []
        for item in items:
            ts = dt_util.parse_datetime(str(item.get("datetime")))
            temp = item.get("temperature")
            if ts is not None and temp is not None:
                wind = _wind_ms(item.get("wind_speed"), unit)
                parsed.append((dt_util.as_utc(ts), float(temp), wind))
        self._forecast = parsed
        self._forecast_at = now

    def _forecast_window(self, hours: float) -> list[float] | None:
        if not self._forecast or hours <= 0:
            return None
        now = dt_util.utcnow()
        end = now + timedelta(hours=hours)
        temps = [t for ts, t, _w in self._forecast if now - timedelta(hours=1) < ts <= end]
        return temps or None

    def _wind_unit(self) -> str | None:
        st = self.hass.states.get(self.weather_entity) if self.weather_entity else None
        return st.attributes.get("wind_speed_unit") if st else None

    def wind_speed(self, hours: float) -> float | None:
        """Mean wind (m/s): now plus the forecast over the look-ahead."""
        if not self.weather_entity:
            return None
        st = self.hass.states.get(self.weather_entity)
        winds: list[float] = []
        if st is not None:
            now_w = _wind_ms(st.attributes.get("wind_speed"), self._wind_unit())
            if now_w is not None:
                winds.append(now_w)
        if self._forecast and hours > 0:
            now = dt_util.utcnow()
            end = now + timedelta(hours=hours)
            winds += [
                w for ts, _t, w in self._forecast
                if w is not None and now - timedelta(hours=1) < ts <= end
            ]
        return round(sum(winds) / len(winds), 1) if winds else None

    def params(self) -> WcParams:
        p = self.wc_params
        return WcParams(
            outdoor_design=p["wc_outdoor_design"],
            flow_design=p["wc_flow_design"],
            outdoor_mild=p["wc_outdoor_mild"],
            flow_mild=p["wc_flow_mild"],
            room_target=p["wc_room_target"],
            room_gain=p["wc_room_gain"],
            room_max_offset=p["wc_room_max_offset"],
            forecast_hours=p["wc_forecast_hours"],
            forecast_weight=p["wc_forecast_weight"],
            flow_min=p["wc_flow_min"],
            flow_max=p["wc_flow_max"],
            heating_cutoff=p["wc_heating_cutoff"],
            wind_gain=p[WC_WIND_GAIN],
            wind_threshold=p[WC_WIND_THRESHOLD],
        )

    async def async_set_wc_param(self, key: str, value: float) -> None:
        self.wc_params[key] = value
        self._apply_thermostat_params()
        await self.async_control(force_flow=True)

    def _apply_thermostat_params(self) -> None:
        p = self.wc_params
        self.thermostat.hysteresis = p[WC_HYSTERESIS]
        self.thermostat.min_on_s = p[WC_MIN_ON] * 60
        self.thermostat.min_off_s = p[WC_MIN_OFF] * 60
        self.adaptive.rate = p[WC_ADAPT_RATE]
        self.adaptive.limit = p[WC_ADAPT_LIMIT]

    async def async_set_emitter(self, emitter: str) -> None:
        """Switch emitter type and apply its preset values."""
        self.emitter = emitter
        self.wc_params.update(EMITTER_PRESETS[emitter])
        self._apply_thermostat_params()
        await self.async_control(force_flow=True)

    @property
    def adaptive_offset(self) -> float:
        """Learned offset currently applied (at today's effective outdoor)."""
        if self.wc_result is not None:
            return self.wc_result.curve_offset
        return self.adaptive.offset_at(0.5)

    async def async_apply_learned_curve(self) -> None:
        """Fold the learned end offsets into the curve points and reset them."""
        p = self.wc_params
        p["wc_flow_design"] = round(p["wc_flow_design"] + self.adaptive.design, 1)
        p["wc_flow_mild"] = round(p["wc_flow_mild"] + self.adaptive.mild, 1)
        self.adaptive.reset()
        self._store.async_delay_save(self._store_data, 1)
        await self.async_control(force_flow=True)

    async def async_reset_learning(self) -> None:
        self.adaptive.reset()
        self._store.async_delay_save(self._store_data, 1)
        await self.async_control(force_flow=True)

    @property
    def room_target(self) -> float:
        return self.wc_params[WC_ROOM_TARGET]

    async def async_set_room_target(self, value: float) -> None:
        await self.async_set_wc_param(WC_ROOM_TARGET, value)

    async def async_set_heating(self, enabled: bool) -> None:
        """User turned heating on/off: overrides the thermostat timers."""
        self.heating_enabled = enabled
        self.thermostat.force(time.monotonic(), enabled)
        await self.async_control(force_flow=True)

    @property
    def boost_active(self) -> bool:
        return self.boost_until is not None and dt_util.utcnow() < self.boost_until

    async def async_start_boost(self) -> None:
        """Run at maximum flow for an hour; pressing again restarts the hour."""
        self.boost_until = dt_util.utcnow() + timedelta(seconds=BOOST_DURATION_S)
        _LOGGER.info("Boost until %s", self.boost_until)
        await self.async_control(force_flow=True)

    async def async_cancel_boost(self) -> None:
        if self.boost_until is None:
            return
        self.boost_until = None
        _LOGGER.info("Boost cancelled")
        await self.async_control(force_flow=True)

    async def async_control(self, force_flow: bool = False) -> None:
        """Thermostat, adaptive offset and weather compensation in one pass."""
        if self._control_lock.locked():
            return
        async with self._control_lock:
            await self._async_control(force_flow)

    async def _async_control(self, force_flow: bool) -> None:
        data = self.data
        if data is None or not self.ready:
            return
        now = time.monotonic()
        dt_s = 0.0 if self._last_control is None else now - self._last_control
        self._last_control = now
        self._apply_thermostat_params()

        if not self._thermostat_synced:
            # After a restart assume the unit only just started, so the
            # minimum on time is respected.
            self.thermostat.calling = data.power or not self.heating_enabled
            self.thermostat.last_change = now
            self._thermostat_synced = True

        boosting = self.boost_active
        if self.boost_until is not None and not boosting:
            _LOGGER.info("Boost finished")
            self.boost_until = None
            force_flow = True

        room = self.room_temp()
        target = self.room_target
        # Learn only while space heating under normal control: not paused,
        # boosting, defrosting or diverted to the hot water cylinder.
        running = (
            self.heating_enabled
            and data.power
            and not boosting
            and not data.defrost
            and not (self.hot_water and data.dhw_active)
        )
        prev = self.wc_result
        self.adaptive.update(
            dt_s,
            room,
            target,
            w_design=prev.design_weight if prev and prev.heating_needed else None,
            learning=running,
            limited=prev.limited if prev else 0,
        )

        outdoor = self.outdoor_temp()
        result: WcResult | None = None
        if outdoor is not None:
            await self._async_refresh_forecast()
            p = self.params()
            p = replace(
                p,
                flow_min=max(p.flow_min, R.FLOW_SET_MIN),
                flow_max=min(p.flow_max, R.FLOW_SET_MAX),
            )
            result = compute(
                p, outdoor, self._forecast_window(p.forecast_hours), room,
                offset_design=self.adaptive.design,
                offset_mild=self.adaptive.mild,
                wind=self.wind_speed(p.forecast_hours),
            )
        self.wc_result = result
        heating_needed = result.heating_needed if result else True

        if boosting:
            # Counts as a run, so the minimum on time applies afterwards.
            self.thermostat.force(now, True)
            calling = True
        else:
            calling = self.thermostat.update(now, room, target, heating_needed)
        want_on = boosting or (self.heating_enabled and calling)

        if want_on != data.power or (want_on and data.mode != R.Mode.HEAT):
            await self._async_set_power(want_on, data)

        if boosting:
            flow = round_half(min(self.wc_params["wc_flow_max"], R.FLOW_SET_MAX))
            await self._async_write_flow(flow, data, now, force_flow)
        elif want_on and result is not None:
            await self._async_write_flow(result.flow_target, data, now, force_flow)

        self.async_update_listeners()

    async def _async_set_power(self, on: bool, data: R.EhsData) -> None:
        _LOGGER.info(
            "Space heating %s (room %s, target %s)",
            "on" if on else "off", self.room_temp(), self.room_target,
        )
        try:
            if on and data.mode != R.Mode.HEAT:
                await self.client.write_indoor(R.IU_MODE, int(R.Mode.HEAT))
            if on != data.power:
                await self.client.write_indoor(R.IU_POWER, 1 if on else 0)
            data.power = on
            data.mode = R.Mode.HEAT if on else data.mode
        except EhsConnectionError as err:
            _LOGGER.warning("Could not switch space heating: %s", err)

    async def _async_write_flow(
        self, target: float, data: R.EhsData, now: float, force: bool
    ) -> None:
        current = data.water_out_set
        if current is not None and abs(target - current) < WC_WRITE_DEADBAND:
            return
        interval = self.wc_interval.total_seconds()
        if (
            not force
            and self._last_flow_write is not None
            and now - self._last_flow_write < interval
        ):
            return
        _LOGGER.debug("Flow setpoint %s -> %s (%s)", current, target, self.wc_result)
        try:
            await self.client.write_indoor(R.IU_WATER_OUT_SET, R.encode_temp(target))
        except EhsConnectionError as err:
            _LOGGER.warning("Could not write flow setpoint: %s", err)
            return
        data.water_out_set = target
        self._last_flow_write = now
        self.wc_last_write = dt_util.utcnow()
