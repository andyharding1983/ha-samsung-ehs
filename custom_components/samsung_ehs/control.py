"""Room thermostat (hysteresis + anti-short-cycle) and adaptive curve offset.

Pure logic, no Home Assistant imports.

Thermostat: heating is called for until the room reaches target + hysteresis,
then paused until the room falls back to target. The compressor is protected
by a minimum on time (and optional minimum off time); with a 20 min minimum
on time the unit can start at most 3 times per hour.

Adaptive curve: slow integral learning of a correction at each end of the
heating curve, so both its level and its slope adapt and the room settles on
target across the whole range of outdoor temperatures.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Thermostat:
    """Hysteresis room thermostat with minimum on/off times."""

    hysteresis: float = 1.0
    min_on_s: float = 20 * 60
    min_off_s: float = 0.0
    calling: bool = True
    last_change: float | None = None  # monotonic seconds

    def update(
        self, now: float, room: float | None, target: float, heating_needed: bool = True
    ) -> bool:
        """Return whether heating should run."""
        elapsed = float("inf") if self.last_change is None else now - self.last_change

        if self.calling:
            # Fail-safe: never stop on a missing room reading.
            satisfied = room is not None and room >= target + self.hysteresis
            if (satisfied or not heating_needed) and elapsed >= self.min_on_s:
                self.calling = False
                self.last_change = now
        else:
            cold = room is None or room <= target
            if cold and heating_needed and elapsed >= self.min_off_s:
                self.calling = True
                self.last_change = now
        return self.calling

    def force(self, now: float, calling: bool) -> None:
        """Record an externally caused state change (e.g. user turned off)."""
        if calling != self.calling:
            self.calling = calling
            self.last_change = now


@dataclass
class AdaptiveCurve:
    """Learns a correction at each end of the heating curve.

    The applied offset at any outdoor temperature is the blend of the two end
    offsets, so learning can change the curve's slope as well as its level.
    Room error is credited to the ends in proportion to how close the current
    outdoor temperature is to each, normalised so the effective learning
    speed equals `rate` everywhere.
    """

    design: float = 0.0  # K, at the cold end
    mild: float = 0.0  # K, at the mild end
    rate: float = 0.25  # K per hour per K of room error
    limit: float = 5.0

    def offset_at(self, w_design: float) -> float:
        return round(w_design * self.design + (1 - w_design) * self.mild, 2)

    def update(
        self,
        dt_s: float,
        room: float | None,
        target: float,
        w_design: float | None,
        learning: bool,
        limited: int = 0,
    ) -> None:
        """Integrate room error into the two ends.

        `limited` is +1 when the flow is already held at its maximum and -1 at
        its minimum; learning further in that direction would only wind up an
        offset that later causes overshoot, so it is blocked.
        """
        if not learning or room is None or w_design is None:
            return
        if self.rate <= 0 or not 0 < dt_s <= 3600:
            return
        err = target - room
        if (err > 0 and limited > 0) or (err < 0 and limited < 0):
            return
        w_d, w_m = w_design, 1.0 - w_design
        norm = w_d * w_d + w_m * w_m  # >= 0.5
        step = self.rate * err * dt_s / 3600.0 / norm
        self.design = max(-self.limit, min(self.limit, self.design + step * w_d))
        self.mild = max(-self.limit, min(self.limit, self.mild + step * w_m))

    def reset(self) -> None:
        self.design = 0.0
        self.mild = 0.0
