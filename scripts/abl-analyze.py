#!/usr/bin/env python3
"""Minimal static analysis for a UEFI PE (AArch64) inside an LZMA'd ASUS abl.img.

- xrefs(target_rva): find code that computes a given address (adrp+add / adrp+ldr)
- func_at(addr): walk back to a likely prologue, disassemble forward
Usage: abl-analyze.py <abl.dec> xref 0xc6b06
       abl-analyze.py <abl.dec> func 0x7aedc
"""
import struct
import sys

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs

TEXT_OFF = 0x1000
TEXT_SZ = 0xF3000
TEXT_END = TEXT_OFF + TEXT_SZ


class Img:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        self.md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

    def word(self, a):
        return struct.unpack_from("<I", self.d, a)[0]

    @staticmethod
    def adrp(w, pc):
        if (w & 0x9F000000) != 0x90000000:
            return None
        immlo = (w >> 29) & 3
        immhi = (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= 1 << 21
        return w & 0x1F, ((pc & ~0xFFF) + (imm << 12)) & 0xFFFFFFFFFFFFFFFF

    def xrefs(self, target):
        """Return [(adrp_addr, patch_addr, kind, value)] for code computing `target`."""
        out = []
        for a in range(TEXT_OFF, TEXT_END - 4, 4):
            r = self.adrp(self.word(a), a)
            if not r:
                continue
            rd, page = r
            if abs(page - target) > 0x10000:
                continue
            for k in range(1, 10):
                b = a + 4 * k
                w2 = self.word(b)
                # add xD, xN, #imm  (64-bit, sh=0/1)
                if (w2 & 0x9F000000) == 0x91000000 and ((w2 >> 5) & 0x1F) == rd:
                    imm = (w2 >> 10) & 0xFFF
                    if (w2 >> 22) & 1:
                        imm <<= 12
                    tgt = page + imm
                    if tgt == target:
                        out.append((a, b, "add", tgt))
                    break
                # ldr xD, [xN, #imm]
                if (w2 & 0xFFC00000) == 0xF9400000 and ((w2 >> 5) & 0x1F) == rd:
                    imm = ((w2 >> 10) & 0xFFF) * 8
                    tgt = page + imm
                    if tgt == target:
                        out.append((a, b, "ldr", tgt))
                    break
                # register clobbered by something else writing rd -> stop tracking
                if (w2 & 0x1F) == rd and (w2 & 0x7F000000) in (0x2A000000, 0x32000000):
                    break
        return out

    def dis(self, start, count=40):
        return list(self.md.disasm(self.d[start : start + 4 * count], start))

    def func_start(self, addr, max_back=0x800):
        """Walk back to the nearest prologue (stp x29,x30 / sub sp,sp) before addr."""
        best = None
        a = max(TEXT_OFF, addr - max_back)
        while a < addr:
            for ins in self.md.disasm(self.d[a : a + 4], a):
                m, o = ins.mnemonic, ins.op_str
                if (m == "stp" and o.startswith("x29, x30")) or (m == "sub" and o.startswith("sp, sp")):
                    best = a
            a += 4
        return best if best is not None else addr - 0x40


def main():
    img = Img(sys.argv[1])
    mode = sys.argv[2]
    val = int(sys.argv[3], 0)
    if mode == "xref":
        for a, b, kind, t in img.xrefs(val):
            print(f"xref at {a:#x} ({kind} at {b:#x}) -> {t:#x}")
            start = img.func_start(a)
            print(f"  likely function start {start:#x}")
    elif mode == "func":
        start = img.func_start(val)
        print(f"=== disassembly from {start:#x} ===")
        for ins in img.dis(start, 160):
            print(f"{ins.address:#08x}  {ins.mnemonic:8s} {ins.op_str}")
    elif mode == "str":
        end = img.d.find(b"\x00", val)
        print(repr(img.d[val:end]))


if __name__ == "__main__":
    main()
