"""Async Modbus RTU client for the Samsung MIM-B19N."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from pymodbus.client import AsyncModbusSerialClient
from pymodbus.exceptions import ModbusException

from . import registers as R

_LOGGER = logging.getLogger(__name__)

# The MIM-B19N is slow; give it a moment between requests.
INTER_REQUEST_DELAY = 0.05


class EhsConnectionError(Exception):
    """Raised when the MIM-B19N cannot be reached or returns an error."""


class EhsClient:
    """Thin, serialised wrapper around pymodbus for one MIM-B19N."""

    def __init__(
        self, port: str, slave: int, baudrate: int = 9600, indoor_index: int = 0
    ) -> None:
        self._client = AsyncModbusSerialClient(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity="E",
            stopbits=1,
            timeout=3,
            retries=2,
        )
        self._slave = slave
        self._base = R.indoor_base(indoor_index)
        self._lock = asyncio.Lock()
        # pymodbus renamed `slave=` to `device_id=` in 3.10.
        params = inspect.signature(self._client.read_holding_registers).parameters
        self._unit_kw = "device_id" if "device_id" in params else "slave"
        self._extended_enabled = False

    @property
    def indoor_base(self) -> int:
        return self._base

    async def connect(self) -> None:
        if self._client.connected:
            return
        ok = await self._client.connect()
        if not ok:
            raise EhsConnectionError("could not open serial port")

    def close(self) -> None:
        self._client.close()

    async def _read(self, address: int, count: int) -> list[int]:
        await self.connect()
        try:
            rr = await self._client.read_holding_registers(
                address, count=count, **{self._unit_kw: self._slave}
            )
        except ModbusException as err:
            raise EhsConnectionError(str(err)) from err
        if rr.isError():
            raise EhsConnectionError(f"read {address}+{count} failed: {rr}")
        await asyncio.sleep(INTER_REQUEST_DELAY)
        return list(rr.registers)

    async def dump(self, start: int = 0, end: int = 130, chunk: int = 10) -> dict[str, Any]:
        """Read a raw register range for diagnostics.

        Reads in chunks; if a chunk is refused (e.g. it spans an unmapped
        address) its registers are retried one by one so the rest still show.
        """
        regs: dict[int, int] = {}
        errors: dict[int, str] = {}
        async with self._lock:
            for addr in range(start, end + 1, chunk):
                count = min(chunk, end + 1 - addr)
                try:
                    for i, val in enumerate(await self._read(addr, count)):
                        regs[addr + i] = val
                    continue
                except EhsConnectionError:
                    pass
                for a in range(addr, addr + count):
                    try:
                        regs[a] = (await self._read(a, 1))[0]
                    except EhsConnectionError as err:
                        errors[a] = str(err)
        return {"registers": regs, "errors": errors}

    async def write(self, address: int, value: int) -> None:
        """Write one holding register."""
        async with self._lock:
            await self.connect()
            try:
                rr = await self._client.write_register(
                    address, value & 0xFFFF, **{self._unit_kw: self._slave}
                )
            except ModbusException as err:
                raise EhsConnectionError(str(err)) from err
            if rr.isError():
                raise EhsConnectionError(f"write {address}={value} failed: {rr}")
            await asyncio.sleep(INTER_REQUEST_DELAY)

    async def write_indoor(self, offset: int, value: int) -> None:
        await self.write(self._base + offset, value)

    async def enable_extended(self) -> None:
        """Register the NASA IDs we want exposed in the extended area."""
        async with self._lock:
            await self.connect()
            for addr, vals in (
                (R.EXT_OUTDOOR_ENABLE, [v.nasa_id for v in R.EXT_OUTDOOR]),
                (R.EXT_INDOOR_ENABLE, [v.nasa_id for v in R.EXT_INDOOR]),
            ):
                if not vals:
                    continue
                rr = await self._client.write_registers(
                    addr, vals, **{self._unit_kw: self._slave}
                )
                if rr.isError():
                    raise EhsConnectionError(f"extended enable at {addr} failed: {rr}")
                await asyncio.sleep(INTER_REQUEST_DELAY)
            self._extended_enabled = True

    async def read_all(self, extended: bool) -> R.EhsData:
        """Read and decode everything in as few requests as possible."""
        async with self._lock:
            try:
                module = await self._read(*R.MODULE_BLOCK)
                indoor = await self._read(self._base, R.INDOOR_BLOCK_LEN)
            except EhsConnectionError:
                # The module forgets the extended mapping on power loss;
                # re-send it once comms recover.
                self._extended_enabled = False
                raise
            ext = None
            if extended and self._extended_enabled:
                n = len(R.extended_layout())
                try:
                    ext = await self._read(R.EXT_READ_START, n)
                except EhsConnectionError as err:
                    _LOGGER.debug("extended read failed: %s", err)
        if extended and not self._extended_enabled:
            try:
                await self.enable_extended()
            except EhsConnectionError as err:
                _LOGGER.warning("could not enable extended registers: %s", err)
        return R.decode(module, indoor, ext)
