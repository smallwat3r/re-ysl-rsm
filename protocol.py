"""Rouge Sur Mesure BLE protocol: frame building, response parsing, log labels.

Pure logic, no I/O, so it is shared by the CLI (`re_tools/control.py`) and the web app
(`app.py`) and is trivially testable. Frame format (read off `libbeam_sdk` and
confirmed against a real capture):

    request   AA | seq | opcode | len | payload           bit 7 of opcode set on writes
    response  AA | seq | opcode | status | len | payload

No CRC, the BLE link layer already CRCs.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Sequence
from enum import IntEnum
from typing import Any, Literal

MAGIC = 0xAA
WRITE_FLAG = 0x80  # bit 7 of the opcode byte, set on write-type commands
DISPENSE_MARKER = 0xAA00
MAX_PAYLOAD = 240
N_CARTRIDGES = 3
U16_MAX = 0xFFFF
# safety cap on total pump units per dispense (a standard shade uses 612)
MAX_TOTAL = 1500

# Device facts captured from a working session with the official app (see the
# README, Protocol). The device address is per unit and comes from RSM_ADDRESS.
WRITE_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
NOTIFY_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
# The first frame the app sends after connecting, the device drops the link
# within ~5 s without it.
HANDSHAKE = bytes.fromhex("aa090008373a7e660006074c")
# RGB of each cartridge, recovered by least-squares from the catalogue shades.
CARTRIDGE_COLOURS: dict[str, tuple[int, int, int]] = {
    "MA_100": (189, 106, 85),
    "MA_200": (141, 63, 59),
    "MA_304": (247, 104, 89),
    "MA_513": (226, 95, 134),
    "MA_527": (196, 13, 103),
    "VC_201": (171, 0, 27),
    "VC_204": (187, 89, 91),
    "VC_206": (112, 8, 33),
    "VC_209": (119, 19, 69),
    "VC_211": (156, 47, 45),
    "VC_219": (158, 27, 23),
    "VC_220": (211, 70, 59),
}

# Short codes printed on the physical cartridges (N=nude, O=orange, P=pink,
# R=red), from the app's BeamLipsCartridgesExtensionKt.getCartridgeName.
CARTRIDGE_LABELS: dict[str, str] = {
    "MA_100": "N1",
    "VC_204": "N2",
    "MA_200": "N3",
    "MA_304": "O1",
    "VC_220": "O2",
    "VC_219": "O3",
    "MA_513": "P1",
    "MA_527": "P2",
    "VC_209": "P3",
    "VC_201": "R1",
    "VC_211": "R2",
    "VC_206": "R3",
}

Direction = Literal["->", "<-"]
StateFragment = dict[str, Any]  # a partial device-state update, JSON-serialisable


class Op(IntEnum):
    """Opcodes (low 7 bits of the opcode byte), from `decodeFrame`'s jump table."""

    HANDSHAKE = 0x00
    DEVICE_INFO = 0x02
    PRODUCTION = 0x24
    USAGE = 0x25
    DISPENSE = 0x26
    TRAVEL_MODE = 0x30
    BATTERY = 0x40
    LID = 0x41
    TRAVEL_EVENT = 0x43
    MANUAL_DISPENSE = 0x45
    DFU = 0x7A
    USAGE_WRITEBACK = 0xA5


OP_NAMES: dict[int, str] = {
    Op.HANDSHAKE: "handshake",
    Op.DEVICE_INFO: "device info",
    0x03: "production",  # alias the device also uses for production data
    Op.PRODUCTION: "production",
    Op.USAGE: "usage",
    Op.DISPENSE: "dispense",
    Op.TRAVEL_MODE: "travel mode",
    Op.BATTERY: "battery",
    Op.LID: "lid",
    Op.TRAVEL_EVENT: "travel event",
    Op.MANUAL_DISPENSE: "manual dispense",
    Op.DFU: "dfu",
    Op.USAGE_WRITEBACK: "usage write-back",
}


