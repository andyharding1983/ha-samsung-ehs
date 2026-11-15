"""Simulator behaviour (needs Home Assistant installed for package import)."""

import pytest

pytest.importorskip("homeassistant")

from custom_components.samsung_ehs import registers as R
from custom_components.samsung_ehs.simulator import SimulatedClient


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, minutes):
        self.t += minutes * 60


async def _run(sim, clock, minutes, step=1):
    data = None
    for _ in range(int(minutes / step)):
        clock.advance(step)
        data = await sim.read_all(extended=True)
    return data


async def test_flow_tracks_setpoint():
    clock = Clock()
    sim = SimulatedClient(lambda: 8.0, clock=clock)
    await sim.write_indoor(R.IU_WATER_OUT_SET, R.encode_temp(40.0))
    d = await _run(sim, clock, 60)
    assert d.water_out == pytest.approx(40.0, abs=0.5)
    assert d.extended["compressor_freq"] > 0
    assert d.extended["flow_rate"] == 15.0
    assert 2.0 < d.extended["sim_power"] < 3000


async def test_off_goes_idle():
    clock = Clock()
    sim = SimulatedClient(lambda: 8.0, clock=clock)
    await sim.write_indoor(R.IU_POWER, 0)
    await sim.write_indoor(R.IU_DHW_POWER, 0)
    d = await _run(sim, clock, 30)
    assert d.extended["compressor_freq"] == 0
    assert d.extended["flow_rate"] == 0
    assert not d.power


async def test_cold_weather_defrosts():
    clock = Clock()
    sim = SimulatedClient(lambda: 1.0, clock=clock)
    await sim.write_indoor(R.IU_DHW_POWER, 0)
    seen = False
    for _ in range(70):
        clock.advance(1)
        d = await sim.read_all(extended=True)
        seen |= d.defrost
    assert seen


async def test_dhw_reheat_cycle():
    clock = Clock()
    sim = SimulatedClient(lambda: 10.0, clock=clock)
    await sim.write_indoor(R.IU_DHW_SET, R.encode_temp(55.0))  # tank 46 < 50
    d = await _run(sim, clock, 5)
    assert d.water_out > 35.0  # heading for DHW flow temp
    d = await _run(sim, clock, 60)
    assert d.dhw_temp >= 54.0
