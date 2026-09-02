#!/usr/bin/env python3
"""Decompile the protocol functions from libbeam_sdk with pyghidra (CPython).
Disables the slow GCC exception-handler pass, keeps switch analysis (needed for
decodeFrame). Output -> decompiled/native_targets.c

    GHIDRA_INSTALL_DIR=tools/ghidra_12.1.3_PUBLIC \
    JAVA_HOME=tools/jdk-21.0.12.1+1 \
    PYTHONPATH=.pylibs python3 decompile_pyghidra.py
"""

import os

import pyghidra

pyghidra.start()

SO = os.path.abspath("native/libbeam_sdk_arm64.so")
WANT = [
    "encodeRequest",
    "decodeResponse",
    "encodeResponse",
    "dispenseColor",
    "dispenseRaw",
    "declareDispenseLips",
    "decodeDispense",
    "primeCartridge",
    "purgeCartridges",
    "getCrc32",
]

from ghidra.app.decompiler import DecompInterface  # noqa: E402
from ghidra.util.task import ConsoleTaskMonitor  # noqa: E402

# No global analyzeAll (blows memory on a 4.5MB binary and isn't needed).
# Functions already exist from ELF symbols, just disassemble + decompile each.
with pyghidra.open_program(
    SO,
    project_location=os.path.abspath("tools/ghproj2"),
    project_name="beam2",
    analyze=False,
) as flat:
    program = flat.getCurrentProgram()
    monitor = ConsoleTaskMonitor()
    fm = program.getFunctionManager()

    # collect target functions from the symbol table
    targets = [f for f in fm.getFunctions(True) if any(w in f.getName() for w in WANT)]
    print(f"targets: {len(targets)}", flush=True)

    # disassemble each target (and let the decompiler follow local flow)
    for f in targets:
        flat.disassemble(f.getEntryPoint())

    dec = DecompInterface()
    dec.openProgram(program)

    os.makedirs("decompiled", exist_ok=True)
    with open("decompiled/native_targets.c", "w") as out:
        n = 0
        for f in targets:
            name = f.getName()
            print(f"decompiling {name} ...", flush=True)
            out.write(f"// ===== {name}  @ {f.getEntryPoint()} =====\n")
            res = dec.decompileFunction(f, 120, monitor)
            if res and res.decompileCompleted():
                out.write(res.getDecompiledFunction().getC())
            else:
                out.write(
                    f"// decompile failed: {res.getErrorMessage() if res else 'null'}\n"
                )
            out.write("\n")
            out.flush()
            n += 1
        print(f"wrote {n} functions", flush=True)
