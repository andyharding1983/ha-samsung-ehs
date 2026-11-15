"""Diagnostics download for Samsung EHS."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import EhsConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EhsConfigEntry
) -> dict[str, Any]:
    c = entry.runtime_data
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "last_update_success": c.last_update_success,
        "data": asdict(c.data) if c.data else None,
        "weather_compensation": {
            "heating_enabled": c.heating_enabled,
            "heat_demand": c.thermostat.calling,
            "adaptive_offset": round(c.adaptive.value, 2),
            "params": c.wc_params,
            "result": asdict(c.wc_result) if c.wc_result else None,
            "last_write": c.wc_last_write.isoformat() if c.wc_last_write else None,
            "forecast_points": len(c._forecast or []),
            "outdoor_used": c.outdoor_temp(),
            "room_used": c.room_temp(),
        },
        "cop": {
            "heat_w": c.heat_w,
            "power_w": c.power_w,
            "cop": c.cop,
            "energy": c.energy.as_dict(),
        },
    }
