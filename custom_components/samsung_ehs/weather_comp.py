"""Weather compensation with forecast blending and room influence.

Pure logic, no Home Assistant imports, so it can be unit-tested directly.

flow = curve(effective_outdoor) + learned(effective_outdoor)
       + clamp(gain * (room_target - room_temp)) + wind boost

* curve: straight line through (outdoor_design, flow_design) and
  (outdoor_mild, flow_mild), clamped to [flow_min, flow_max].
* effective_outdoor: blend of the current outdoor temperature and the mean
  forecast over the next N hours, so the house starts warming up before a
  cold snap arrives (and backs off before a warm spell). This compensates
  for the thermal lag of underfloor heating.
* learned: blend of the corrections learned at each curve end (control.py),
  so learning can change the slope as well as the level.
* room influence: proportional correction from a reference room sensor.
* wind boost: extra flow when the wind (now + forecast) is above a
  threshold, for the extra heat loss from draughts and wind-chilled walls.
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
    wind_gain: float = 0.0  # K flow per m/s above wind_threshold
    wind_threshold: float = 5.0  # m/s


@dataclass(frozen=True)
class WcResult:
    """Outcome of one weather-compensation calculation."""

    flow_target: float
    curve_flow: float
    room_offset: float
    curve_offset: float
    effective_outdoor: float
    forecast_mean: float | None
    heating_needed: bool
    limited: int = 0
    design_weight: float = 0.5  # share of the cold curve end at effective_outdoor
    wind_offset: float = 0.0  # K added for wind
    wind_speed: float | None = None  # m/s used for wind_offset


WIND_MAX_OFFSET = 3.0  # K


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


def wind_offset(p: WcParams, wind_ms: float | None) -> float:
    """Extra flow for wind above the threshold, capped at WIND_MAX_OFFSET."""
    if wind_ms is None or p.wind_gain <= 0:
        return 0.0
    return round(_clamp(p.wind_gain * (wind_ms - p.wind_threshold), 0.0, WIND_MAX_OFFSET), 2)


def design_weight(p: WcParams, eff_outdoor: float) -> float:
    """Share of the cold (design) curve end at this outdoor temperature."""
    span = p.outdoor_mild - p.outdoor_design
    if span <= 0:
        return 0.5
    return max(0.0, min(1.0, (p.outdoor_mild - eff_outdoor) / span))


def curve_points(
    p: WcParams,
    offset_design: float = 0.0,
    offset_mild: float = 0.0,
    step: float = 1.0,
    margin: float = 10.0,
) -> list[dict[str, float]]:
    """Sample the set and learned curves for plotting.

    Runs from `margin` below the design temperature up to the heating
    cut-off; both curves are clamped to the flow limits as in `compute`.
    """
    out: list[dict[str, float]] = []
    lo, hi = p.outdoor_design - margin, p.heating_cutoff
    n = max(1, int(round((hi - lo) / step)))
    for i in range(n + 1):
        t = round(min(hi, lo + i * step), 1)
        base = curve_flow(p, t)
        w = design_weight(p, t)
        learned = _clamp(
            base + w * offset_design + (1 - w) * offset_mild, p.flow_min, p.flow_max
        )
        out.append({"outdoor": t, "set": round(base, 1), "learned": round(learned, 1)})
    return out


def round_half(val: float) -> float:
    """Round to the nearest 0.5 K to keep setpoint writes infrequent."""
    return round(val * 2) / 2


def compute(
    p: WcParams,
    outdoor_now: float,
    forecast: list[float] | None = None,
    room_temp: float | None = None,
    offset_design: float = 0.0,
    offset_mild: float = 0.0,
    wind: float | None = None,
) -> WcResult:
    """Calculate the flow temperature target.

    offset_design/offset_mild are learned corrections at each end of the
    curve; between the ends they are blended linearly, beyond them the end
    value is held.
    """
    eff, fmean_ = effective_outdoor(p, outdoor_now, forecast)
    w_d = design_weight(p, eff)
    curve_offset = w_d * offset_design + (1 - w_d) * offset_mild
    if eff >= p.heating_cutoff:
        return WcResult(
            flow_target=round_half(p.flow_min),
            curve_flow=p.flow_min,
            room_offset=0.0,
            curve_offset=round(curve_offset, 2),
            effective_outdoor=eff,
            forecast_mean=fmean_,
            heating_needed=False,
            design_weight=w_d,
            wind_speed=wind,
        )
    base = curve_flow(p, eff)
    off = room_offset(p, room_temp)
    w_off = wind_offset(p, wind)
    raw = base + curve_offset + off + w_off
    flow = _clamp(raw, p.flow_min, p.flow_max)
    limited = 1 if raw > p.flow_max else -1 if raw < p.flow_min else 0
    return WcResult(
        flow_target=round_half(flow),
        curve_flow=round(base, 2),
        room_offset=off,
        curve_offset=round(curve_offset, 2),
        effective_outdoor=eff,
        forecast_mean=fmean_,
        heating_needed=True,
        limited=limited,
        design_weight=w_d,
        wind_offset=w_off,
        wind_speed=wind,
    )
