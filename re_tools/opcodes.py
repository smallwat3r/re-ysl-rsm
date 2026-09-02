#!/usr/bin/env python3
"""Recover the numeric opcode -> handler map from decodeFrame's jump table.

decodeFrame: idx = opcode (0..0x7a); tgt = 0x80e50 + table[opcode]*4; br tgt.
Each case block's first bl (through the PLT) names the decode* handler.

    PYTHONPATH=.pylibs python3 opcodes.py native/libbeam_sdk_arm64.so
"""

import io
import sys
from pathlib import Path

from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs
from elftools.elf.elffile import ELFFile

so = sys.argv[1]
f = io.BytesIO(Path(so).read_bytes())
elf = ELFFile(f)

TABLE_VA = 0x3B4CD8
BR_BASE = 0x80E50
N_OPS = 0x7B


def va2off(va):
    for seg in elf.iter_segments():
        if (
            seg["p_type"] == "PT_LOAD"
            and seg["p_vaddr"] <= va < seg["p_vaddr"] + seg["p_filesz"]
        ):
            return seg["p_offset"] + (va - seg["p_vaddr"])
    raise ValueError(hex(va))


def read(va, n):
    f.seek(va2off(va))
    return f.read(n)


# --- PLT resolution: stub addr -> symbol name ---
plt = elf.get_section_by_name(".plt")
relaplt = elf.get_section_by_name(".rela.plt")
dynsym = elf.get_section_by_name(".dynsym")
stub2name = {}
if plt and relaplt:
    plt_base = plt["sh_addr"]
    # AArch64: 0x20-byte header, 0x10-byte stubs, in .rela.plt order.
    for i, reloc in enumerate(relaplt.iter_relocations()):
        stub = plt_base + 0x20 + i * 0x10
        name = dynsym.get_symbol(reloc["r_info_sym"]).name
        stub2name[stub] = name

md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)


def first_call_name(block_va):
    """Disassemble a case block until the first bl, return the callee name."""
    code = read(block_va, 0x60)
    for insn in md.disasm(code, block_va):
        if insn.mnemonic == "bl" and insn.op_str.startswith("#"):
            tgt = int(insn.op_str[1:], 16)
            return stub2name.get(tgt) or f"sub_{tgt:x}"
        if insn.mnemonic in ("ret", "br"):
            break
    return "?"


table = read(TABLE_VA, N_OPS)
# distinct case blocks -> resolve once
block_name = {}
print(f"{'op':>4}  {'0x':>4}  handler")
for op in range(N_OPS):
    tgt = BR_BASE + table[op] * 4
    if tgt not in block_name:
        block_name[tgt] = first_call_name(tgt)
    name = block_name[tgt]
    # skip the shared default block (table byte 0) unless it names a decoder
    if name in ("?", "sub_80e50") and table[op] == 0:
        continue
    print(f"{op:>4}  0x{op:02x}  {name}")
