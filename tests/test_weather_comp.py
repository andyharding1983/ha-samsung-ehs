import pytest

from conftest import weather_comp as W

P = W.WcParams()  # -3 -> 45, 15 -> 25


def test_curve_endpoints_and_clamp():
    assert W.curve_flow(P, -3) == pytest.approx(45)
    assert W.curve_flow(P, 15) == pytest.approx(25)
    assert W.curve_flow(P, 6) == pytest.approx(35)
    assert W.curve_flow(P, -20) == P.flow_max or W.curve_flow(P, -20) <= P.flow_max
    assert W.curve_flow(P, 30) == P.flow_min


def test_forecast_blend():
    eff, mean = W.effective_outdoor(P, 10.0, [0.0, 2.0])
    assert mean == 1.0
    assert eff == pytest.approx(0.7 * 10 + 0.3 * 1)


def test_no_forecast():
    eff, mean = W.effective_outdoor(P, 5.0, None)
    assert (eff, mean) == (5.0, None)


def test_room_offset_clamped():
    assert W.room_offset(P, 20.0) == 3.0  # 1 K cold * gain 3
    assert W.room_offset(P, 15.0) == 5.0  # clamped
    assert W.room_offset(P, 22.0) == -3.0
    assert W.room_offset(P, None) == 0.0


def test_compute_full():
    r = W.compute(P, 6.0, None, 20.5)  # curve 35 + 1.5
    assert r.flow_target == 36.5
    assert r.heating_needed


def test_cutoff():
    r = W.compute(P, 18.0)
    assert not r.heating_needed
    assert r.flow_target == P.flow_min


def test_cold_forecast_raises_flow():
    warm = W.compute(P, 8.0, [8.0] * 6)
    cold = W.compute(P, 8.0, [0.0] * 6)
    assert cold.flow_target > warm.flow_target


def test_round_half():
    assert W.round_half(36.26) == 36.5
    assert W.round_half(36.2) == 36.0


def test_limited_flag():
    p = W.WcParams(flow_max=45.0, room_gain=3.0, room_max_offset=5.0)
    assert W.compute(p, -10.0, None, 18.0).limited == 1
    assert W.compute(p, 5.0, None, 21.0).limited == 0
    assert W.compute(W.WcParams(flow_min=30.0), 14.0, None, 23.0).limited == -1
