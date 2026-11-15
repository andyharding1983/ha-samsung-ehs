"""Data coordinator: polling, COP tracking and weather compensation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import registers as R
from .api import EhsClient, EhsConnectionError
from .const import (
    CONF_EXTENDED,
    CONF_OUTDOOR_SENSOR,
    CONF_POWER_SENSOR,
    CONF_ROOM_SENSOR,
    CONF_SCAN_INTERVAL,
    CONF_WC_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_EXTENDED,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WC_INTERVAL,
    DOMAIN,
    WC_DEFAULTS,
    WC_WRITE_DEADBAND,
)
from .cop import EnergyTracker, heat_output_w, instant_cop
from .weather_comp import WcParams, WcResult, compute

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
FORECAST_REFRESH = timedelta(minutes=30)
SAVE_EVERY = timedelta(minutes=5)


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
        self.room_sensor: str | None = opts.get(CONF_ROOM_SENSOR)
        self.power_sensor: str | None = opts.get(CONF_POWER_SENSOR)
        self.weather_entity: str | None = opts.get(CONF_WEATHER_ENTITY)
        self.outdoor_sensor: str | None = opts.get(CONF_OUTDOOR_SENSOR)
        self.wc_interval = timedelta(
            seconds=opts.get(CONF_WC_INTERVAL, DEFAULT_WC_INTERVAL)
        )

        # Weather compensation state. Params are pushed in by the (restored)
        # number entities; the enable flag by the (restored) switch entity.
        self.wc_params: dict[str, float] = dict(WC_DEFAULTS)
        self.wc_enabled = False
        self.wc_result: WcResult | None = None
        self.wc_last_write: datetime | None = None
        self._forecast: list[tuple[datetime, float]] | None = None
        self._forecast_at: datetime | None = None

        # COP
        self.heat_w: float | None = None
        self.power_w: float | None = None
        self.cop: float | None = None
        self.energy = EnergyTracker()
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._last_save: datetime | None = None
        self._unsub_wc = None
        self._closed = False

    # ------------------------------------------------------------------ setup
    async def async_setup(self) -> None:
        self.energy = EnergyTracker.from_dict(await self._store.async_load())
        self._unsub_wc = async_track_time_interval(
            self.hass, self._async_wc_tick, self.wc_interval
        )

    async def async_shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._unsub_wc:
            self._unsub_wc()
            self._unsub_wc = None
        await self._store.async_save(self.energy.as_dict())
        await super().async_shutdown()
        self.client.close()

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
        self.cop = instant_cop(self.heat_w, self.power_w)
        now = dt_util.now()
        self.energy.update(now, self.heat_w, self.power_w)
        if self._last_save is None or now - self._last_save >= SAVE_EVERY:
            self._last_save = now
            self._store.async_delay_save(self.energy.as_dict, 10)

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
        ext = _float_state(self.hass, self.outdoor_sensor)
        if ext is not None:
            return ext
        if self.data and self.data.extended:
            return self.data.extended.get("outdoor_temp")
        return None

    def room_temp(self) -> float | None:
        val = _float_state(self.hass, self.room_sensor)
        if val is None and self.data:
            val = self.data.room_temp
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
        parsed: list[tuple[datetime, float]] = []
        for item in items:
            ts = dt_util.parse_datetime(str(item.get("datetime")))
            temp = item.get("temperature")
            if ts is not None and temp is not None:
                parsed.append((dt_util.as_utc(ts), float(temp)))
        self._forecast = parsed
        self._forecast_at = now

    def _forecast_window(self, hours: float) -> list[float] | None:
        if not self._forecast or hours <= 0:
            return None
        now = dt_util.utcnow()
        end = now + timedelta(hours=hours)
        temps = [t for ts, t in self._forecast if now - timedelta(hours=1) < ts <= end]
        return temps or None

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
        )

    async def async_set_wc_param(self, key: str, value: float) -> None:
        self.wc_params[key] = value
        await self.async_run_wc()

    async def async_set_wc_enabled(self, enabled: bool) -> None:
        self.wc_enabled = enabled
        await self.async_run_wc()

    async def _async_wc_tick(self, _now: datetime) -> None:
        await self.async_run_wc()

    async def async_run_wc(self) -> None:
        """Calculate and (if enabled) apply the flow temperature target."""
        outdoor = self.outdoor_temp()
        if outdoor is None:
            self.wc_result = None
            self.async_update_listeners()
            return
        await self._async_refresh_forecast()
        p = self.params()
        p = replace(p, flow_min=max(p.flow_min, R.FLOW_SET_MIN),
                    flow_max=min(p.flow_max, R.FLOW_SET_MAX))
        self.wc_result = compute(
            p, outdoor, self._forecast_window(p.forecast_hours), self.room_temp()
        )
        self.async_update_listeners()

        data = self.data
        if not (self.wc_enabled and data and data.power and data.mode == R.Mode.HEAT):
            return
        target = self.wc_result.flow_target
        current = data.water_out_set
        if current is not None and abs(target - current) < WC_WRITE_DEADBAND:
            return
        _LOGGER.debug("WC: flow setpoint %s -> %s (%s)", current, target, self.wc_result)
        try:
            await self.async_write(R.IU_WATER_OUT_SET, R.encode_temp(target))
            self.wc_last_write = dt_util.utcnow()
        except HomeAssistantError as err:
            _LOGGER.warning("WC: could not write flow setpoint: %s", err)
