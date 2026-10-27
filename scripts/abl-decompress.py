#!/usr/bin/env python3
"""Decompress the LZMA payload inside an ASUS/Qualcomm abl.img (ELF32 UEFI wrapper).

Usage: abl-decompress.py <abl.img> [out.bin]
Prints the offset/size and writes the decompressed image. Read-only analysis helper.
"""
import lzma
import sys

UEFI_FV_OFFSET_HINT = 0x1028  # FV header in the .145 abl.img; we scan anyway


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else "abl.dec"
    d = open(src, "rb").read()

    # Find LZMA_ALONE streams: props byte 0x5d is by far the common one in UEFI FVs.
    offsets = [i for i in range(len(d)) if d[i] == 0x5D and d[i + 1 : i + 3] == b"\x00\x00"]
    for off in offsets:
        try:
            out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(d[off:])
        except lzma.LZMAError:
            continue
        if len(out) > 65536:
            open(dst, "wb").write(out)
            print(f"LZMA stream at 0x{off:x} -> {len(out)} bytes -> {dst}")
            return 0
    print("no LZMA stream found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
