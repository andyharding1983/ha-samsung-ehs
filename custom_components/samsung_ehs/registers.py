"""Samsung MIM-B19N Modbus register map.

Source: Samsung "Modbus Interface Module MIM-B19N" installation manual
(DB68-07538A), Table 2. Addresses below are Modbus *PDU* addresses
(0-based), i.e. what pymodbus expects.

Indoor-unit registers repeat every 50 addresses: base = 50 + index * 50.
For a single EHS on the MIM-B19N the index is 0, so the base is 50.

This module is deliberately free of Home Assistant imports so the decode
logic can be unit-tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

# --- Module / outdoor registers (absolute PDU addresses) -------------------
REG_MODULE_ERROR = 0  # bitfield: b0 address err, b1 R1/R2 comms err, b2 tracking err
REG_OUTDOOR_ERROR = 1  # 0 = none, 100..999 = error code
REG_DEFROST = 2  # 0 or 0xFF = off, anything else = defrost running
MODULE_BLOCK = (0, 3)  # start, count

# --- Indoor-unit registers (offset from indoor base) -----------------------
IU_COMM_STATUS = 0
IU_MODEL = 1
IU_POWER = 2  # 0 off / 1 on (space heating/cooling)
IU_MODE = 3  # 0 auto, 1 cool, 2 dry, 3 fan, 4 heat
IU_SET_TEMP = 8  # room set temp x10
IU_ROOM_TEMP = 9  # room temp x10 (from the Samsung controller sensor)
IU_ERROR = 13  # 0 none, 100..999 error code
IU_WATER_IN = 15  # return temp x10
IU_WATER_OUT = 16  # flow temp x10
IU_WATER_OUT_SET = 18  # flow setpoint x10 (EHS heat 15..55, cool 5..25)
IU_DHW_POWER = 22  # 0 off / 1 on
IU_DHW_MODE = 23  # 0 eco, 1 standard, 2 power, 3 force
IU_DHW_SET = 24  # x10, EHS 30..70
IU_DHW_TEMP = 25  # x10
IU_QUIET = 28  # 0 off / 1 on
IU_AWAY = 29  # 0 off / 1 on
INDOOR_BLOCK_LEN = 30  # read offsets 0..29 in one request

FLOW_SET_MIN = 15.0
FLOW_SET_MAX = 55.0
DHW_SET_MIN = 30.0
DHW_SET_MAX = 70.0


def indoor_base(index: int) -> int:
    """Return the PDU base address for indoor unit `index` (0..47)."""
    if not 0 <= index <= 47:
        raise ValueError("indoor index must be 0..47")
    return 50 + index * 50


class Mode(IntEnum):
    """Operation mode values (register IU_MODE)."""

    AUTO = 0
    COOL = 1
    DRY = 2
    FAN = 3
    HEAT = 4


class DhwMode(IntEnum):
    """Hot water mode values (register IU_DHW_MODE)."""

    ECO = 0
    STANDARD = 1
    POWER = 2
    FORCE = 3


# --- Extended ("hidden") registers ------------------------------------------
# The MIM-B19N can expose extra NASA values. Writing a list of NASA message
# IDs to 6000.. (outdoor) and 7000.. (indoor) with function code 16 makes
# their values readable from address 5 upwards, outdoor IDs first, then
# indoor IDs, in the order written.
# NOTE: confirmed by community reports and by the user's previous working
# config (5 = outdoor temp, 6 = flow rate), but verify on first live run.
EXT_OUTDOOR_ENABLE = 6000
EXT_INDOOR_ENABLE = 7000
EXT_READ_START = 5


@dataclass(frozen=True)
class ExtendedValue:
    """A NASA value exposed through the extended-register mechanism."""

    key: str
    nasa_id: int
    scale: float
    signed: bool = True


EXT_OUTDOOR: tuple[ExtendedValue, ...] = (
    ExtendedValue("outdoor_temp", 0x8204, 0.1),
    ExtendedValue("compressor_freq", 0x8238, 1.0, signed=False),
)
EXT_INDOOR: tuple[ExtendedValue, ...] = (
    ExtendedValue("flow_rate", 0x42E9, 0.1, signed=False),
)


def extended_layout() -> list[tuple[int, ExtendedValue]]:
    """Return (address, value) pairs for the extended readback area."""
    out: list[tuple[int, ExtendedValue]] = []
    addr = EXT_READ_START
    for val in (*EXT_OUTDOOR, *EXT_INDOOR):
        out.append((addr, val))
        addr += 1
    return out


# --- Decoding helpers --------------------------------------------------------
def to_signed(raw: int) -> int:
    """Interpret a 16-bit register as signed."""
    raw &= 0xFFFF
    return raw - 0x10000 if raw & 0x8000 else raw


def decode_temp(raw: int) -> float | None:
    """Decode a Celsius x10 register; 0x7FFF/0x8000 style sentinels -> None."""
    val = to_signed(raw)
    if val in (-32768, 32767):
        return None
    return round(val / 10.0, 1)


def encode_temp(value: float) -> int:
    """Encode Celsius to a x10 unsigned 16-bit register value."""
    return int(round(value * 10)) & 0xFFFF


def decode_defrost(raw: int) -> bool:
    """Defrost register: 0 or 0xFF means off, anything else means running."""
    return raw not in (0, 0xFF)


def decode_extended(raw: int, val: ExtendedValue) -> float:
    """Decode an extended register value."""
    num = to_signed(raw) if val.signed else raw & 0xFFFF
    return round(num * val.scale, 2)


@dataclass
class EhsData:
    """Decoded snapshot of the heat pump."""

    module_error: int = 0
    outdoor_error: int = 0
    defrost: bool = False
    comm_status: int = 0
    power: bool = False
    mode: int | None = None
    room_set: float | None = None
    room_temp: float | None = None
    indoor_error: int = 0
    water_in: float | None = None
    water_out: float | None = None
    water_out_set: float | None = None
    dhw_power: bool = False
    dhw_mode: int | None = None
    dhw_set: float | None = None
    dhw_temp: float | None = None
    quiet: bool = False
    away: bool = False
    extended: dict[str, float] | None = None

    @property
    def delta_t(self) -> float | None:
        if self.water_out is None or self.water_in is None:
            return None
        return round(self.water_out - self.water_in, 1)


def decode(module: list[int], indoor: list[int], ext: list[int] | None) -> EhsData:
    """Decode raw register blocks into an EhsData snapshot."""
    if len(module) < MODULE_BLOCK[1] or len(indoor) < INDOOR_BLOCK_LEN:
        raise ValueError("short register read")
    d = EhsData(
        module_error=module[REG_MODULE_ERROR],
        outdoor_error=module[REG_OUTDOOR_ERROR],
        defrost=decode_defrost(module[REG_DEFROST]),
        comm_status=indoor[IU_COMM_STATUS],
        power=indoor[IU_POWER] == 1,
        mode=indoor[IU_MODE],
        room_set=decode_temp(indoor[IU_SET_TEMP]),
        room_temp=decode_temp(indoor[IU_ROOM_TEMP]),
        indoor_error=indoor[IU_ERROR],
        water_in=decode_temp(indoor[IU_WATER_IN]),
        water_out=decode_temp(indoor[IU_WATER_OUT]),
        water_out_set=decode_temp(indoor[IU_WATER_OUT_SET]),
        dhw_power=indoor[IU_DHW_POWER] == 1,
        dhw_mode=indoor[IU_DHW_MODE],
        dhw_set=decode_temp(indoor[IU_DHW_SET]),
        dhw_temp=decode_temp(indoor[IU_DHW_TEMP]),
        quiet=indoor[IU_QUIET] == 1,
        away=indoor[IU_AWAY] == 1,
    )
    if ext is not None:
        layout = extended_layout()
        d.extended = {
            val.key: decode_extended(ext[i], val)
            for i, (_addr, val) in enumerate(layout)
            if i < len(ext)
        }
    return d
