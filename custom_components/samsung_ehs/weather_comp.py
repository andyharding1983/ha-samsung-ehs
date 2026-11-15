"""Weather compensation with forecast blending and room influence.

Pure logic, no Home Assistant imports, so it can be unit-tested directly.

flow = curve(effective_outdoor) + clamp(gain * (room_target - room_temp))

* curve: straight line through (outdoor_design, flow_design) and
  (outdoor_mild, flow_mild), clamped to [flow_min, flow_max].
* effective_outdoor: blend of the current outdoor temperature and the mean
  forecast over the next N hours, so the house starts warming up before a
  cold snap arrives (and backs off before a warm spell). This compensates
  for the thermal lag of underfloor heating.
* room influence: proportional correction from a reference room sensor.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean


@dataclass(frozen=True)
class WcParams:
    """Weather compensation tuning parameters."""

    outdoor_design: float = -3.0
    flow_design: float = 45.0
    outdoor_mild: float = 15.0
    flow_mild: float = 25.0
    room_target: float = 21.0
    room_gain: float = 3.0
    room_max_offset: float = 5.0
    forecast_hours: float = 6.0
    forecast_weight: float = 0.3
    flow_min: float = 20.0
    flow_max: float = 55.0
    heating_cutoff: float = 17.0


@dataclass(frozen=True)
class WcResult:
    """Outcome of one weather-compensation calculation."""

    flow_target: float
    curve_flow: float
    room_offset: float
    effective_outdoor: float
    forecast_mean: float | None
    heating_needed: bool


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def curve_flow(p: WcParams, outdoor: float) -> float:
    """Flow temperature from the heating curve alone."""
    span = p.outdoor_mild - p.outdoor_design
    if span == 0:
        return _clamp(p.flow_design, p.flow_min, p.flow_max)
    slope = (p.flow_mild - p.flow_design) / span
    flow = p.flow_design + slope * (outdoor - p.outdoor_design)
    return _clamp(flow, p.flow_min, p.flow_max)


def effective_outdoor(
    p: WcParams, outdoor_now: float, forecast: list[float] | None
) -> tuple[float, float | None]:
    """Blend current outdoor temp with the forecast mean."""
    if not forecast or p.forecast_weight <= 0:
        return outdoor_now, None
    mean = fmean(forecast)
    w = _clamp(p.forecast_weight, 0.0, 1.0)
    return round((1 - w) * outdoor_now + w * mean, 2), round(mean, 2)


def room_offset(p: WcParams, room_temp: float | None) -> float:
    """Proportional room correction, clamped to +/- room_max_offset."""
    if room_temp is None or p.room_gain == 0:
        return 0.0
    off = p.room_gain * (p.room_target - room_temp)
    return round(_clamp(off, -p.room_max_offset, p.room_max_offset), 2)


def round_half(val: float) -> float:
    """Round to the nearest 0.5 K to keep setpoint writes infrequent."""
    return round(val * 2) / 2


def compute(
    p: WcParams,
    outdoor_now: float,
    forecast: list[float] | None = None,
    room_temp: float | None = None,
) -> WcResult:
    """Calculate the flow temperature target."""
    eff, fmean_ = effective_outdoor(p, outdoor_now, forecast)
    if eff >= p.heating_cutoff:
        return WcResult(
            flow_target=round_half(p.flow_min),
            curve_flow=p.flow_min,
            room_offset=0.0,
            effective_outdoor=eff,
            forecast_mean=fmean_,
            heating_needed=False,
        )
    base = curve_flow(p, eff)
    off = room_offset(p, room_temp)
    flow = _clamp(base + off, p.flow_min, p.flow_max)
    return WcResult(
        flow_target=round_half(flow),
        curve_flow=round(base, 2),
        room_offset=off,
        effective_outdoor=eff,
        forecast_mean=fmean_,
        heating_needed=True,
    )
