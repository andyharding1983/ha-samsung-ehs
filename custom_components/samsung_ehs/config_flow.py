"""Config flow for Samsung EHS."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import EhsClient, EhsConnectionError
from .simulator import SIM_PORT
from .const import (
    CONF_BAUDRATE,
    CONF_EXTENDED,
    CONF_HOT_WATER,
    CONF_INDOOR_INDEX,
    CONF_OUTDOOR_SENSOR,
    CONF_PORT,
    CONF_POWER_SENSOR,
    CONF_ROOM_SENSOR,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    CONF_WC_INTERVAL,
    CONF_WEATHER_ENTITY,
    DEFAULT_BAUDRATE,
    DEFAULT_EXTENDED,
    DEFAULT_HOT_WATER,
    DEFAULT_INDOOR_INDEX,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE,
    DEFAULT_WC_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT, default=DEFAULT_PORT): str,
        vol.Required(CONF_SLAVE, default=DEFAULT_SLAVE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
        vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.In(
            [9600, 19200]
        ),
        vol.Required(CONF_INDOOR_INDEX, default=DEFAULT_INDOOR_INDEX): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=47)
        ),
        vol.Required(CONF_HOT_WATER, default=DEFAULT_HOT_WATER): bool,
    }
)


async def _validate(data: dict[str, Any]) -> None:
    if data[CONF_PORT] == SIM_PORT:
        return
    client = EhsClient(
        data[CONF_PORT], data[CONF_SLAVE], data[CONF_BAUDRATE], data[CONF_INDOOR_INDEX]
    )
    try:
        await client.read_all(extended=False)
    finally:
        client.close()


class EhsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Samsung EHS."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_PORT]}_{user_input[CONF_SLAVE]}_"
                f"{user_input[CONF_INDOOR_INDEX]}"
            )
            self._abort_if_unique_id_configured()
            try:
                await _validate(user_input)
            except EhsConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                data = dict(user_input)
                hot_water = data.pop(CONF_HOT_WATER)
                return self.async_create_entry(
                    title="Samsung EHS", data=data, options={CONF_HOT_WATER: hot_water}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EhsOptionsFlow()


def _entity(domain: str, device_class: str | None = None) -> selector.EntitySelector:
    cfg = selector.EntitySelectorConfig(domain=domain)
    if device_class:
        cfg = selector.EntitySelectorConfig(domain=domain, device_class=device_class)
    return selector.EntitySelector(cfg)


class EhsOptionsFlow(OptionsFlow):
    """Options: helper entities for WC/COP and polling intervals."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Optional(CONF_ROOM_SENSOR): _entity("sensor", "temperature"),
                vol.Optional(CONF_POWER_SENSOR): _entity("sensor", "power"),
                vol.Optional(CONF_WEATHER_ENTITY): _entity("weather"),
                vol.Optional(CONF_OUTDOOR_SENSOR): _entity("sensor", "temperature"),
                vol.Required(
                    CONF_HOT_WATER, default=DEFAULT_HOT_WATER
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_EXTENDED, default=DEFAULT_EXTENDED
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=300, step=1, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_WC_INTERVAL, default=DEFAULT_WC_INTERVAL
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=60, max=3600, step=30, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
