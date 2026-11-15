"""Room thermostat (hysteresis + anti-short-cycle) and adaptive curve offset.

Pure logic, no Home Assistant imports.

Thermostat: heating is called for until the room reaches target + hysteresis,
then paused until the room falls back to target. The compressor is protected
by a minimum on time (and optional minimum off time); with a 20 min minimum
on time the unit can start at most 3 times per hour.

Adaptive offset: a slow integral term that shifts the whole heating curve so
the room settles on target instead of a little above/below it.
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
class AdaptiveOffset:
    """Integral term on room error, applied as a curve offset (K)."""

    value: float = 0.0
    rate: float = 0.5  # K offset per hour per K of room error
    limit: float = 5.0

    def update(
        self,
        dt_s: float,
        room: float | None,
        target: float,
        learning: bool,
        limited: int = 0,
    ) -> float:
        """Integrate room error.

        `limited` is +1 when the flow is already held at its maximum and -1 at
        its minimum; learning further in that direction would only wind up
        an offset that later causes overshoot, so it is blocked.
        """
        if learning and room is not None and self.rate > 0 and 0 < dt_s <= 3600:
            err = target - room
            if (err > 0 and limited > 0) or (err < 0 and limited < 0):
                return round(self.value, 2)
            self.value += self.rate * err * dt_s / 3600.0
            self.value = max(-self.limit, min(self.limit, self.value))
        return round(self.value, 2)
