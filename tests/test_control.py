from conftest import _load

C = _load("control")

MIN = 60.0


def test_pauses_at_target_plus_hysteresis_after_min_on():
    t = C.Thermostat(hysteresis=1.0, min_on_s=20 * MIN)
    t.last_change = 0
    assert t.update(5 * MIN, 22.0, 21.0) is True  # satisfied but min-on not met
    assert t.update(20 * MIN, 21.9, 21.0) is True  # below target + 1
    assert t.update(21 * MIN, 22.0, 21.0) is False  # paused


def test_resumes_at_target():
    t = C.Thermostat(min_on_s=0, calling=False, last_change=0)
    assert t.update(MIN, 21.5, 21.0) is False  # between target and target+1
    assert t.update(2 * MIN, 21.0, 21.0) is True


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


def test_adaptive_integrates_and_clamps():
    a = C.AdaptiveOffset(rate=0.5, limit=2.0)
    a.update(3600, 20.0, 21.0, learning=True)  # 1 K low for 1 h
    assert a.value == 0.5
    a.update(3600, 20.0, 21.0, learning=False)  # paused: no learning
    assert a.value == 0.5
    for _ in range(10):
        a.update(3600, 18.0, 21.0, learning=True)
    assert a.value == 2.0
    a.update(3600, None, 21.0, learning=True)
    assert a.value == 2.0