def build_frame(op: int, payload: bytes = b"", seq: int = 0) -> bytes:
    """Generic request frame: AA | seq | opcode | len | payload."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload too long ({len(payload)} > {MAX_PAYLOAD})")
    return bytes([MAGIC, seq & 0xFF, op, len(payload)]) + payload


def build_dispense(amounts: Sequence[int], seq: int = 0) -> bytes:
    """Dispense frame from three cartridge amounts in device units.

    Opcode 0x26 with bit 7 set (0xA6 on the wire), 14-byte payload = 0xAA00
    marker, three uint16 amounts, then a 0xAA flag per used tube. The seq byte is
    rewritten at send time. Captured blends total ~612, the pure pink was 270.
    """
    if len(amounts) != N_CARTRIDGES:
        raise ValueError(f"need exactly {N_CARTRIDGES} cartridge amounts")
    values = [int(a) for a in amounts]
    if not all(0 <= a <= U16_MAX for a in values):
        raise ValueError(f"amounts must be 0..{U16_MAX}")
    if sum(values) > MAX_TOTAL:
        raise ValueError(f"total {sum(values)} exceeds the safety cap of {MAX_TOTAL}")
    flags = (0xAA if a else 0 for a in values)
    payload = struct.pack("<4H", DISPENSE_MARKER, *values) + struct.pack("<3H", *flags)
    return build_frame(Op.DISPENSE | WRITE_FLAG, payload, seq)


def _cstr(b: bytes) -> str:
    """A null-terminated latin/utf-8 string from a fixed-width field."""
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


def _parse_handshake(p: bytes) -> StateFragment:
    # 03 03 6d00 | lid_opens(u16) 0000 | 12-byte id | 3 x 12-byte cartridge status
    return {
        "lid_opens": struct.unpack_from("<H", p, 4)[0],
        "cartridge_status": [
            p[20 + 12 * i : 32 + 12 * i].hex() for i in range(N_CARTRIDGES)
        ],
    }


def _parse_production(p: bytes) -> StateFragment:
    # 3 x 32: 0000 tube 00 | name(8) | u16 usable mL x1000 | u16 shelf days |
    #         batch(8) | u16 made | u16 expires (days since epoch) | u32 crc
    # (Cartridge::fromBinaryProdData). "serial" is really the two date fields.
    return {
        "cartridges": [
            {
                "name": _cstr(p[4 + 32 * i : 12 + 32 * i]),
                "usable_ml": struct.unpack_from("<H", p, 12 + 32 * i)[0] / 1000,
                "batch": _cstr(p[16 + 32 * i : 24 + 32 * i]),
                "serial": f"{struct.unpack_from('<I', p, 24 + 32 * i)[0]:08x}",
                "expires": struct.unpack_from("<H", p, 26 + 32 * i)[0],
            }
            for i in range(N_CARTRIDGES)
        ]
    }


def _parse_usage(p: bytes) -> StateFragment:
    # 3 x 24: 0000 aa tube | u32 devId | u16 opened | u16 ends (days since epoch) |
    #         u32 last use | u16 err | u16 lid | u16 remaining mL x1000 | u16 crc16
    # (Cartridge::fromBinaryUsageData). The app writes this record back on first
    # use, so a never-used cartridge's record is all zeros: not opened, and its
    # remaining field is meaningless (the app shows such a tube as 100%).
    return {
        "usage": [
            {
                "opened": struct.unpack_from("<H", p, 8 + 24 * i)[0] > 0,
                "ends": struct.unpack_from("<H", p, 10 + 24 * i)[0],
                "last_use": struct.unpack_from("<I", p, 12 + 24 * i)[0],
                "lid_count": struct.unpack_from("<H", p, 18 + 24 * i)[0],
                "remaining_ml": struct.unpack_from("<H", p, 20 + 24 * i)[0] / 1000,
            }
            for i in range(N_CARTRIDGES)
        ]
    }


def _parse_device_info(p: bytes) -> StateFragment:
    # null-separated strings: brand, model, serial, _, fw, hw, _, variant
    s = p.split(b"\0")
    return {
        "brand": _cstr(s[0]),
        "model": _cstr(s[1]),
        "serial": _cstr(s[2]),
        "fw": _cstr(s[4]),
        "hw": _cstr(s[5]),
        "variant": _cstr(s[7]) if len(s) > 7 else "",
    }


def _parse_battery(p: bytes) -> StateFragment:
    # bit 7 is the charging flag, the low 7 bits are the percentage
    return {"battery": p[0] & 0x7F, "charging": bool(p[0] & 0x80)}


# opcode -> decoder. Add a row to support a new response, no control flow to touch.
_PARSERS: dict[int, Callable[[bytes], StateFragment]] = {
    Op.HANDSHAKE: _parse_handshake,
    Op.PRODUCTION: _parse_production,
    Op.USAGE: _parse_usage,
    Op.DEVICE_INFO: _parse_device_info,
    Op.BATTERY: _parse_battery,
}


def parse_response(op: int, payload: bytes) -> StateFragment:
    """Decode a response payload into device-state fields (only the known ones).

    Returns {} for anything unrecognised or too short to decode, so a malformed
    or spoofed frame can never raise into the notification handler.
    """
    parser = _PARSERS.get(op)
    if parser is None:
        return {}
    try:
        return parser(payload)
    except (struct.error, IndexError):
        return {}


def annotate(direction: Direction, data: bytes) -> str:
    """One log line: opcode name, a decoded value for the ones worth it, raw hex."""
    label = "?"
    if len(data) >= 3 and data[0] == MAGIC:
        op = data[2] & 0x7F
        label = OP_NAMES.get(op, f"op {op:#04x}")
        if direction == "<-" and len(data) >= 4:
            status = data[3]
            if op == Op.BATTERY and len(data) >= 6:
                charging = " (charging)" if data[5] & 0x80 else ""
                label = f"battery {data[5] & 0x7F}%{charging}"
            elif op == Op.LID:
                label = "lid opened"
            elif op == Op.DISPENSE:
                label += f" ERROR status {status:#x}" if status else " ack"
            elif status:
                label += f" status {status:#x}"
    return f"{direction} {label:<16} {data.hex()}"
