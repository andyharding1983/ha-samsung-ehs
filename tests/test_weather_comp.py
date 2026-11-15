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


def test_end_offsets_blend_and_hold():
    p = W.WcParams()  # -3 -> 45, 15 -> 25
    assert W.compute(p, -3.0, offset_design=2.0, offset_mild=-1.0).curve_offset == 2.0
    assert W.compute(p, 15.0 - 0.01, offset_design=2.0, offset_mild=-1.0).curve_offset == -1.0
    assert W.compute(p, 6.0, offset_design=2.0, offset_mild=-1.0).curve_offset == 0.5
    assert W.compute(p, -15.0, offset_design=2.0, offset_mild=-1.0).curve_offset == 2.0  # held
    assert W.compute(p, 6.0).design_weight == 0.5


def test_curve_points_follow_curve_and_offsets():
    p = W.WcParams(flow_design=40, flow_mild=25, flow_min=25, flow_max=45)
    pts = W.curve_points(p, offset_design=2.0, offset_mild=0.0, margin=5.0)
    by_t = {pt["outdoor"]: pt for pt in pts}
    assert by_t[-3.0] == {"outdoor": -3.0, "set": 40.0, "learned": 42.0}
    assert by_t[15.0] == {"outdoor": 15.0, "set": 25.0, "learned": 25.0}
    assert by_t[6.0]["learned"] == 33.5  # midway: +1 K
    assert by_t[-8.0]["set"] == 44.2 and by_t[-8.0]["learned"] == 45.0  # clamped
    assert pts[-1]["outdoor"] == p.heating_cutoff


def test_wind_boost():
    p = W.WcParams(wind_gain=0.3, wind_threshold=5.0)
    assert W.wind_offset(p, None) == 0.0
    assert W.wind_offset(p, 4.0) == 0.0
    assert W.wind_offset(p, 10.0) == 1.5
    assert W.wind_offset(p, 40.0) == W.WIND_MAX_OFFSET
    calm = W.compute(p, 5.0, wind=2.0)
    windy = W.compute(p, 5.0, wind=10.0)
    assert windy.wind_offset == 1.5 and windy.wind_speed == 10.0
    assert windy.flow_target - calm.flow_target == pytest.approx(1.5, abs=0.5)
