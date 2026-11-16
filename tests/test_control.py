from conftest import _load

C = _load("control")

MIN = 60.0


def test_pauses_at_target_plus_hysteresis_after_min_on():
    t = C.Thermostat(hysteresis=1.0, min_on_s=20 * MIN)
    t.last_change = 0
    assert t.update(5 * MIN, 22.0, 21.0) is True  # satisfied but min-on not met
    assert t.update(20 * MIN, 21.9, 21.0) is True  # below target + 1
    assert t.update(21 * MIN, 22.0, 21.0) is False  # paused


def test_resumes_at_target_with_full_drop():
    t = C.Thermostat(min_on_s=0, calling=False, last_change=0, restart_drop=1.0)
    assert t.update(MIN, 21.5, 21.0) is False  # between target and target+1
    assert t.update(2 * MIN, 21.0, 21.0) is True


def test_default_restarts_half_way_back():
    t = C.Thermostat(min_on_s=0, calling=False, last_change=0)  # drop 0.5
    assert t.update(MIN, 21.6, 21.0) is False
    assert t.update(2 * MIN, 21.5, 21.0) is True  # target + 1 - 0.5


def test_start_counter():
    from datetime import datetime, timedelta

    sc = C.StartCounter()
    t0 = datetime(2026, 11, 16, 12, 0)
    sc.update(t0, True)  # first reading only sets the baseline
    assert sc.today == 0
    sc.update(t0 + timedelta(minutes=1), False)
    sc.update(t0 + timedelta(minutes=2), None)  # unknown: no change
    sc.update(t0 + timedelta(minutes=3), True)
    assert (sc.today, sc.total) == (1, 1)
    sc.update(t0 + timedelta(days=1), True)  # new day resets today only
    assert (sc.today, sc.total) == (0, 1)
    again = C.StartCounter.from_dict(sc.as_dict())
    assert (again.today, again.total, again.day) == (0, 1, sc.day)


def test_min_on_20_caps_three_starts_per_hour():
    t = C.Thermostat(min_on_s=20 * MIN, min_off_s=0)
    t.last_change = -1e9
    starts, prev = 0, t.calling
    # Room swings wildly every minute; count starts over one hour
    for m in range(60):
        room = 25.0 if m % 2 else 15.0
        now_calling = t.update(m * MIN, room, 21.0)
        if now_calling and not prev:
            starts += 1
        prev = now_calling
    assert starts <= 3


def test_min_off_respected():
    t = C.Thermostat(min_on_s=0, min_off_s=10 * MIN, calling=False, last_change=0)
    assert t.update(5 * MIN, 18.0, 21.0) is False
    assert t.update(10 * MIN, 18.0, 21.0) is True


def test_missing_room_never_pauses_and_restarts():
    t = C.Thermostat(min_on_s=0)
    t.last_change = 0
    assert t.update(MIN, None, 21.0) is True
    t2 = C.Thermostat(min_on_s=0, calling=False, last_change=0)
    assert t2.update(MIN, None, 21.0) is True


def test_warm_weather_stops_heating():
    t = C.Thermostat(min_on_s=0)
    t.last_change = 0
    assert t.update(MIN, 19.0, 21.0, heating_needed=False) is False
    assert t.update(2 * MIN, 19.0, 21.0, heating_needed=False) is False


def test_adaptive_cold_end_learns_in_cold_weather():
    a = C.AdaptiveCurve(rate=0.5, limit=5.0)
    a.update(3600, 20.0, 21.0, w_design=1.0, learning=True)  # at design temp, 1 K cold
    assert (a.design, a.mild) == (0.5, 0.0)
    a.update(3600, 22.0, 21.0, w_design=0.0, learning=True)  # mild day, 1 K warm
    assert (a.design, a.mild) == (0.5, -0.5)  # slope steepened


def test_adaptive_midpoint_learns_at_full_rate():
    a = C.AdaptiveCurve(rate=0.5)
    a.update(3600, 20.0, 21.0, w_design=0.5, learning=True)
    assert a.offset_at(0.5) == 0.5  # effective change equals the rate
    assert a.design == a.mild == 0.5


def test_adaptive_paused_and_clamped():
    a = C.AdaptiveCurve(rate=1.0, limit=2.0)
    a.update(3600, 20.0, 21.0, w_design=1.0, learning=False)
    a.update(3600, None, 21.0, w_design=1.0, learning=True)
    a.update(3600, 20.0, 21.0, w_design=None, learning=True)
    assert (a.design, a.mild) == (0.0, 0.0)
    for _ in range(10):
        a.update(3600, 15.0, 21.0, w_design=1.0, learning=True)
    assert a.design == 2.0


def test_adaptive_anti_windup():
    a = C.AdaptiveCurve(rate=1.0, limit=5.0)
    a.update(3600, 20.0, 21.0, w_design=1.0, learning=True, limited=1)
    assert a.design == 0.0
    a.update(3600, 22.0, 21.0, w_design=1.0, learning=True, limited=1)
    assert a.design == -1.0
    a.update(3600, 22.0, 21.0, w_design=1.0, learning=True, limited=-1)
    assert a.design == -1.0
