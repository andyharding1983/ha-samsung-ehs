"""Heat output and COP calculation. Pure logic, no Home Assistant imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

WATER_CP = 4.18  # kJ/(kg*K)
WATER_DENSITY = 1.0  # kg/L

# Below this electrical input the unit is idle/standby; COP is meaningless.
MIN_POWER_W = 100.0
# Cap absurd values caused by sensor lag (e.g. flow still warm after stop).
MAX_COP = 10.0


def heat_output_w(flow_lpm: float | None, delta_t: float | None) -> float | None:
    """Thermal power in watts from flow rate (L/min) and flow-return delta T."""
    if flow_lpm is None or delta_t is None:
        return None
    if flow_lpm <= 0:
        return 0.0
    kg_s = flow_lpm * WATER_DENSITY / 60.0
    return round(kg_s * WATER_CP * delta_t * 1000.0, 1)


def instant_cop(heat_w: float | None, power_w: float | None) -> float | None:
    """Instantaneous COP, or None when not meaningful."""
    if heat_w is None or power_w is None or power_w < MIN_POWER_W:
        return None
    if heat_w <= 0:
        return 0.0
    return round(min(heat_w / power_w, MAX_COP), 2)


@dataclass
class EnergyTracker:
    """Integrates heat and electrical power into energy (trapezoidal rule).

    Keeps lifetime totals plus today's counters, rolling over at local
    midnight so a daily COP can be derived.
    """

    heat_kwh: float = 0.0
    elec_kwh: float = 0.0
    heat_today_kwh: float = 0.0
    elec_today_kwh: float = 0.0
    day: date | None = None
    _last_ts: datetime | None = field(default=None, repr=False)
    _last_heat_w: float | None = field(default=None, repr=False)
    _last_elec_w: float | None = field(default=None, repr=False)

    # Ignore gaps longer than this (e.g. HA restart): don't integrate across.
    max_gap_s: float = 600.0

    def update(self, now: datetime, heat_w: float | None, elec_w: float | None) -> None:
        if self.day is None:
            self.day = now.date()
        elif now.date() != self.day:
            self.day = now.date()
            self.heat_today_kwh = 0.0
            self.elec_today_kwh = 0.0

        if self._last_ts is not None:
            dt = (now - self._last_ts).total_seconds()
            if 0 < dt <= self.max_gap_s:
                hours = dt / 3600.0
                if heat_w is not None and self._last_heat_w is not None:
                    # Negative heat (defrost) is counted: it is real energy
                    # taken back from the system and lowers the true COP.
                    e = (heat_w + self._last_heat_w) / 2 * hours / 1000.0
                    self.heat_kwh += e
                    self.heat_today_kwh += e
                if elec_w is not None and self._last_elec_w is not None:
                    e = max(0.0, (elec_w + self._last_elec_w) / 2) * hours / 1000.0
                    self.elec_kwh += e
                    self.elec_today_kwh += e

        self._last_ts = now
        self._last_heat_w = heat_w
        self._last_elec_w = elec_w

    @property
    def cop_today(self) -> float | None:
        if self.elec_today_kwh < 0.05:
            return None
        return round(max(self.heat_today_kwh, 0.0) / self.elec_today_kwh, 2)

    @property
    def cop_lifetime(self) -> float | None:
        if self.elec_kwh < 0.05:
            return None
        return round(max(self.heat_kwh, 0.0) / self.elec_kwh, 2)

    def as_dict(self) -> dict:
        return {
            "heat_kwh": self.heat_kwh,
            "elec_kwh": self.elec_kwh,
            "heat_today_kwh": self.heat_today_kwh,
            "elec_today_kwh": self.elec_today_kwh,
            "day": self.day.isoformat() if self.day else None,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> EnergyTracker:
        if not data:
            return cls()
        day = data.get("day")
        return cls(
            heat_kwh=float(data.get("heat_kwh", 0.0)),
            elec_kwh=float(data.get("elec_kwh", 0.0)),
            heat_today_kwh=float(data.get("heat_today_kwh", 0.0)),
            elec_today_kwh=float(data.get("elec_today_kwh", 0.0)),
            day=date.fromisoformat(day) if day else None,
        )
