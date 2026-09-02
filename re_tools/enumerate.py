#!/usr/bin/env python3
"""Scan for the device and dump its full GATT tree, then (optionally) sit on the
notify channel printing everything the device sends while you poke it.

    python enumerate.py                 # scan and list nearby BLE devices
    python enumerate.py wake            # identify the device by opening its lid
    python enumerate.py <ADDRESS>       # dump services/characteristics
    python enumerate.py <ADDRESS> watch # dump, then stream notifications

The device has no useful name, so to pick it out of the crowd:
  - it advertises manufacturer ID 0xface (nothing else around does), flagged
    below, and
  - it only advertises while awake, so `wake` scans before/after you open the
    lid and shows whichever device just appeared.
"""

import asyncio
import sys

from bleak import BleakClient, BleakScanner

NORDIC_UART = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RSM_COMPANY_ID = 0xFACE  # the device's BLE manufacturer ID, unique among normal kit


def _is_device(adv) -> bool:
    return RSM_COMPANY_ID in adv.manufacturer_data


def _print_devices(found: dict, mark: bool = True) -> None:
    """One line per device, strongest signal first, with identifying hints.

    `mark` flags matches with an arrow, useful in a mixed list, redundant when
    the whole list is already the device.
    """
    for d, adv in sorted(found.values(), key=lambda x: -x[1].rssi):
        hints = []
        if adv.manufacturer_data:
            hints.append(
                "mfr " + ",".join(f"0x{cid:04x}" for cid in adv.manufacturer_data)
            )
        if mark and _is_device(adv):
            hints.append("<- the device")
        name = d.name or "(no name)"
        print(f"  {d.address}  rssi={adv.rssi:>4}  {name:<20} {'  '.join(hints)}")


async def scan() -> None:
    print("Scanning 8s...")
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    hits = {addr: pair for addr, pair in found.items() if _is_device(pair[1])}

    if len(hits) == 1:
        print("\nFound!")
        _print_devices(hits, mark=False)
        print(f"\nAdd to .env:\n  RSM_ADDRESS={next(iter(hits))}")
        return

    # Can't decide, so show everything to pick from by hand.
    _print_devices(found)
    if not hits:
        print("\nNo 0xface device seen (asleep?). Wake it and try:")
        print("  python enumerate.py wake")
    else:
        print("\nMore than one 0xface device (?). Pick by signal, or:")
        print("  python enumerate.py wake")


async def wake() -> None:
    """Diff two scans across a lid-open: the device that appears is yours.

    Relies on the device advertising only while awake, so it must be asleep
    (lid shut, untouched) for the first scan.
    """
    input("Make sure the device is ASLEEP (lid shut, untouched), then press Enter...")
    print("Scanning 6s...")
    before = {d.address for d in await BleakScanner.discover(timeout=6.0)}

    input("Now OPEN THE LID (or press its button) to wake it, then press Enter...")
    print("Scanning 6s...")
    after = await BleakScanner.discover(timeout=6.0, return_adv=True)

    appeared = {addr: pair for addr, pair in after.items() if addr not in before}
    if appeared:
        print("\nAppeared after waking (almost certainly your device):")
        _print_devices(appeared)
    else:
        # Nothing new: it was already advertising, so fall back to the 0xface tag.
        print("\nNothing new appeared (it may have already been awake). Best guesses:")
        _print_devices({a: p for a, p in after.items() if _is_device(p[1])} or after)
    print("\nAdd the address above to .env as RSM_ADDRESS.")


async def dump(address, watch=False):
    async with BleakClient(address) as client:
        print(f"Connected: {address}\n")
        for svc in client.services:
            flag = "  <- Nordic UART" if svc.uuid.lower() == NORDIC_UART else ""
            print(f"service {svc.uuid}{flag}")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                val = ""
                if "read" in ch.properties:
                    try:
                        raw = await client.read_gatt_char(ch.uuid)
                        val = f"  = {raw.hex()} {raw!r}"
                    except Exception as e:  # noqa: BLE001 - best-effort dump
                        val = f"  (read failed: {e})"
                print(f"  char {ch.uuid}  [{props}]{val}")

        if not watch:
            return

        # Subscribe to every notify/indicate characteristic and print frames.
        def on_notify(sender, data):
            print(f"NOTIFY {sender}: {data.hex()}")

        subscribed = False
        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    await client.start_notify(ch.uuid, on_notify)
                    subscribed = True
                    print(f"watching {ch.uuid}")
        if not subscribed:
            print("No notify characteristics to watch.")
            return
        print("\nPoke the device (insert cartridge, prime, dispense). Ctrl-C to stop.")
        while True:
            await asyncio.sleep(1)


def main():
    args = sys.argv[1:]
    if not args:
        asyncio.run(scan())
    elif args[0] == "wake":
        asyncio.run(wake())
    else:
        asyncio.run(dump(args[0], watch=(len(args) > 1 and args[1] == "watch")))


if __name__ == "__main__":
    main()
