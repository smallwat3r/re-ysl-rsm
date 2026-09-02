"""A simulated Rouge Sur Mesure for developing without the hardware.

    make mock       # the control page against this, no radio needed

`FakeDevice` is the peripheral: its state (cartridges, battery, lid count) and
the reply it gives to each request frame, built to the same layouts
`protocol.py` parses. `_FakeClient` stands in for the bleak connection and
`FakeLink` for the BlueZ scan, so everything above them (`device.Device`,
`app.py`, the page) runs unchanged, on the real frame codec.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NamedTuple

from protocol import (
    MAGIC,
    N_CARTRIDGES,
    NOTIFY_UUID,
    WRITE_UUID,
    Op,
)

# Reply timings, roughly what the real device does.
CONNECT_S = 1.0  # connect + GATT discovery
REPLY_S = 0.05  # a read's round trip
HANDSHAKE_DEADLINE_S = 5.0  # the device hangs up if no handshake by then
BATTERY_TICK_S = 20.0  # unsolicited battery notification period
# pump speed and units->mL are guesses, calibrate against a real dispense
PUMP_UNITS_PER_S = 200.0  # a standard 612-unit shade takes ~3 s
ML_PER_UNIT = 0.001

STATUS_OK = 0x00
STATUS_ERROR = 0x01  # real error codes unknown, one generic code


class Reply(NamedTuple):
    payload: bytes
    status: int = STATUS_OK
    delay: float = REPLY_S


def _response_frame(seq: int, op: int, reply: Reply) -> bytes:
    """Wire form of a reply: AA | seq | opcode | status | len | payload."""
    head = bytes([MAGIC, seq & 0xFF, op, reply.status, len(reply.payload)])
    return head + reply.payload


@dataclass
class Cartridge:
    name: str
    usable_ml: float
    remaining_ml: float
    opened: bool = False
    last_use: int = 0
    lid_count: int = 0


class FakeDevice:
    """The peripheral's state and its answer to each request frame."""

    def __init__(
        self,
        names: tuple[str, ...] = ("VC_220", "MA_527", "MA_200"),
        battery: int = 87,
        usable_ml: float = 5.8,
    ) -> None:
        if len(names) != N_CARTRIDGES:
            raise ValueError(f"need exactly {N_CARTRIDGES} cartridge names")
        self.battery = battery
        self.charging = False
        self.lid_opens = 88
        self.seq = 0  # for unsolicited notifications
        # different wear per slot so the mock page doesn't look cloned
        fractions = (1.0, 0.73, 0.41)
        self.cartridges = [
            Cartridge(name, usable_ml, round(usable_ml * frac, 3), opened=frac < 1.0)
            for name, frac in zip(names, fractions, strict=True)
        ]

    def handle(self, frame: bytes) -> Reply:
        """Answer one request frame (AA seq op len payload)."""
        if len(frame) < 4 or frame[0] != MAGIC:
            return Reply(b"", STATUS_ERROR)
        op, payload = frame[2] & 0x7F, frame[4 : 4 + frame[3]]
        handler = self._HANDLERS.get(op)
        if handler is None:
            return Reply(b"", STATUS_ERROR)
        return handler(self, payload)

    def battery_frame(self) -> bytes:
        """An unsolicited battery notification."""
        self.seq += 1
        return _response_frame(self.seq, Op.BATTERY, self._battery(b""))

    # Handlers, one per opcode, payload layouts mirror protocol._parse_*

    def _handshake(self, _payload: bytes) -> Reply:
        # 03 03 6d00 | lid_opens(u16) 0000 | 12-byte id | 3 x 12-byte cartridge status
        head = b"\x03\x03\x6d\x00" + struct.pack("<H", self.lid_opens) + b"\0\0"
        return Reply(head + bytes(12) + bytes(12 * N_CARTRIDGES))

    def _production(self, _payload: bytes) -> Reply:
        # 3 x 32: 0000 tube 00 | name(8) | u16 usable mL x1000 | u16 shelf days |
        #         batch(8) | u16 made | u16 expires (days since epoch) | u32 crc
        today = int(time.time() // 86400)
        records = [
            struct.pack(
                "<HBB8sHH8sHHI",
                0,
                i,
                0,
                c.name.encode(),
                round(c.usable_ml * 1000),
                730,
                f"62U6{i}0".encode(),  # a batch per slot, the page shows it
                today - 200,
                today + 530,
                0,
            )
            for i, c in enumerate(self.cartridges)
        ]
        return Reply(b"".join(records))

    def _usage(self, _payload: bytes) -> Reply:
        # 3 x 24: 0000 aa tube | u32 devId | u16 opened | u16 ends | u32 last use |
        #         u16 err | u16 lid | u16 remaining mL x1000 | u16 crc16
        # A never-used tube's record is all zeros, like the real one.
        today = int(time.time() // 86400)
        records = [
            (
                struct.pack(
                    "<HBBIHHIHHHH",
                    0,
                    0xAA,
                    i,
                    0xC731BB65,
                    today - 30,
                    today + 335 - 155 * i,  # slot 2 nears its opened-life deadline
                    c.last_use,
                    0,
                    c.lid_count,
                    round(c.remaining_ml * 1000),
                    0,
                )
                if c.opened
                else bytes(24)
            )
            for i, c in enumerate(self.cartridges)
        ]
        return Reply(b"".join(records))

    def _device_info(self, _payload: bytes) -> Reply:
        # null-separated strings: brand, model, serial, _, fw, hw, _, variant
        fields = [
            b"L'Or\xc3\xa9al",
            b"RSM",
            b"00000000",
            b"3",
            b"3.109",
            b"2.12",
            b"",
            b"MOCK",
        ]
        return Reply(b"\0".join(fields) + b"\0")

    def _battery(self, _payload: bytes) -> Reply:
        # bit 7 = charging, low 7 bits = percentage
        return Reply(bytes([(self.battery & 0x7F) | (0x80 if self.charging else 0)]))

    def _dispense(self, payload: bytes) -> Reply:
        # 0xAA00 marker, three uint16 amounts, then a 0xAA flag per used tube
        if len(payload) != 14 or struct.unpack_from("<H", payload)[0] != 0xAA00:
            return Reply(b"", STATUS_ERROR)
        amounts = struct.unpack_from("<3H", payload, 2)
        if any(
            a * ML_PER_UNIT > c.remaining_ml
            for a, c in zip(amounts, self.cartridges, strict=True)
        ):
            return Reply(b"", STATUS_ERROR)  # a tube would run dry
        now = int(time.time())
        for amount, c in zip(amounts, self.cartridges, strict=True):
            if amount:
                c.opened = True
                c.last_use = now
                c.remaining_ml = round(c.remaining_ml - amount * ML_PER_UNIT, 3)
        # ack payload as captured from the real device
        return Reply(b"\x24\x64", delay=sum(amounts) / PUMP_UNITS_PER_S)

    _HANDLERS: dict[int, Callable[[FakeDevice, bytes], Reply]] = {
        Op.HANDSHAKE: _handshake,
        Op.DEVICE_INFO: _device_info,
        0x03: _production,
        Op.PRODUCTION: _production,
        Op.USAGE: _usage,
        Op.BATTERY: _battery,
        Op.DISPENSE: _dispense,
    }


class _FakeClient:
    """A `device.Client` over a `FakeDevice`: replies arrive as notifications."""

    def __init__(self, device: FakeDevice, on_disconnect: Callable[[], None]) -> None:
        self.device = device
        self.is_connected = True
        self._on_disconnect = on_disconnect
        self._notify: Callable[[Any, bytearray], None] | None = None
        self._handshaken = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._spawn(self._enforce_handshake())

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start_notify(
        self, uuid: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        if uuid.lower() != NOTIFY_UUID.lower():
            raise ValueError(f"no such characteristic: {uuid}")
        self._notify = callback
        self._spawn(self._battery_ticks())

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool) -> None:
        if not self.is_connected:
            raise ConnectionError("not connected")
        if uuid.lower() != WRITE_UUID.lower():
            raise ValueError(f"no such characteristic: {uuid}")
        frame = bytes(data)
        if len(frame) >= 3 and (frame[2] & 0x7F) == Op.HANDSHAKE:
            self._handshaken = True
        reply = self.device.handle(frame)
        self._spawn(
            self._deliver(reply.delay, _response_frame(frame[1], frame[2], reply))
        )

    async def disconnect(self) -> None:
        # No awaits below, so _enforce_handshake can call this and still finish
        # cleanly even though the loop cancels it too.
        if not self.is_connected:
            return
        self.is_connected = False
        for task in list(self._tasks):
            task.cancel()
        self._on_disconnect()

    async def _deliver(self, delay: float, frame: bytes) -> None:
        await asyncio.sleep(delay)
        if self.is_connected and self._notify:
            self._notify(None, bytearray(frame))

    async def _enforce_handshake(self) -> None:
        await asyncio.sleep(HANDSHAKE_DEADLINE_S)
        if not self._handshaken:
            await self.disconnect()

    async def _battery_ticks(self) -> None:
        await asyncio.sleep(REPLY_S)  # first reading right after connecting
        while True:
            await self._deliver(0, self.device.battery_frame())
            await asyncio.sleep(BATTERY_TICK_S)
            self.device.battery = max(0, self.device.battery - 1)


class FakeLink:
    """A `device.Link` to one `FakeDevice` (the same unit across reconnects)."""

    def __init__(self, device: FakeDevice | None = None) -> None:
        self.device = device or FakeDevice()

    async def find(self, log: Callable[[str], None]) -> FakeDevice:
        log("mock device, nothing is real (RSM_MOCK=1)")
        return self.device

    async def open(
        self, target: FakeDevice, on_disconnect: Callable[[], None]
    ) -> _FakeClient:
        await asyncio.sleep(CONNECT_S)
        return _FakeClient(target, on_disconnect)
