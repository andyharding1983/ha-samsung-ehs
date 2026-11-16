"""A simulated MIM-B19N for trying the integration without hardware.

Enter ``simulator`` as the serial port. The model is deliberately simple but
reacts plausibly to writes: flow temperature chases the setpoint, the
compressor modulates with load, cold-weather defrosts happen, the hot water
tank cools and gets reheated (pausing space heating via the 3-way valve),
and electrical power is derived from a Carnot-fraction COP.

It reads the outdoor temperature from a callable so it can follow a real
weather entity in Home Assistant.
"""

from __future__ import annotations

from collections.abc import Callable
import time

from . import registers as R

SIM_PORT = "simulator"

FLOW_RATE_LPM = 15.0
TAU_FLOW_RUN = 8.0  # minutes for flow temp to approach setpoint
TAU_FLOW_IDLE = 30.0
DEFROST_EVERY = 50.0  # minutes of running below DEFROST_BELOW
DEFROST_FOR = 4.0
DEFROST_BELOW = 6.0
TANK_LOSS_K_PER_MIN = 0.5 / 60
TANK_HEAT_K_PER_MIN = 0.25
DHW_HYSTERESIS = 5.0
STANDBY_W = 25.0


class SimulatedClient:
    """Drop-in replacement for EhsClient."""

    def __init__(
        self,
        outdoor_fn: Callable[[], float | None],
        indoor_index: int = 0,
        clock: Callable[[], float] = time.monotonic,
        hot_water: bool = True,
    ) -> None:
        self._outdoor_fn = outdoor_fn
        self._clock = clock
        self._base = R.indoor_base(indoor_index)
        self._regs: dict[int, int] = {}
        self._last = clock()
        self._run_min = 0.0
        self._defrost_left = 0.0
        self._dhw_heating = False
        self.power_w = STANDBY_W
        b = self._base
        self._set(b + R.IU_COMM_STATUS, 0b0111)
        self._set(b + R.IU_MODEL, 0x0073)
        self._set(b + R.IU_POWER, 1)
        self._set(b + R.IU_MODE, int(R.Mode.HEAT))
        self._set_t(b + R.IU_SET_TEMP, 21.0)
        self._set_t(b + R.IU_ROOM_TEMP, 20.5)
        self._set_t(b + R.IU_WATER_OUT, 30.0)
        self._set_t(b + R.IU_WATER_IN, 28.0)
        self._set_t(b + R.IU_WATER_OUT_SET, 35.0)
        self._set(b + R.IU_DHW_POWER, 1 if hot_water else 0)
        self._set(b + R.IU_DHW_MODE, int(R.DhwMode.STANDARD))
        self._set_t(b + R.IU_DHW_SET, 48.0)
        self._set_t(b + R.IU_DHW_TEMP, 46.0)

    # --- register helpers -------------------------------------------------
    def _set(self, addr: int, raw: int) -> None:
        self._regs[addr] = raw & 0xFFFF

    def _set_t(self, addr: int, val: float) -> None:
        self._set(addr, R.encode_temp(val))

    def _t(self, offset: int) -> float:
        return R.decode_temp(self._regs.get(self._base + offset, 0)) or 0.0

    def _i(self, offset: int) -> int:
        return self._regs.get(self._base + offset, 0)

    # --- EhsClient interface ---------------------------------------------
    @property
    def indoor_base(self) -> int:
        return self._base

    async def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def write(self, address: int, value: int) -> None:
        self._set(address, value)

    async def write_indoor(self, offset: int, value: int) -> None:
        self._set(self._base + offset, value)

    async def enable_extended(self) -> None:
        return None

    async def read_all(self, extended: bool) -> R.EhsData:
        self.step()
        module = [self._regs.get(a, 0) for a in range(R.MODULE_BLOCK[1])]
        indoor = [self._regs.get(self._base + i, 0) for i in range(R.INDOOR_BLOCK_LEN)]
        ext = None
        if extended:
            ext = [self._regs.get(a, 0) for a, _ in R.extended_layout(self._base)]
        data = R.decode(module, indoor, ext)
        if data.extended is not None:
            data.extended["sim_power"] = round(self.power_w, 1)
        return data

    async def dump(self, start: int = 0, end: int = 130, chunk: int = 10) -> dict:
        return {
            "registers": {a: self._regs.get(a, 0) for a in range(start, end + 1)},
            "errors": {},
        }

    # --- physics ------------------------------------------------------------
    def step(self) -> None:
        now = self._clock()
        dt_min = min((now - self._last) / 60.0, 10.0)
        self._last = now
        if dt_min <= 0:
            return
        outdoor = self._outdoor_fn()
        if outdoor is None:
            outdoor = 7.0
        b = self._base

        flow = self._t(R.IU_WATER_OUT)
        tank = self._t(R.IU_DHW_TEMP)
        dhw_set = self._t(R.IU_DHW_SET)
        flow_set = self._t(R.IU_WATER_OUT_SET)
        heat_on = self._i(R.IU_POWER) == 1 and self._i(R.IU_MODE) == R.Mode.HEAT

        # Hot water has priority over space heating (3-way valve).
        tank -= TANK_LOSS_K_PER_MIN * dt_min
        if self._i(R.IU_DHW_POWER) == 1:
            if tank < dhw_set - DHW_HYSTERESIS:
                self._dhw_heating = True
            elif tank >= dhw_set:
                self._dhw_heating = False
        else:
            self._dhw_heating = False

        if self._dhw_heating:
            target = dhw_set + 5.0
            running = True
        elif heat_on and flow_set > 22.0 and outdoor < 18.0:
            target = flow_set
            running = True
        else:
            target = None
            running = False

        defrost = False
        if running and outdoor < DEFROST_BELOW:
            if self._defrost_left > 0:
                self._defrost_left -= dt_min
                defrost = True
            else:
                self._run_min += dt_min
                if self._run_min >= DEFROST_EVERY:
                    self._run_min = 0.0
                    self._defrost_left = DEFROST_FOR
                    defrost = True

        if defrost:
            flow -= 1.5 * dt_min
            delta = -3.0
            freq = 60
            heat_w = FLOW_RATE_LPM / 60 * 4.18 * delta * 1000
            self.power_w = 1800.0
        elif running and target is not None:
            flow += (target - flow) * min(1.0, dt_min / TAU_FLOW_RUN)
            delta = max(2.0, min(7.0, (target - outdoor) / 7.0))
            freq = int(max(20, min(90, 15 + 2.0 * (target - outdoor))))
            heat_w = FLOW_RATE_LPM / 60 * 4.18 * delta * 1000
            cop = 0.5 * (flow + 273.15) / max(flow - outdoor + 8.0, 5.0)
            self.power_w = heat_w / max(cop, 1.0) + STANDBY_W
            if self._dhw_heating:
                tank += TANK_HEAT_K_PER_MIN * dt_min
        else:
            flow += (22.0 - flow) * min(1.0, dt_min / TAU_FLOW_IDLE)
            delta = 0.0
            freq = 0
            self.power_w = STANDBY_W

        pumping = running or defrost
        self._set_t(b + R.IU_WATER_OUT, round(flow, 1))
        self._set_t(b + R.IU_WATER_IN, round(flow - delta, 1))
        self._set_t(b + R.IU_DHW_TEMP, round(tank, 1))
        self._set(R.REG_DEFROST, 1 if defrost else 0)
        ext = dict(
            outdoor_temp=R.encode_temp(outdoor),
            compressor_freq=freq,
            flow_rate=int(FLOW_RATE_LPM * 10) if pumping else 0,
            dhw_valve=1 if self._dhw_heating else 0,
        )
        for addr, val in R.extended_layout(self._base):
            self._set(addr, ext.get(val.key, 0))
