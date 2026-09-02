#!/usr/bin/env python3
"""Locate the BLE layer in the APK: which classes call the Android Bluetooth
GATT API, what UUIDs are baked in, and where dispense/prime/pump words live.

    PYTHONPATH=.pylibs python3 find_ble.py apk/rouge-sur-mesure-2.2.2-640.apk
"""

import collections
import re
import sys

from androguard.misc import AnalyzeAPK

apk_path = sys.argv[1]
print(f"Loading {apk_path} (parsing 3 dex, ~a minute)...", flush=True)
_, _, dx = AnalyzeAPK(apk_path)

# 1. Classes that call the Android BLE GATT API -> the BLE layer lives here.
GATT_API = [
    ("Landroid/bluetooth/BluetoothGatt;", "writeCharacteristic"),
    ("Landroid/bluetooth/BluetoothGatt;", "setCharacteristicNotification"),
    ("Landroid/bluetooth/BluetoothGattCharacteristic;", "setValue"),
    ("Landroid/bluetooth/BluetoothGattCharacteristic;", "getValue"),
]
callers = collections.Counter()
for cls, meth in GATT_API:
    for m in dx.find_methods(classname=re.escape(cls), methodname=meth):
        for _, caller, _ in m.get_xref_from():
            callers[caller.class_name] += 1

print("\n=== app classes calling the BLE GATT API (by hit count) ===")
for cls, n in callers.most_common(20):
    print(f"  {n:3}  {cls}")

# 2. 128-bit UUID literals (service/characteristic UUIDs).
uuid_re = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
print("\n=== 128-bit UUIDs found (string -> classes using it) ===")
seen = set()
for s in dx.find_strings(uuid_re.pattern):
    val = s.get_value()
    if val in seen:
        continue
    seen.add(val)
    users = {m.class_name for _, m in s.get_xref_from()}
    print(f"  {val}")
    for u in sorted(users):
        print(f"        <- {u}")

# 3. Protocol vocabulary -> narrows opcodes / command builders.
print("\n=== dispense/prime/pump/cartridge strings ===")
vocab = re.compile(
    r"(?i)(dispense|prime|priming|pump|cartridge|mix|motor|purge|shade|recipe)"
)
hits = 0
for s in dx.find_strings(vocab.pattern):
    users = {m.class_name for _, m in s.get_xref_from()}
    if not users:
        continue
    print(f"  {s.get_value()!r}")
    for u in sorted(users):
        print(f"        <- {u}")
    hits += 1
    if hits > 60:
        print("  ... (truncated)")
        break
