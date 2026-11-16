"""Diagnostics download for Samsung EHS."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import EhsConfigEntry
from . import registers as R
from .api import EhsConnectionError

DUMP_START = 0
DUMP_END = 130


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EhsConfigEntry
) -> dict[str, Any]:
    c = entry.runtime_data
    try:
        raw = await c.client.dump(DUMP_START, DUMP_END)
    except EhsConnectionError as err:
        raw = {"error": str(err)}
    if "registers" in raw:
        # Show each register as raw, and as a signed value /10 (temperature style).
        raw["registers"] = {
            str(a): {"raw": v, "x0.1": R.to_signed(v) / 10}
            for a, v in sorted(raw["registers"].items())
        }
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "last_update_success": c.last_update_success,
        "data": asdict(c.data) if c.data else None,
        "weather_compensation": {
            "heating_enabled": c.heating_enabled,
            "heat_demand": c.thermostat.calling,
            "adaptive_design": c.adaptive.design,
            "adaptive_mild": c.adaptive.mild,
            "params": c.wc_params,
            "result": asdict(c.wc_result) if c.wc_result else None,
            "last_write": c.wc_last_write.isoformat() if c.wc_last_write else None,
            "forecast_points": len(c._forecast or []),
            "outdoor_used": c.outdoor_temp(),
            "room_used": c.room_temp(),
        },
        "extended_registers": {
            "enabled": c.extended,
            "requested": {
                "6000": [hex(v.nasa_id) for v in R.EXT_OUTDOOR],
                "7000": [hex(v.nasa_id) for v in R.EXT_INDOOR],
            },
            "assumed_layout": {
                str(addr): v.key for addr, v in R.extended_layout(c.client.indoor_base)
            },
            "raw_dump": raw,
        },
        "cop": {
            "heat_w": c.heat_w,
            "power_w": c.power_w,
            "cop": c.cop,
            "energy": c.energy.as_dict(),
        },
    }
