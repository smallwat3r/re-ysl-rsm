#!/usr/bin/env python3
"""Decompile named classes to Java-ish source and write them to decompiled/.

PYTHONPATH=.pylibs python3 dump_class.py apk/....apk BleManager CommandExecutor
"""

import os
import sys

from androguard.misc import AnalyzeAPK

apk_path = sys.argv[1]
wanted = sys.argv[2:] or ["BleManager", "CommandExecutor"]

print(f"Loading {apk_path} ...", flush=True)
a, dvms, dx = AnalyzeAPK(apk_path)

os.makedirs("decompiled", exist_ok=True)
for ca in dx.get_classes():
    name = ca.name  # e.g. Lcom/vinsol/.../BleManager;
    short = name.strip("L;").split("/")[-1].split("$")[0]
    if short not in wanted:
        continue
    try:
        src = ca.get_vm_class().get_source()
    except Exception as e:  # noqa: BLE001
        src = f"// decompile failed: {e}\n"
    out = f"decompiled/{name.strip('L;').replace('/', '.')}.java"
    with open(out, "w") as f:
        f.write(src or "// (empty)\n")
    print(f"  wrote {out}  ({len(src or '')} bytes)")
