#!/usr/bin/env python3
"""Manual-testing CLI: dispense by commanding the pumps directly.

    make dispense A=249,180,183   # 3 cartridge amounts, device units
    make test                     # offline checks (pytest)

The three amounts drive the three cartridges in slot order (= production data
order). No colour science, you are commanding the pumps. Dispense over a bin,
cartridges are expensive. Frame format lives in `protocol.py`, the web app
(`make web`) is the intended way to drive the device.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from protocol import HANDSHAKE, NOTIFY_UUID, WRITE_UUID, build_dispense

SEND_GAP = 0.8  # s between frames, the app left ~0.7 s after the handshake
DISPENSE_WAIT = 10.0  # s, a dispense takes ~8 s, its ack arrives after
MAX_ATTEMPTS = 5


async def send_frames(address: str, frames: Sequence[bytes], label: str) -> None:
    """Connect, handshake, then write `frames`, retrying a flaky link up to 5x.

    The device drops the link ~5 s after connecting unless it has had the
    handshake (the preamble) by then, so that goes out first. We never retry once
    a real frame has been written, to avoid dispensing twice.
    """
    from bleak import (  # lazy: frame building needs no BLE stack
        BleakClient,
        BleakError,
        BleakScanner,
    )

    preamble = [HANDSHAKE]
    dispensed = (
        False  # a real (non-preamble) frame has been written, do not retry past it
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"scanning for {address}...")
            device = await BleakScanner.find_device_by_address(address, timeout=15.0)
            if device is None:
                raise BleakError(
                    "not advertising (asleep? open the lid, or the phone has it)"
                )
            async with BleakClient(device, timeout=20.0) as client:
                await client.start_notify(
                    NOTIFY_UUID, lambda _, d: print(f"  <- {d.hex()}")
                )
                print(f"{label} ({len(frames)} frames)...")
                for seq, frame in enumerate(preamble + list(frames)):
                    data = bytearray(frame)
                    data[1] = (
                        seq & 0xFF
                    )  # fresh per-connection seq so the frame isn't rejected as stale
                    print(f"  -> {data.hex()}")
                    await client.write_gatt_char(WRITE_UUID, bytes(data), response=True)
                    dispensed = dispensed or seq >= len(preamble)
                    await asyncio.sleep(SEND_GAP)
                await asyncio.sleep(DISPENSE_WAIT)
            print("Done.")
            return
        except (TimeoutError, BleakError) as exc:
            if dispensed or attempt == MAX_ATTEMPTS:
                raise
            print(f"attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
            await asyncio.sleep(1.0)


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("address", help="device BLE address")
    ap.add_argument(
        "--dispense",
        metavar="A,B,C",
        required=True,
        help="3 cartridge amounts in device units (e.g. 249,180,183)",
    )
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    amounts = [int(x) for x in args.dispense.split(",")]
    frame = build_dispense(amounts)
    asyncio.run(send_frames(args.address, [frame], f"Dispensing {amounts}"))


if __name__ == "__main__":
    main()
