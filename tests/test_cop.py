from datetime import datetime, timedelta

import pytest

from conftest import cop as C


def test_heat_output():
    # 15 L/min, dT 5 K -> 0.25 kg/s * 4.18 * 5 = 5.225 kW
    assert C.heat_output_w(15, 5) == pytest.approx(5225, abs=1)
    assert C.heat_output_w(None, 5) is None
    assert C.heat_output_w(0, 5) == 0.0


def test_instant_cop():
    assert C.instant_cop(5225, 1500) == pytest.approx(3.48, abs=0.01)
    assert C.instant_cop(5000, 50) is None  # standby
    assert C.instant_cop(100000, 1000) == C.MAX_COP


def test_tracker_integrates_and_rolls_over():
    t = C.EnergyTracker()
    start = datetime(2026, 11, 15, 22, 59)
    for i in range(61):  # one hour, 1-minute steps, ends 23:59
        t.update(start + timedelta(minutes=i), 4000, 1000)
    assert t.heat_kwh == pytest.approx(4.0)
    assert t.elec_kwh == pytest.approx(1.0)
    assert t.cop_today == pytest.approx(4.0)
    t.update(start + timedelta(minutes=61), 4000, 1000)  # 00:00, new day
    assert t.heat_today_kwh == pytest.approx(4000 * (1 / 60) / 1000)
    assert t.heat_kwh == pytest.approx(4.0 + 4000 / 60 / 1000)


def test_tracker_skips_gaps():
    t = C.EnergyTracker()
    now = datetime(2026, 11, 15, 12, 0)
    t.update(now, 4000, 1000)
    t.update(now + timedelta(hours=2), 4000, 1000)
    assert t.heat_kwh == 0


def test_tracker_roundtrip():
    t = C.EnergyTracker(heat_kwh=10, elec_kwh=3)
    t.update(datetime(2026, 11, 15, 12, 0), 0, 0)
    t2 = C.EnergyTracker.from_dict(t.as_dict())
    assert t2.heat_kwh == 10 and t2.day == t.day
