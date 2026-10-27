#!/usr/bin/env python3
"""
install_lineage_locked.py — install LineageOS on a bootloader-locked ASUS ZenFone 9
(AI2202) through a custom AVB key. The bootloader stays locked, and the custom key
becomes the trust anchor for the boot chain.

It does NOT unlock the bootloader, touch fuses, or write EDL. It only flashes
ordinary partitions and sets `avb_custom_key` (a supported fastboot partition).

Subcommands
-----------
  doctor    check host tools and input files
  download  download the ASUS firmware and LineageOS ROM, and extract their images
  prepare   download missing images, then build the AVB artifacts
  install   download missing images, prepare, and flash the device
  root      patch the Lineage boot image with Magisk and flash it, re-signing vbmeta
  unroot    restore the unpatched Lineage boot chain
  verify    read the device state (adb or fastboot)
  restore   download missing stock images, then put the stock images back

`prepare`, `install`, and `restore` download only the image sets they need. Use
`--no-download` to stop instead of downloading. Use `download` to fetch images in
advance.

Input layout (defaults)
-----------------------
  <workdir>/images/lineage/
      boot.img dtbo.img vendor_boot.img recovery.img vbmeta_system.img
      system.img vendor.img product.img system_ext.img odm.img vendor_dlkm.img
  <workdir>/images/stock/
      boot.img dtbo.img vendor_boot.img recovery.img vbmeta.img vbmeta_system.img
      system.img vendor.img product.img system_ext.img odm.img vendor_dlkm.img
  <workdir>/key.pem            generated on first run; restore requires the original key
                               (or pass --key PATH)
  <workdir>/avb_pub.bin        generated on first run
  <workdir>/out/               generated vbmeta + re-signed recovery
  <workdir>/out/boot_magisk.img  Magisk-patched boot image (root command)
  <workdir>/downloads/         payload files; ZIPs are deleted after extraction
                               (also caches the downloaded Magisk APK)

The stock images are only needed for `prepare` (boot/dtbo/vendor_boot/vbmeta_system)
and for `restore` (all of them). The Lineage images are needed for `install`.

Examples
--------
  ./install_lineage_locked.py doctor   --workdir ./zf9
  ./install_lineage_locked.py download --workdir ./zf9
  ./install_lineage_locked.py prepare  --workdir ./zf9
  ./install_lineage_locked.py install  --workdir ./zf9
  ./install_lineage_locked.py root     --workdir ./zf9
  ./install_lineage_locked.py unroot   --workdir ./zf9
  ./install_lineage_locked.py verify   --workdir ./zf9

WARNING: this erases userdata and rewrites the active slot's logical partitions.
Read docs/15-install-guide.md first. The `root` command only rewrites `boot` and
`vbmeta`; it needs the original AVB private key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

BE = ">"
LOGICAL = ["system", "vendor", "product", "system_ext", "odm", "vendor_dlkm"]
PHYSICAL = ["boot", "dtbo", "vendor_boot", "recovery", "vbmeta_system", "vbmeta"]
STOCK_PARTS = PHYSICAL + LOGICAL
LINEAGE_PARTS = ["boot", "dtbo", "vendor_boot", "recovery", "vbmeta_system"] + LOGICAL
STOCK_PREPARE = ["boot", "dtbo", "vendor_boot", "vbmeta_system"]
LINEAGE_PREPARE = ["boot", "dtbo", "vendor_boot", "recovery", "vbmeta_system",
                   "vendor", "odm", "vendor_dlkm"]
ROOT_PARTS = ["boot", "dtbo", "vendor_boot", "vbmeta_system",
              "vendor", "odm", "vendor_dlkm"]
FIRMWARE_NAME = "UL-ASUS_AI2202-ASUS-34.0304.2004.145-1.1.1-2407-user.zip"
FIRMWARE_URL = (
    "https://dlcdnets.asus.com/pub/ASUS/ZenFone/AI2202/" + FIRMWARE_NAME
)
FIRMWARE_SHA256 = "c5f45bef35e0827cbd8f613e81618523af2d3666b6966204451f7ab37b7d3dc8"
ROM_NAME = "rinko-23.2-20260810-UNOFFICIAL-davinci.zip"
ROM_URL = (
    "https://download.lynnrin.moe/ROM/ASUS%20Zenfone%209/LineageOS/" + ROM_NAME
)
ROM_SHA256 = "f466719609a6ad5e2db42e5e8e48a7f1b232a4348d883c31f93e21225c237bbc"

# Magisk root: tools are pulled from the official APK. The host runs magiskboot;
# the ramdisk binaries must always be the device's arm64-v8a builds.
MAGISK_API = "https://api.github.com/repos/topjohnwu/Magisk/releases/latest"
MAGISK_MIN_CODE = 27000
MAGISK_PREINIT_DEFAULT = "/dev/block/by-name/metadata"

C_RESET = "\033[0m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"


# ---------------------------------------------------------------------------
# logging / subprocess
# ---------------------------------------------------------------------------
def say(msg: str = "") -> None:
    print(msg, flush=True)


def info(msg: str) -> None:
    say(f"{C_CYAN}==>{C_RESET} {msg}")


def warn(msg: str) -> None:
    say(f"{C_YELLOW}warning:{C_RESET} {msg}")


def ok(msg: str) -> None:
    say(f"{C_GREEN}ok:{C_RESET} {msg}")


def usb_debugging_hint() -> None:
    say("   For adb (and `verify`), enable Settings -> About phone -> tap Build")
    say("   number 7 times, then Settings -> System -> Developer options -> USB debugging.")


def die(msg: str, code: int = 1) -> "None":
    say(f"{C_RED}error:{C_RESET} {msg}")
    raise SystemExit(code)


class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.work = Path(args.workdir).expanduser().resolve()
        self.images = Path(args.images).expanduser().resolve() if args.images else self.work / "images"
        self.lineage = self.images / "lineage"
        self.stock = self.images / "stock"
        self.out = self.work / "out"
        self.key = (Path(args.key).expanduser().resolve()
                    if args.key else self.work / "key.pem")
        self.pub = self.work / "avb_pub.bin"
        self.dry = bool(args.dry_run)
        self.serial: str | None = None
        self.selector: str | None = None

    # -- process helpers ----------------------------------------------------
    def run(self, cmd: list[str], check: bool = True, quiet: bool = False,
            timeout: int = 600, cwd: Path | None = None,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        shown = " ".join(cmd)
        if self.dry:
            say(f"{C_YELLOW}[dry-run]{C_RESET} {shown}")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if not quiet:
            say(f"$ {shown}")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               cwd=cwd, env=env)
        except FileNotFoundError:
            die(f"command not found: {cmd[0]}")
        except subprocess.TimeoutExpired:
            die(f"command timed out after {timeout}s: {shown}")
        if check and p.returncode != 0:
            if p.stdout.strip():
                say(p.stdout.strip())
            if p.stderr.strip():
                say(p.stderr.strip())
            die(f"command failed ({p.returncode}): {shown}")
        return p

    def have(self, name: str) -> bool:
        return shutil.which(name) is not None

    # -- fastboot -----------------------------------------------------------
    def fastboot(self, *args: str, check: bool = True,
                 timeout: int = 600) -> subprocess.CompletedProcess:
        cmd = ["fastboot"]
        if self.selector:
            cmd += ["-s", self.selector]
        cmd += list(args)
        return self.run(cmd, check=check, timeout=timeout)

    def fastboot_getvar(self, var: str) -> str:
        p = self.fastboot("getvar", var, check=False, timeout=15)
        text = (p.stdout or "") + (p.stderr or "")
        m = re.search(rf"{re.escape(var)}:\s*(.+)", text)
        return m.group(1).strip() if m else ""

    def fastboot_devices(self) -> list[tuple[str, str, str]]:
        p = self.run(["fastboot", "devices", "-l"], check=False, quiet=True, timeout=15)
        out: list[tuple[str, str, str]] = []
        for line in (p.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, mode = parts[0], parts[1]
            usb = next((x for x in parts if x.startswith("usb:")), "")
            out.append((serial, usb, mode))
        return out

    def probe_fastboot(self, want_userspace: bool | None = None) -> bool:
        """Pick a live fastboot node and store serial + usb-path selector."""
        for serial, usb, mode in self.fastboot_devices():
            selector = usb or serial
            self.selector = selector
            prod = self.fastboot_getvar("product")
            if not prod:
                continue
            is_userspace = self.fastboot_getvar("is-userspace").lower() == "yes"
            if want_userspace is None or is_userspace == want_userspace:
                self.serial = serial
                return True
        self.selector = None
        return False

    def wait_fastboot(self, userspace: bool, timeout: int = 180, manual: str | None = None) -> None:
        deadline = time.time() + timeout
        prompted = False
        while time.time() < deadline:
            if self.probe_fastboot(want_userspace=userspace):
                kind = "fastbootd" if userspace else "bootloader"
                ok(f"device in {kind} ({self.serial})")
                return
            if manual and not prompted and time.time() > deadline - timeout + 45:
                say("")
                warn(manual)
                if not self.args.yes:
                    input("Press Enter when done... ")
                prompted = True
                deadline = time.time() + 180
            time.sleep(2)
        die(f"timed out waiting for {'fastbootd' if userspace else 'bootloader'}")

    # -- adb ----------------------------------------------------------------
    def adb_states(self) -> list[tuple[str, str]]:
        p = self.run(["adb", "devices"], check=False, quiet=True)
        out = []
        for line in (p.stdout or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                out.append((parts[0], parts[1]))
        return out

    def adb_devices(self) -> list[str]:
        return [serial for serial, state in self.adb_states()
                if state in ("device", "recovery", "sideload")]

    def adb(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["adb"]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        return self.run(cmd, check=check)

    def adb_getprop(self, prop: str) -> str:
        p = self.adb("shell", "getprop", prop, check=False)
        return (p.stdout or "").strip()

    def edl_present(self) -> bool:
        if not self.have("lsusb"):
            return False
        p = self.run(["lsusb"], check=False, quiet=True)
        return bool(re.search(r"05c6:900[0-9a-f]", p.stdout or ""))

    # -- prompts ------------------------------------------------------------
    def confirm(self, prompt: str) -> None:
        if self.args.yes:
            return
        ans = input(f"{prompt} [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            die("aborted by user")

    def pause(self, prompt: str) -> None:
        if self.args.yes:
            return
        input(f"{prompt} ")


# ---------------------------------------------------------------------------
# AVB primitives
# ---------------------------------------------------------------------------
def vbmeta_region(data: bytes) -> tuple[int, int]:
    if data[:4] == b"AVB0":
        return 0, len(data)
    if len(data) >= 64 and data[-64:-60] == b"AVBf":
        orig, voff, vsz = struct.unpack_from(BE + "QQQ", data, len(data) - 64 + 12)
        if data[voff:voff + 4] != b"AVB0":
            raise ValueError("AVB footer does not point at an AVB0 header")
        return voff, vsz
    raise ValueError("no AVB0 header or AVBf footer")


def vbmeta_header(v: bytes) -> dict:
    auth, aux = struct.unpack_from(BE + "QQ", v, 12)
    algo = struct.unpack_from(BE + "I", v, 28)[0]
    hash_off, hash_size = struct.unpack_from(BE + "QQ", v, 32)
    sig_off, sig_size = struct.unpack_from(BE + "QQ", v, 48)
    pk_off, pk_size = struct.unpack_from(BE + "QQ", v, 64)
    desc_off, desc_size = struct.unpack_from(BE + "QQ", v, 96)
    rollback = struct.unpack_from(BE + "Q", v, 112)[0]
    flags = struct.unpack_from(BE + "I", v, 120)[0]
    return {
        "auth": auth, "aux": aux, "algo": algo,
        "hash_off": hash_off, "hash_size": hash_size,
        "sig_off": sig_off, "sig_size": sig_size,
        "pk_off": pk_off, "pk_size": pk_size,
        "desc_off": desc_off, "desc_size": desc_size,
        "rollback": rollback, "flags": flags,
    }


def iter_descriptors(v: bytes):
    h = vbmeta_header(v)
    base = 256 + h["auth"]
    p = base + h["desc_off"]
    end = p + h["desc_size"]
    while p + 16 <= end:
        tag, nb = struct.unpack_from(BE + "QQ", v, p)
        n = nb & 0xFFFFFFFF
        total = 16 + ((n + 7) // 8) * 8
        if p + total > len(v):
            break
        yield tag, v[p:p + total]
        p += total


def descriptors_from_image(path: Path, tags: tuple[int, ...] = (1, 2)) -> list[bytes]:
    data = path.read_bytes()
    voff, vsz = vbmeta_region(data)
    v = data[voff:voff + vsz]
    out = [raw for tag, raw in iter_descriptors(v) if tag in tags]
    if not out:
        raise ValueError(f"{path}: no descriptors with tags {tags}")
    return out


def public_key_from_image(path: Path) -> bytes:
    data = path.read_bytes()
    voff, vsz = vbmeta_region(data)
    v = data[voff:voff + vsz]
    h = vbmeta_header(v)
    base = 256 + h["auth"]
    pk = v[base + h["pk_off"]:base + h["pk_off"] + h["pk_size"]]
    if len(pk) != h["pk_size"] or h["pk_size"] < 8:
        raise ValueError(f"{path}: bad public key")
    return pk


def chain_descriptor(name: str, rloc: int, pubkey: bytes) -> bytes:
    n = name.encode()
    body = struct.pack(BE + "III", rloc, len(n), len(pubkey)) + b"\0" * 64 + n + pubkey
    body += b"\0" * ((8 - len(body) % 8) % 8)   # avbtool aligns num_bytes_following
    return struct.pack(BE + "QQ", 4, len(body)) + body


def hash_descriptor(name: str, path: Path, salt: bytes = b"",
                    flags: int = 0) -> bytes:
    """AVB hash descriptor for an image that has no embedded vbmeta of its own.

    Magisk removes or rewrites the boot image's AVB footer, so its old descriptor
    cannot be reused. The top-level vbmeta stays authoritative: hashing the whole
    patched image keeps verification enabled (`--flags 0`).
    """
    data = path.read_bytes()
    n = name.encode()
    digest = hashlib.sha256(salt + data).digest()
    body = (struct.pack(BE + "Q", len(data))
            + b"sha256".ljust(32, b"\0")
            + struct.pack(BE + "IIII", len(n), len(salt), len(digest), flags)
            + b"\0" * 60 + n + salt + digest)
    raw = struct.pack(BE + "QQ", 2, len(body)) + body
    return raw + b"\0" * ((8 - len(raw) % 8) % 8)


def verify_hash_descriptor(path: Path, raw: bytes) -> None:
    tag, _ = struct.unpack_from(BE + "QQ", raw, 0)
    if tag != 2:
        die(f"{path}: descriptor is not a hash descriptor")
    size = struct.unpack_from(BE + "Q", raw, 16)[0]
    nl, sl, dl, _ = struct.unpack_from(BE + "IIII", raw, 56)
    name = raw[132:132 + nl].decode()
    salt = bytes(raw[132 + nl:132 + nl + sl])
    digest = bytes(raw[132 + nl + sl:132 + nl + sl + dl])
    if (size != path.stat().st_size
            or hashlib.sha256(salt + path.read_bytes()).digest() != digest):
        die(f"{path}: hash descriptor does not match the image")
    ok(f"hash descriptor verifies: {name} ({size} bytes)")


def sign_sha256(key_pem: Path, digest: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "hash.bin"
        s = Path(td) / "sig.bin"
        h.write_bytes(digest)
        p = subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-inkey", str(key_pem),
             "-pkeyopt", "digest:sha256", "-in", str(h), "-out", str(s)],
            capture_output=True, text=True,
        )
        if p.returncode != 0:
            die("openssl signing failed: " + p.stderr.strip())
        return s.read_bytes()


def build_vbmeta(descriptors: list[bytes], key_pem: Path, pubkey: bytes,
                 out_path: Path, flags: int = 0, rollback: int = 0) -> bytes:
    desc = b"".join(descriptors)
    aux_size = ((len(desc) + len(pubkey) + 63) // 64) * 64
    aux = desc + pubkey + b"\0" * (aux_size - len(desc) - len(pubkey))
    hash_size, sig_size = 32, 512
    auth_size = ((hash_size + sig_size + 63) // 64) * 64

    hdr = bytearray(256)
    hdr[0:4] = b"AVB0"
    struct.pack_into(BE + "II", hdr, 4, 1, 0)
    struct.pack_into(BE + "QQ", hdr, 12, auth_size, aux_size)
    struct.pack_into(BE + "I", hdr, 28, 2)  # SHA256_RSA4096
    struct.pack_into(BE + "QQ", hdr, 32, 0, hash_size)
    struct.pack_into(BE + "QQ", hdr, 48, hash_size, sig_size)
    struct.pack_into(BE + "QQ", hdr, 64, len(desc), len(pubkey))
    struct.pack_into(BE + "QQ", hdr, 80, len(desc) + len(pubkey), 0)
    struct.pack_into(BE + "QQ", hdr, 96, 0, len(desc))
    struct.pack_into(BE + "Q", hdr, 112, rollback)
    struct.pack_into(BE + "II", hdr, 120, flags, 0)
    hdr[128:176] = b"avbtool 1.2.0".ljust(48, b"\0")

    digest = hashlib.sha256(bytes(hdr) + aux).digest()
    sig = sign_sha256(key_pem, digest)
    if len(sig) != sig_size:
        die(f"unexpected signature length {len(sig)} (need {sig_size})")
    auth = bytearray(auth_size)
    auth[0:hash_size] = digest
    auth[hash_size:hash_size + sig_size] = sig
    blob = bytes(hdr) + bytes(auth) + aux
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(blob)
    return blob


def verify_vbmeta_blob(d: bytes, pubkey: bytes, name: str) -> None:
    h = vbmeta_header(d)
    base = 256 + h["auth"]
    stored = d[256 + h["hash_off"]:256 + h["hash_off"] + h["hash_size"]]
    calc = hashlib.sha256(d[:256] + d[base:base + h["aux"]]).digest()
    if stored != calc:
        die(f"{name}: stored hash does not match content")
    n = int.from_bytes(pubkey[8:8 + (struct.unpack_from(BE + "I", pubkey, 0)[0] // 8)], "big")
    sig = d[256 + h["sig_off"]:256 + h["sig_off"] + h["sig_size"]]
    m = pow(int.from_bytes(sig, "big"), 65537, n).to_bytes(h["sig_size"], "big")
    if m[:4] != b"\x00\x01\xff\xff" or m[-32:] != calc:
        die(f"{name}: signature does not verify")
    ok(f"vbmeta verifies: {name} ({len(d)} bytes)")


def verify_vbmeta(path: Path, pubkey: bytes) -> None:
    verify_vbmeta_blob(path.read_bytes(), pubkey, path.name)


def descriptor_names(descriptors: list[bytes]) -> list[str]:
    names = []
    for raw in descriptors:
        tag, nb = struct.unpack_from(BE + "QQ", raw, 0)
        n = nb & 0xFFFFFFFF
        if tag == 2:  # hash
            nl = struct.unpack_from(BE + "I", raw, 56)[0]
            off = 132
            names.append(raw[off:off + nl].decode())
        elif tag == 1:  # hashtree
            nl = struct.unpack_from(BE + "I", raw, 16 + 88)[0]
            off = 16 + 164
            names.append(raw[off:off + nl].decode())
    return names


# ---------------------------------------------------------------------------
# key + recovery
# ---------------------------------------------------------------------------
def gen_key(ctx: Ctx) -> bytes:
    ctx.work.mkdir(parents=True, exist_ok=True)
    if not ctx.key.exists():
        info("generating a 4096-bit AVB key")
        ctx.run(["openssl", "genpkey", "-algorithm", "RSA",
                 "-pkeyopt", "rsa_keygen_bits:4096", "-out", str(ctx.key)])
        ctx.key.chmod(0o600)
    mod = ctx.run(["openssl", "rsa", "-in", str(ctx.key), "-noout", "-modulus"],
                  quiet=True).stdout.strip()
    n = int(mod.split("=")[1], 16)
    text = ctx.run(["openssl", "rsa", "-in", str(ctx.key), "-noout", "-text"],
                   quiet=True).stdout
    m = re.search(r"publicExponent:\s*(\d+)", text)
    e = int(m.group(1)) if m else 65537
    if e != 65537:
        warn(f"unexpected RSA exponent {e}; AVB expects 65537")
    bits = n.bit_length()
    n0inv = (-pow(n, -1, 1 << 32)) & 0xFFFFFFFF
    rr = pow(2, 2 * bits, n)
    blob = (struct.pack(BE + "II", bits, n0inv)
            + n.to_bytes(bits // 8, "big")
            + rr.to_bytes(bits // 8, "big"))
    ctx.pub.write_bytes(blob)
    ctx.pub.chmod(0o644)
    info(f"AVB public key blob: {ctx.pub} ({len(blob)} bytes, {bits}-bit)")
    return blob


def resign_recovery(src: Path, dst: Path, key_pem: Path, pubkey: bytes) -> None:
    data = bytearray(src.read_bytes())
    fl = len(data) - 64
    if data[fl:fl + 4] != b"AVBf":
        die(f"{src}: no AVB footer; re-signing only supports AVB-footered images")
    orig, voff, vsz = struct.unpack_from(BE + "QQQ", data, fl + 12)
    v = bytes(data[voff:voff + vsz])
    hash_descs = [raw for tag, raw in iter_descriptors(v) if tag == 2]
    if not hash_descs:
        die(f"{src}: embedded vbmeta has no hash descriptor")
    blob = build_vbmeta(hash_descs, key_pem, pubkey, dst)
    if voff + len(blob) > fl:
        die("re-signed vbmeta does not fit before the footer")
    data[voff:voff + len(blob)] = blob
    struct.pack_into(BE + "Q", data, fl + 28, len(blob))
    dst.write_bytes(bytes(data))

    # verify the embedded hash descriptors against the (unchanged) image bytes
    d = dst.read_bytes()
    for raw in hash_descs:
        size = struct.unpack_from(BE + "Q", raw, 16)[0]
        nl, sl, dl, _ = struct.unpack_from(BE + "IIII", raw, 56)
        name = raw[132:132 + nl].decode()
        salt = bytes(raw[132 + nl:132 + nl + sl])
        dig = bytes(raw[132 + nl + sl:132 + nl + sl + dl])
        if hashlib.sha256(salt + d[:size]).digest() != dig:
            die(f"{dst}: hash descriptor for {name} does not match image")
    verify_vbmeta_blob(blob, pubkey, dst.name)
    ok(f"re-signed recovery: {dst} ({len(dst.read_bytes())} bytes)")


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def crc32_file(path: Path) -> int:
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def copy_progress(src, dst, total: int, label: str) -> None:
    done = 0
    next_report = 0
    while True:
        chunk = src.read(1024 * 1024)
        if not chunk:
            break
        dst.write(chunk)
        done += len(chunk)
        if total and done * 100 // total >= next_report:
            say(f"  {label}: {done * 100 // total}% ({done} of {total} bytes)")
            next_report += 10
    if total and done < total:
        raise ValueError(f"short read: got {done} of {total} bytes")


def download_archive(ctx: Ctx, url: str, expected: str, label: str) -> Path:
    expected = expected.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        die(f"{label} SHA-256 must be 64 hexadecimal characters")
    downloads = ctx.work / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    if not name:
        die(f"{label} URL has no file name: {url}")
    archive = downloads / name

    if archive.exists() and not ctx.args.force:
        info(f"checking existing {label}: {archive}")
        if sha256_file(archive) == expected:
            ok(f"{label} SHA-256 matches; using the existing file")
        else:
            warn(f"existing {label} SHA-256 does not match; downloading again")
            archive.unlink()
    if not archive.exists():
        part = archive.with_name(archive.name + ".part")
        info(f"downloading {label}: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "zf9-installer/1"})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                with part.open("wb") as f:
                    copy_progress(response, f, total, label)
        except (OSError, ValueError, urllib.error.URLError) as e:
            part.unlink(missing_ok=True)
            die(f"{label} download failed: {e}")
        os.replace(part, archive)

    info(f"checking {label} SHA-256")
    digest = sha256_file(archive)
    if digest != expected:
        die(f"{label} SHA-256 mismatch\n  expected: {expected}\n  actual:   {digest}")
    ok(f"{label} verified: {archive}")
    return archive


def payload_hash_path(payload: Path) -> Path:
    return payload.with_name(payload.name + ".sha256")


def payload_valid(payload: Path) -> bool:
    if not payload.is_file() or payload.stat().st_size < 24:
        return False
    with payload.open("rb") as f:
        if f.read(4) != b"CrAU":
            return False
    hash_path = payload_hash_path(payload)
    digest = sha256_file(payload)
    if hash_path.exists():
        return hash_path.read_text().strip() == digest
    hash_path.write_text(digest + "\n")
    return True


def extract_payload(archive: Path, payload: Path, label: str) -> None:
    info(f"extracting payload.bin from the {label} ZIP")
    try:
        with zipfile.ZipFile(archive) as zf:
            member = next((i for i in zf.infolist()
                           if Path(i.filename).name == "payload.bin"), None)
            if member is None:
                die(f"{archive}: payload.bin not found")
            reuse = (payload.exists()
                     and payload.stat().st_size == member.file_size
                     and crc32_file(payload) == member.CRC)
            if reuse:
                ok(f"existing {payload.name} matches the ZIP; using it")
            else:
                part = payload.with_name(payload.name + ".part")
                with zf.open(member) as src, part.open("wb") as dst:
                    copy_progress(src, dst, member.file_size, payload.name)
                os.replace(part, payload)
    except zipfile.BadZipFile as e:
        die(f"invalid {label} ZIP: {e}")
    payload_hash_path(payload).write_text(sha256_file(payload) + "\n")
    ok(f"{payload.name}: {payload}")


def remove_archive(ctx: Ctx, archive: Path) -> None:
    if ctx.args.keep_zip:
        ok(f"kept {archive} (--keep-zip)")
        return
    archive.unlink(missing_ok=True)
    ok(f"removed {archive.name} after payload extraction")


def extract_images(ctx: Ctx, payload: Path, parts: list[str], dest: Path,
                   label: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    info(f"extracting {len(parts)} {label} partitions")
    ctx.run(["payload-dumper-go", "-o", str(dest),
             "-p", ",".join(parts), str(payload)])
    missing = [name for name in parts
               if not (dest / f"{name}.img").is_file()
               or (dest / f"{name}.img").stat().st_size == 0]
    if missing:
        die(f"{label} images missing after extraction: " + ", ".join(missing))
    ok(f"{label} images ready: {dest}")


def fetch_stock(ctx: Ctx, parts: list[str] | None = None) -> None:
    payload = ctx.work / "downloads" / "stock-payload.bin"
    if ctx.args.force or not payload_valid(payload):
        archive = download_archive(ctx, ctx.args.firmware_url,
                                   ctx.args.firmware_sha256, "firmware")
        extract_payload(archive, payload, "firmware")
        remove_archive(ctx, archive)
    else:
        info(f"using existing {payload}")
    extract_images(ctx, payload, parts or STOCK_PARTS, ctx.stock, "stock")


def fetch_lineage(ctx: Ctx, parts: list[str] | None = None) -> None:
    payload = ctx.work / "downloads" / "lineage-payload.bin"
    if ctx.args.force or not payload_valid(payload):
        archive = download_archive(ctx, ctx.args.rom_url, ctx.args.rom_sha256,
                                   "LineageOS ROM")
        extract_payload(archive, payload, "LineageOS ROM")
        remove_archive(ctx, archive)
    else:
        info(f"using existing {payload}")
    extract_images(ctx, payload, parts or LINEAGE_PARTS, ctx.lineage, "LineageOS")


def missing_images(dest: Path, parts: list[str]) -> list[str]:
    return [name for name in parts
            if not (dest / f"{name}.img").is_file()
            or (dest / f"{name}.img").stat().st_size == 0]


def ensure_images(ctx: Ctx, stock_parts: list[str], lineage_parts: list[str]) -> None:
    missing_stock = missing_images(ctx.stock, stock_parts)
    missing_lineage = missing_images(ctx.lineage, lineage_parts)
    if not missing_stock and not missing_lineage:
        return
    detail = []
    if missing_stock:
        detail.append("stock: " + ", ".join(missing_stock))
    if missing_lineage:
        detail.append("LineageOS: " + ", ".join(missing_lineage))
    if ctx.args.no_download:
        die("missing images and --no-download is set\n  " + "\n  ".join(detail))
    if ctx.dry:
        die("cannot download images during --dry-run\n  " + "\n  ".join(detail))
    if not ctx.have("payload-dumper-go"):
        die("payload-dumper-go is required to download images; see docs/setup.md")
    info("missing images: " + "; ".join(detail))
    if missing_stock:
        fetch_stock(ctx, missing_stock)
    if missing_lineage:
        fetch_lineage(ctx, missing_lineage)


def download_images(ctx: Ctx) -> None:
    if ctx.dry:
        die("download does not support --dry-run")
    want_stock = not ctx.args.lineage_only
    want_lineage = not ctx.args.stock_only
    if not ctx.args.zip_only and not ctx.have("payload-dumper-go"):
        die("payload-dumper-go is required to extract images; see docs/setup.md")
    if ctx.args.zip_only:
        if want_stock:
            download_archive(ctx, ctx.args.firmware_url, ctx.args.firmware_sha256, "firmware")
        if want_lineage:
            download_archive(ctx, ctx.args.rom_url, ctx.args.rom_sha256, "LineageOS ROM")
        ok("stopped after download (--zip-only)")
        return
    if want_stock:
        fetch_stock(ctx)
    if want_lineage:
        fetch_lineage(ctx)


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
def need(path: Path, what: str) -> Path:
    if not path.exists():
        die(f"missing {what}: {path}")
    return path


def build_rec(ctx: Ctx) -> dict[str, Path]:
    """Artifacts needed to boot our recovery/fastbootd from the stock kernel."""
    info("checking stock images + Lineage recovery")
    lin = ctx.lineage
    st = ctx.stock
    for name in ("boot", "dtbo", "vendor_boot", "vbmeta_system"):
        need(st / f"{name}.img", "stock image")
    need(lin / "recovery.img", "Lineage recovery image")

    pub = gen_key(ctx)
    ctx.out.mkdir(parents=True, exist_ok=True)

    stock_vmsys_pub = public_key_from_image(st / "vbmeta_system.img")
    info(f"stock vbmeta_system key: {len(stock_vmsys_pub)} bytes")

    # -- vbmeta_rec: stock boot chain + our recovery
    rec_descs: list[bytes] = []
    for name in ("boot", "dtbo", "vendor_boot"):
        rec_descs += descriptors_from_image(st / f"{name}.img", tags=(1, 2))
    rec_descs += [
        chain_descriptor("recovery", 1, pub),
        chain_descriptor("vbmeta_system", 2, stock_vmsys_pub),
    ]
    vbmeta_rec = ctx.out / "vbmeta_rec.img"
    build_vbmeta(rec_descs, ctx.key, pub, vbmeta_rec)
    verify_vbmeta(vbmeta_rec, pub)
    ok(f"built {vbmeta_rec.name}: {descriptor_names(rec_descs)}")

    # -- recovery_ours: Lineage recovery re-signed with our key
    recovery_ours = ctx.out / "recovery_ours.img"
    resign_recovery(lin / "recovery.img", recovery_ours, ctx.key, pub)

    return {"pub": pub, "vbmeta_rec": vbmeta_rec, "recovery_ours": recovery_ours}


def build_lineage(ctx: Ctx, pub: bytes,
                  boot_descriptor: bytes | None = None) -> dict[str, Path]:
    info("checking Lineage boot + vendor images")
    lin = ctx.lineage
    for name in ("boot", "dtbo", "vendor_boot", "vendor", "odm", "vendor_dlkm",
                 "vbmeta_system"):
        need(lin / f"{name}.img", "Lineage image")
    lineage_vmsys_pub = public_key_from_image(lin / "vbmeta_system.img")
    info(f"Lineage vbmeta_system key: {len(lineage_vmsys_pub)} bytes")

    lin_descs: list[bytes] = []
    if boot_descriptor is None:
        lin_descs += descriptors_from_image(lin / "boot.img", tags=(1, 2))
    else:
        lin_descs.append(boot_descriptor)
    for name in ("dtbo", "vendor_boot"):
        lin_descs += descriptors_from_image(lin / f"{name}.img", tags=(1, 2))
    for name in ("vendor", "odm", "vendor_dlkm"):
        lin_descs += descriptors_from_image(lin / f"{name}.img", tags=(1, 2))
    lin_descs += [
        chain_descriptor("recovery", 1, pub),
        chain_descriptor("vbmeta_system", 2, lineage_vmsys_pub),
    ]
    vbmeta_lineage = ctx.out / "vbmeta_lineage.img"
    build_vbmeta(lin_descs, ctx.key, pub, vbmeta_lineage)
    verify_vbmeta(vbmeta_lineage, pub)
    ok(f"built {vbmeta_lineage.name}: {descriptor_names(lin_descs)}")
    return {"vbmeta_lineage": vbmeta_lineage}


def prepare(ctx: Ctx) -> dict[str, Path]:
    ensure_images(ctx, STOCK_PREPARE, LINEAGE_PREPARE)
    rec = build_rec(ctx)
    lin = build_lineage(ctx, rec["pub"])
    return {**rec, **lin}


# ---------------------------------------------------------------------------
# Magisk
# ---------------------------------------------------------------------------
def host_abi() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "arm64-v8a"
    die(f"unsupported host architecture for magiskboot: {machine}")


def magisk_apk_details(apk: Path) -> tuple[str, int]:
    try:
        with zipfile.ZipFile(apk) as zf:
            text = zf.read("assets/util_functions.sh").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as e:
        die(f"{apk}: not a Magisk APK ({e})")
    ver = re.search(r"^MAGISK_VER='([^']+)'", text, re.M)
    code = re.search(r"^MAGISK_VER_CODE=(\d+)", text, re.M)
    if not ver or not code:
        die(f"{apk}: cannot read the Magisk version from assets/util_functions.sh")
    return ver.group(1), int(code.group(1))


def magisk_tool_files() -> dict[str, str]:
    """Host magiskboot plus the arm64-v8a binaries that go into the ramdisk."""
    abi = host_abi()
    return {
        "magiskboot": f"lib/{abi}/libmagiskboot.so",
        "magiskinit": "lib/arm64-v8a/libmagiskinit.so",
        "magisk": "lib/arm64-v8a/libmagisk.so",
        "init-ld": "lib/arm64-v8a/libinit-ld.so",
        "boot_patch.sh": "assets/boot_patch.sh",
        "util_functions.sh": "assets/util_functions.sh",
    }


def fetch_magisk(ctx: Ctx) -> Path:
    if ctx.args.magisk_apk:
        apk = Path(ctx.args.magisk_apk).expanduser().resolve()
        return need(apk, "Magisk APK")
    if ctx.dry:
        die("root cannot download Magisk during --dry-run; pass --magisk-apk PATH")

    downloads = ctx.work / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    info("looking up the latest Magisk release")
    req = urllib.request.Request(
        MAGISK_API, headers={"User-Agent": "zf9-installer/1",
                             "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            release = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as e:
        die(f"Magisk release lookup failed: {e}\n"
            "Download the APK manually and pass --magisk-apk PATH")

    tag = str(release.get("tag_name") or "")
    asset = next((a for a in release.get("assets", [])
                  if str(a.get("name", "")).startswith("Magisk-")
                  and str(a.get("name", "")).endswith(".apk")), None)
    if not tag or asset is None:
        die("the latest Magisk release has no Magisk-*.apk asset; "
            "pass --magisk-apk PATH")

    apk = downloads / str(asset["name"])
    digest = str(asset.get("digest") or "")
    expected = digest[len("sha256:"):] if digest.startswith("sha256:") else ""
    if apk.exists() and not ctx.args.force:
        info(f"using existing {apk}")
        if expected and sha256_file(apk) != expected:
            warn("existing Magisk APK does not match the release metadata; downloading again")
            apk.unlink()
        else:
            if expected:
                ok("Magisk APK SHA-256 matches the release metadata")
            return apk

    part = apk.with_name(apk.name + ".part")
    info(f"downloading Magisk {tag}: {asset['browser_download_url']}")
    try:
        req = urllib.request.Request(str(asset["browser_download_url"]),
                                     headers={"User-Agent": "zf9-installer/1"})
        with urllib.request.urlopen(req, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            with part.open("wb") as f:
                copy_progress(response, f, total, apk.name)
    except (OSError, ValueError, urllib.error.URLError) as e:
        part.unlink(missing_ok=True)
        die(f"Magisk APK download failed: {e}")
    os.replace(part, apk)

    if expected:
        actual = sha256_file(apk)
        if actual != expected:
            die(f"Magisk APK SHA-256 mismatch\n  expected: {expected}\n  actual:   {actual}")
        ok("Magisk APK SHA-256 matches the release metadata")
    return apk


def extract_magisk_tools(ctx: Ctx, apk: Path) -> Path:
    stage = ctx.out / "magisk"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    files = magisk_tool_files()
    try:
        with zipfile.ZipFile(apk) as zf:
            names = set(zf.namelist())
            missing = [src for src in files.values() if src not in names]
            if missing:
                die(f"{apk}: missing Magisk files: " + ", ".join(missing))
            for dst, src in files.items():
                target = stage / dst
                with zf.open(src) as f, target.open("wb") as out:
                    shutil.copyfileobj(f, out)
                target.chmod(0o755)
    except zipfile.BadZipFile as e:
        die(f"invalid Magisk APK: {e}")
    return stage


def patch_boot(ctx: Ctx, src: Path, apk: Path, preinit: str) -> Path:
    need(src, "Lineage boot image")
    stage = extract_magisk_tools(ctx, apk)
    shutil.copyfile(src, stage / "boot.img")

    env = os.environ.copy()
    env.update({
        "BOOTMODE": "true",
        "KEEPVERITY": "true",
        "KEEPFORCEENCRYPT": "true",
        "PATCHVBMETAFLAG": "false",
        "RECOVERYMODE": "false",
        "LEGACYSAR": "false",
        "PREINITDEVICE": preinit,
    })
    info(f"patching boot.img with Magisk (pre-init device: {preinit})")
    ctx.run(["sh", "boot_patch.sh", "boot.img"], cwd=stage, env=env)
    new_boot = stage / "new-boot.img"
    if not new_boot.is_file():
        die("Magisk did not produce new-boot.img")

    dst = ctx.out / "boot_magisk.img"
    shutil.copyfile(new_boot, dst)
    ok(f"Magisk-patched boot image: {dst} ({dst.stat().st_size} bytes)")
    return dst


# ---------------------------------------------------------------------------
# device install
# ---------------------------------------------------------------------------
def select_slot(ctx: Ctx) -> str:
    slot = ctx.fastboot_getvar("current-slot")
    if slot not in ("a", "b"):
        die(f"could not read current-slot (got {slot!r})")
    return slot


def ensure_bootloader(ctx: Ctx) -> None:
    if ctx.probe_fastboot(want_userspace=False):
        return
    if ctx.probe_fastboot(want_userspace=True):
        info("rebooting from fastbootd to the bootloader")
        ctx.fastboot("reboot", "bootloader", check=False)
        ctx.wait_fastboot(userspace=False, timeout=180)
        return

    adb = ctx.adb_states()
    authorized = [(s, st) for s, st in adb
                  if st in ("device", "recovery", "sideload")]
    if authorized:
        ctx.serial = authorized[0][0]
        info(f"rebooting {ctx.serial} from Android to the bootloader")
        ctx.adb("reboot", "bootloader", check=False)
        ctx.wait_fastboot(userspace=False, timeout=180)
        return

    unauthorized = [s for s, st in adb if st in ("unauthorized", "offline")]
    if unauthorized:
        ctx.serial = unauthorized[0]
        warn("USB debugging is not authorized on the phone")
        say("Unlock the phone and tap 'Allow' on the USB debugging prompt.")
        say("The script will reboot it to the bootloader once adb is allowed.")
        deadline = time.time() + 120
        while time.time() < deadline:
            if ctx.probe_fastboot(want_userspace=False):
                return
            if any(s == ctx.serial and st in ("device", "recovery", "sideload")
                   for s, st in ctx.adb_states()):
                info(f"rebooting {ctx.serial} from Android to the bootloader")
                ctx.adb("reboot", "bootloader", check=False)
                ctx.wait_fastboot(userspace=False, timeout=180)
                return
            time.sleep(2)
        ctx.selector = None

    if ctx.edl_present():
        say("")
        warn("The phone is in EDL (05c6:900x).")
        say("Hold Power ~15 seconds, then hold Volume Down + Power to reach the bootloader.")
        say("")
    ctx.wait_fastboot(
        userspace=False, timeout=300,
        manual="Connect/power the phone into the bootloader (Vol Down + Power), then continue.",
    )


def check_device(ctx: Ctx) -> str:
    product = ctx.fastboot_getvar("product")
    slot = select_slot(ctx)
    unlocked = ctx.fastboot_getvar("unlocked")
    ver = ctx.fastboot_getvar("version-bootloader")
    info(f"device: product={product} slot={slot} unlocked={unlocked} bootloader={ver}")
    if product and product not in ("taro",):
        warn(f"expected product 'taro', got {product!r} — continuing anyway")
    if unlocked.lower() in ("yes", "true"):
        warn("bootloader reports unlocked; this guide targets a LOCKED device")
    return slot


def flash(ctx: Ctx, partition: str, image: Path) -> None:
    need(image, "image")
    ctx.fastboot("flash", partition, str(image))


def wipe(ctx: Ctx) -> None:
    p = ctx.fastboot("-w", check=False)
    if p.returncode != 0:
        if p.stdout.strip():
            say(p.stdout.strip())
        if p.stderr.strip():
            say(p.stderr.strip())
        die("fastboot -w failed; do not continue the installation or restore")


def check_super(ctx: Ctx, total: int) -> None:
    super_size = ctx.fastboot_getvar("partition-size:super")
    if super_size.startswith("0x"):
        if total > int(super_size, 16):
            die(f"logical images ({total} bytes) do not fit in super ({super_size})")
    else:
        warn(f"could not read super size (got {super_size!r})")


def check_image_fits(ctx: Ctx, partition: str, image: Path) -> None:
    size = ctx.fastboot_getvar(f"partition-size:{partition}")
    if size.startswith("0x"):
        if image.stat().st_size > int(size, 16):
            die(f"{image} ({image.stat().st_size} bytes) does not fit in "
                f"{partition} ({size})")
    else:
        warn(f"could not read the size of {partition} (got {size!r})")


def flash_parts(ctx: Ctx, slot: str) -> None:
    """Flash the six physical partitions on the active slot."""
    info("flashing physical partitions")
    flash(ctx, f"boot_{slot}", ctx.images / "lineage" / "boot.img")
    flash(ctx, f"dtbo_{slot}", ctx.images / "lineage" / "dtbo.img")
    flash(ctx, f"vendor_boot_{slot}", ctx.images / "lineage" / "vendor_boot.img")
    flash(ctx, f"recovery_{slot}", ctx.out / "recovery_ours.img")
    flash(ctx, f"vbmeta_system_{slot}", ctx.images / "lineage" / "vbmeta_system.img")
    # vbmeta last so the chain is consistent
    flash(ctx, f"vbmeta_{slot}", ctx.out / "vbmeta_lineage.img")


def install_logical(ctx: Ctx, slot: str) -> None:
    total = 0
    for name in LOGICAL:
        img = ctx.images / "lineage" / f"{name}.img"
        need(img, "Lineage logical image")
        total += img.stat().st_size
    check_super(ctx, total)

    info(f"writing {len(LOGICAL)} logical partitions ({total} bytes)")
    # Delete all first so the old partitions do not reserve space in `super`.
    for name in LOGICAL:
        ctx.fastboot("delete-logical-partition", f"{name}_{slot}", check=False)
    for name in LOGICAL:
        img = ctx.images / "lineage" / f"{name}.img"
        ctx.fastboot("create-logical-partition", f"{name}_{slot}", str(img.stat().st_size))
    for name in LOGICAL:
        flash(ctx, f"{name}_{slot}", ctx.images / "lineage" / f"{name}.img")


def install(ctx: Ctx) -> None:
    ensure_images(ctx, STOCK_PREPARE, LINEAGE_PARTS)
    art = prepare(ctx)
    ensure_bootloader(ctx)
    slot = check_device(ctx)
    ctx.confirm(
        f"Install LineageOS to slot {slot}? This ERASES userdata and rewrites "
        f"the slot's logical partitions."
    )

    info("setting avb_custom_key (user trust anchor; bootloader stays locked)")
    ctx.fastboot("flash", "avb_custom_key", str(ctx.pub))

    info("stage 1: stock kernel + our recovery, so fastbootd can write super")
    flash(ctx, f"boot_{slot}", ctx.stock / "boot.img")
    flash(ctx, f"dtbo_{slot}", ctx.stock / "dtbo.img")
    flash(ctx, f"vendor_boot_{slot}", ctx.stock / "vendor_boot.img")
    flash(ctx, f"vbmeta_system_{slot}", ctx.stock / "vbmeta_system.img")
    flash(ctx, f"recovery_{slot}", art["recovery_ours"])
    flash(ctx, f"vbmeta_{slot}", art["vbmeta_rec"])

    if not ctx.args.no_wipe:
        info("formatting userdata + metadata")
        wipe(ctx)

    info("rebooting into fastbootd")
    ctx.fastboot("reboot", "fastboot", check=False)
    ctx.wait_fastboot(
        userspace=True, timeout=180,
        manual="In Lineage Recovery, choose 'Advanced -> Reboot to fastbootd' "
               "(or 'Enter fastboot'), then continue.",
    )
    slot = select_slot(ctx)
    install_logical(ctx, slot)

    info("back to the bootloader")
    ctx.fastboot("reboot", "bootloader", check=False)
    ctx.wait_fastboot(userspace=False, timeout=180)

    flash_parts(ctx, slot)
    info("rebooting into LineageOS (first boot can take several minutes)")
    ctx.fastboot("reboot", check=False)
    say("")
    ok("install finished. First boot initialises /data; do not panic if the screen is")
    say("   black for a few minutes. When the UI is up:")
    usb_debugging_hint()


# ---------------------------------------------------------------------------
# root / unroot
# ---------------------------------------------------------------------------
def root(ctx: Ctx) -> None:
    if not ctx.key.exists():
        die(f"root needs the AVB private key that matches avb_custom_key: {ctx.key}\n"
            "Use --key PATH or put the original key at <workdir>/key.pem.")
    if ctx.dry:
        die("root does not support --dry-run; use --patch-only to inspect the artifacts")

    preinit = ctx.args.preinit_device or MAGISK_PREINIT_DEFAULT
    if not preinit.startswith("/dev/block/"):
        die(f"--preinit-device must be a block device path, got {preinit!r}")

    ensure_images(ctx, [], ROOT_PARTS)
    apk = fetch_magisk(ctx)
    ver, code = magisk_apk_details(apk)
    if code < MAGISK_MIN_CODE:
        die(f"Magisk {ver} (code {code}) is too old; use v27 or newer")
    ok(f"Magisk {ver} ({apk.name})")

    pub = gen_key(ctx)  # derives avb_pub.bin from the existing key; never generates one
    patched = patch_boot(ctx, ctx.lineage / "boot.img", apk, preinit)
    boot_desc = hash_descriptor("boot", patched)
    verify_hash_descriptor(patched, boot_desc)
    art = build_lineage(ctx, pub, boot_descriptor=boot_desc)

    if ctx.args.patch_only:
        say("")
        ok("patched images are ready; not flashing (--patch-only)")
        say(f"   boot:   {patched}")
        say(f"   vbmeta: {art['vbmeta_lineage']}")
        return

    ensure_bootloader(ctx)
    slot = check_device(ctx)
    ctx.confirm(f"Flash the Magisk-patched boot image to slot {slot}? "
                "This does not erase data.")
    check_image_fits(ctx, f"boot_{slot}", patched)

    info("flashing Magisk-patched boot + re-signed vbmeta")
    flash(ctx, f"boot_{slot}", patched)
    flash(ctx, f"vbmeta_{slot}", art["vbmeta_lineage"])
    ctx.fastboot("reboot", check=False)
    say("")
    ok("root install finished. Open the Magisk app and let it finish setup.")
    say("   When the UI is up:")
    usb_debugging_hint()
    say("   If the phone does not boot, reach the bootloader with Vol Down + Power,")
    say("   then run `unroot`.")


def unroot(ctx: Ctx) -> None:
    if not ctx.key.exists():
        die(f"unroot needs the AVB private key that matches avb_custom_key: {ctx.key}\n"
            "Use --key PATH or put the original key at <workdir>/key.pem.")
    if ctx.dry:
        die("unroot does not support --dry-run")

    ensure_images(ctx, [], ROOT_PARTS)
    pub = gen_key(ctx)
    art = build_lineage(ctx, pub)

    ensure_bootloader(ctx)
    slot = check_device(ctx)
    ctx.confirm(f"Restore the unpatched Lineage boot chain on slot {slot}? "
                "This does not erase data.")
    info("flashing the unpatched Lineage boot + original vbmeta")
    check_image_fits(ctx, f"boot_{slot}", ctx.lineage / "boot.img")
    flash(ctx, f"boot_{slot}", ctx.lineage / "boot.img")
    flash(ctx, f"vbmeta_{slot}", art["vbmeta_lineage"])
    ctx.fastboot("reboot", check=False)
    say("")
    ok("unroot finished. Magisk no longer runs; /data/adb files are left behind")
    say("   and a userdata wipe or `restore` removes them.")


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------
def restore(ctx: Ctx) -> None:
    if not ctx.key.exists():
        die(f"restore needs the private key that matches avb_custom_key: {ctx.key}\n"
            "Use --key PATH or put the original key at <workdir>/key.pem.")
    info(f"using AVB key: {ctx.key}")
    info("checking stock images")
    ensure_images(ctx, STOCK_PARTS, ["recovery"])
    art = build_rec(ctx)  # recovery_ours + vbmeta_rec, no Lineage system images needed
    ensure_bootloader(ctx)
    slot = check_device(ctx)
    ctx.confirm(f"Restore STOCK images to slot {slot}? This erases userdata.")

    # Stage a complete boot chain: recovery is ramdisk-only and needs the stock kernel.
    flash(ctx, f"boot_{slot}", ctx.stock / "boot.img")
    flash(ctx, f"dtbo_{slot}", ctx.stock / "dtbo.img")
    flash(ctx, f"vendor_boot_{slot}", ctx.stock / "vendor_boot.img")
    flash(ctx, f"recovery_{slot}", art["recovery_ours"])
    flash(ctx, f"vbmeta_{slot}", art["vbmeta_rec"])
    flash(ctx, f"vbmeta_system_{slot}", ctx.stock / "vbmeta_system.img")
    wipe(ctx)
    ctx.fastboot("reboot", "fastboot", check=False)
    ctx.wait_fastboot(
        userspace=True, timeout=180,
        manual="In Lineage Recovery, choose 'Advanced -> Reboot to fastbootd', then continue.",
    )
    slot = select_slot(ctx)

    total = sum((ctx.stock / f"{n}.img").stat().st_size for n in LOGICAL)
    check_super(ctx, total)
    info(f"restoring logical partitions ({total} bytes)")
    # Delete all first so the old partitions do not reserve space in `super`.
    for name in LOGICAL:
        ctx.fastboot("delete-logical-partition", f"{name}_{slot}", check=False)
    for name in LOGICAL:
        img = ctx.stock / f"{name}.img"
        ctx.fastboot("create-logical-partition", f"{name}_{slot}", str(img.stat().st_size))
    for name in LOGICAL:
        flash(ctx, f"{name}_{slot}", ctx.stock / f"{name}.img")

    ctx.fastboot("reboot", "bootloader", check=False)
    ctx.wait_fastboot(userspace=False, timeout=180)
    info("restoring stock physical chain")
    for name in ("boot", "dtbo", "vendor_boot", "recovery", "vbmeta_system", "vbmeta"):
        flash(ctx, f"{name}_{slot}", ctx.stock / f"{name}.img")
    ctx.fastboot("reboot", check=False)
    ok("stock restore finished")


# ---------------------------------------------------------------------------
# verify / doctor
# ---------------------------------------------------------------------------
def verify(ctx: Ctx) -> None:
    adb = ctx.adb_devices()
    if adb:
        ctx.serial = adb[0]
        info(f"adb device {ctx.serial}")
        keys = [
            "sys.boot_completed", "ro.build.fingerprint", "ro.build.version.sdk",
            "ro.boot.flash.locked", "ro.boot.vbmeta.device_state",
            "ro.boot.slot_suffix", "ro.boot.veritymode", "ro.crypto.state",
            "ro.boot.verifiedbootstate",
        ]
        for k in keys:
            say(f"  {k} = {ctx.adb_getprop(k)}")
        say("")
        warn("ro.boot.verifiedbootstate is SPOOFED to 'green' by LineageOS for")
        say("   SafetyNet. A non-spoofing recovery (TWRP) reports the real value.")
        return
    if ctx.probe_fastboot(want_userspace=False):
        product = ctx.fastboot_getvar("product")
        slot = ctx.fastboot_getvar("current-slot")
        info(f"bootloader: product={product} slot={slot}")
        p = ctx.fastboot("oem", "device-info", check=False)
        say((p.stdout or "").strip())
        return
    die("no adb or fastboot device found\n"
        "  For adb, enable Settings -> About phone -> tap Build number 7 times, "
        "then\n  Settings -> System -> Developer options -> USB debugging.")


def doctor(ctx: Ctx) -> None:
    info("host tools")
    for tool in ("adb", "fastboot", "openssl"):
        if ctx.have(tool):
            ok(tool)
        else:
            die(f"missing host tool: {tool}")
    if sys.version_info < (3, 8):
        die("Python 3.8+ required")
    if ctx.have("payload-dumper-go"):
        ok("payload-dumper-go")
    else:
        warn("payload-dumper-go missing (needed for `download`)")

    info("inputs")
    lin = ctx.lineage
    st = ctx.stock
    for name in ("boot", "dtbo", "vendor_boot", "recovery", "vbmeta_system",
                 "vendor", "odm", "vendor_dlkm", "system", "product", "system_ext"):
        p = lin / f"{name}.img"
        (ok if p.exists() else warn)(f"lineage/{name}.img" + ("" if p.exists() else " (missing)"))
    for name in PHYSICAL + ["system", "vendor", "product", "system_ext", "odm", "vendor_dlkm"]:
        p = st / f"{name}.img"
        (ok if p.exists() else warn)(f"stock/{name}.img" + ("" if p.exists() else " (missing)"))

    info("device")
    if ctx.probe_fastboot(want_userspace=False):
        check_device(ctx)
    elif ctx.adb_devices():
        ctx.serial = ctx.adb_devices()[0]
        ok(f"adb device {ctx.serial}")
    elif ctx.edl_present():
        warn("device is in EDL; hold Power ~15 s, then Vol Down + Power")
    else:
        warn("no adb/fastboot device (fine for prepare only)")

    if ctx.key.exists():
        ok(f"key exists: {ctx.key}")
    else:
        info("key will be generated on prepare")

    info("root")
    if ctx.args.magisk_apk:
        apk = Path(ctx.args.magisk_apk).expanduser()
        if apk.is_file():
            ver, code = magisk_apk_details(apk)
            ok(f"Magisk {ver} ({code})")
        else:
            warn(f"Magisk APK not found: {apk}")
    else:
        info("no --magisk-apk; root downloads the latest official release")
    if not ctx.key.exists():
        warn("root needs the original key that matches avb_custom_key")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command",
                    choices=["doctor", "download", "prepare", "install", "root",
                             "unroot", "verify", "restore"])
    ap.add_argument("--workdir", default="./zf9")
    ap.add_argument("--images", default=None,
                    help="directory containing lineage/ and stock/ (default: <workdir>/images)")
    ap.add_argument("--key", default=None,
                    help="private AVB key (default: <workdir>/key.pem)")
    ap.add_argument("--yes", action="store_true", help="do not prompt")
    ap.add_argument("--dry-run", action="store_true", help="print commands without running them")
    ap.add_argument("--no-wipe", action="store_true", help="do not format userdata/metadata")
    ap.add_argument("--serial", default=None, help="adb/fastboot serial to use")
    ap.add_argument("--firmware-url", default=FIRMWARE_URL,
                    help="ASUS firmware ZIP URL (default: tested WW .145 package)")
    ap.add_argument("--firmware-sha256", default=FIRMWARE_SHA256,
                    help="expected SHA-256 of the firmware ZIP")
    ap.add_argument("--rom-url", default=ROM_URL,
                    help="LineageOS ROM ZIP URL (default: tested unofficial build)")
    ap.add_argument("--rom-sha256", default=ROM_SHA256,
                    help="expected SHA-256 of the LineageOS ROM ZIP")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--stock-only", action="store_true",
                       help="download only the ASUS firmware and stock images")
    group.add_argument("--lineage-only", action="store_true",
                       help="download only the LineageOS ROM and images")
    ap.add_argument("--zip-only", action="store_true",
                    help="download and verify ZIPs, but do not extract images")
    ap.add_argument("--keep-zip", action="store_true",
                    help="keep ZIPs after payload extraction")
    ap.add_argument("--force", action="store_true", help="download the ZIPs again")
    ap.add_argument("--no-download", action="store_true",
                    help="do not download missing images")
    ap.add_argument("--magisk-apk", default=None,
                    help="Magisk APK for root (default: download the latest release)")
    ap.add_argument("--preinit-device", default=None,
                    help="Magisk pre-init block device "
                         f"(default: {MAGISK_PREINIT_DEFAULT})")
    ap.add_argument("--patch-only", action="store_true",
                    help="root: build the patched images but do not flash them")
    args = ap.parse_args()

    if args.command != "download" and (args.zip_only or args.stock_only
                                       or args.lineage_only):
        ap.error("--zip-only, --stock-only and --lineage-only are only valid "
                 "with the download command")
    if args.command == "download" and args.no_download:
        ap.error("--no-download is not valid with the download command")
    if args.command not in ("root", "doctor") and args.magisk_apk:
        ap.error("--magisk-apk is only valid with the root or doctor command")
    if args.command != "root" and (args.preinit_device or args.patch_only):
        ap.error("--preinit-device and --patch-only are only valid with the "
                 "root command")

    if args.serial:
        os.environ["ANDROID_SERIAL"] = args.serial

    ctx = Ctx(args)
    if args.serial:
        ctx.serial = args.serial

    if args.command == "doctor":
        doctor(ctx)
    elif args.command == "download":
        download_images(ctx)
    elif args.command == "prepare":
        prepare(ctx)
        say("")
        ok("prepared. Next: connect the phone (Android with USB debugging, or the")
        say("   bootloader) and run `install`; it moves the phone to the bootloader.")
    elif args.command == "install":
        install(ctx)
    elif args.command == "root":
        root(ctx)
    elif args.command == "unroot":
        unroot(ctx)
    elif args.command == "verify":
        verify(ctx)
    elif args.command == "restore":
        restore(ctx)


if __name__ == "__main__":
    main()
