#!/usr/bin/env python3
"""Disassemble a function from the AArch64 .so by name or address.
objdump on this box has no aarch64 backend, so use capstone.

    PYTHONPATH=.pylibs python3 disasm.py native/libbeam_sdk_arm64.so encodeRequest
    PYTHONPATH=.pylibs python3 disasm.py native/libbeam_sdk_arm64.so 0x354380 0x354404
"""

import io
import sys
from pathlib import Path

from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs
from elftools.elf.elffile import ELFFile

so_path = sys.argv[1]
target = sys.argv[2]

f = io.BytesIO(Path(so_path).read_bytes())
elf = ELFFile(f)

# Map symbol name -> (vaddr, size) from .dynsym.
syms = {}
for sec in elf.iter_sections():
    if sec.name in (".dynsym", ".symtab"):
        for s in sec.iter_symbols():
            if s.entry.st_value:
                syms.setdefault(s.name, (s.entry.st_value, s.entry.st_size))


def vaddr_to_off(va):
    for seg in elf.iter_segments():
        if seg["p_type"] != "PT_LOAD":
            continue
        if seg["p_vaddr"] <= va < seg["p_vaddr"] + seg["p_filesz"]:
            return seg["p_offset"] + (va - seg["p_vaddr"])
    raise ValueError(f"vaddr {va:#x} not in any PT_LOAD")


if target.startswith("0x"):
    start = int(target, 16)
    stop = int(sys.argv[3], 16)
else:
    # match a symbol whose demangled-ish name contains target
    hits = [n for n in syms if target in n]
    if not hits:
        sys.exit(f"no symbol matching {target!r}")
    name = min(hits, key=len)
    start, size = syms[name]
    stop = start + (size or 0x200)
    print(f"// {name}  @ {start:#x} size {size}")

off = vaddr_to_off(start)
f.seek(off)
code = f.read(stop - start)

md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
md.detail = False
# Build a reverse address->symbol map for annotating bl targets.
addr2sym = {v: n for n, (v, _) in syms.items()}
for insn in md.disasm(code, start):
    note = ""
    if insn.mnemonic in ("bl", "b") and insn.op_str.startswith("#"):
        tgt = int(insn.op_str[1:], 16)
        if tgt in addr2sym:
            note = f"   ; {addr2sym[tgt]}"
    print(f"  {insn.address:#08x}  {insn.mnemonic:<8}{insn.op_str}{note}")
