import pytest

from conftest import registers as R


def test_indoor_base():
    assert R.indoor_base(0) == 50
    assert R.indoor_base(1) == 100
    with pytest.raises(ValueError):
        R.indoor_base(48)


def test_absolute_addresses_match_manual():
    # Addresses known to work on a real EHS (PDU, unit 0)
    base = R.indoor_base(0)
    assert base + R.IU_POWER == 52
    assert base + R.IU_WATER_IN == 65
    assert base + R.IU_WATER_OUT == 66
    assert base + R.IU_WATER_OUT_SET == 68
    assert base + R.IU_DHW_POWER == 72
    assert base + R.IU_DHW_TEMP == 75
    assert base + R.IU_QUIET == 78
    assert base + R.IU_AWAY == 79


def test_temp_codec():
    assert R.decode_temp(235) == 23.5
    assert R.decode_temp(0xFFF6) == -1.0
    assert R.decode_temp(0x8000) is None
    assert R.encode_temp(-1.0) == 0xFFF6
    assert R.encode_temp(42.5) == 425


def test_defrost():
    assert R.decode_defrost(0) is False
    assert R.decode_defrost(0xFF) is False
    assert R.decode_defrost(1) is True


def test_decode_snapshot():
    module = [0, 0, 0]
    indoor = [0] * R.INDOOR_BLOCK_LEN
    indoor[R.IU_POWER] = 1
    indoor[R.IU_MODE] = 4
    indoor[R.IU_WATER_IN] = 300
    indoor[R.IU_WATER_OUT] = 350
    indoor[R.IU_WATER_OUT_SET] = 360
    indoor[R.IU_DHW_MODE] = 1
    indoor[R.IU_QUIET] = 1
    ext = [0xFFE2, 45, 152]  # -3.0 C, 45 Hz, 15.2 L/min
    d = R.decode(module, indoor, ext)
    assert d.power and d.mode == R.Mode.HEAT
    assert d.delta_t == 5.0
    assert d.quiet and not d.away
    assert d.extended == {"outdoor_temp": -3.0, "compressor_freq": 45, "flow_rate": 15.2}


def test_extended_layout_order():
    keys = [(a, v.key) for a, v in R.extended_layout()]
    assert keys == [(5, "outdoor_temp"), (6, "compressor_freq"), (7, "flow_rate")]


def test_short_read_rejected():
    with pytest.raises(ValueError):
        R.decode([0, 0, 0], [0] * 5, None)
