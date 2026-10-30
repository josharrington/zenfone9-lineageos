# Install LineageOS on a bootloader-locked ASUS ZenFone 9 (AI2202)

This guide describes how LineageOS 23.2 was installed on a ZenFone 9 with a **locked** bootloader.
The bootloader accepts a custom AVB key as the trust anchor for the boot chain, but it remains locked.
At boot, the phone warns that it is loading a different operating system.

This is **not** a bootloader unlock.

> **Status:** The manual method booted LineageOS on one `ASUS_AI2202` with firmware
> `WW-34.0304.2004.145` and LineageOS 23.2 (`BP4A.251205.006`, Android 16). This does not establish
> that the automation, other builds, other devices, or recovery from every failure have been tested.

---

## 0. Read this first

- **This erases userdata** and rewrites the active slot's logical partitions in `super`.
- **There is a risk of an unrecoverable device.** A working fastboot-only restore depends on the
  bootloader and a compatible recovery remaining accessible. There is no verified EDL unbrick path.
- **Do not accept ASUS OTAs** afterwards. An OTA can overwrite `vbmeta` and undo the chain.
- **Back up `<workdir>/key.pem`.** It is the signing key for this device's boot chain. Without it you
  cannot build new signed images; the current install still boots, and stock can be restored without
  it only if a suitable stock restore path remains accessible. Keep the key private and out of Git.
- **The only proven way back is fastboot/fastbootd + the stock images.** There is no public signed
  Firehose programmer for `.145`; do not enter EDL deliberately. If the phone falls to EDL
  (`05c6:900x`), holding **Power ~15 s**, then **Vol Down + Power**, may return it to the bootloader.
  This is not a guaranteed recovery method.
- **First boot can be slow.** In the tested run, the screen stayed black for several minutes while
  Lineage initialised `/data`. A black screen can also indicate a failure; do not assume it is safe.

---

## 1. How it works

On the tested device, the locked bootloader accepted a custom AVB key and the required boot-chain
writes. The [technical explanation](14-lineage-booted.md) describes how the images are signed,
installed, verified, and loaded.

---

## 2. Requirements

**Phone**

- ASUS ZenFone 9 (`ASUS_AI2202`, fastboot product `taro`), WW SKU.
- Firmware **`WW-34.0304.2004.145`**. The stock images you supply must match the firmware already on
  the phone. The tested ROM and firmware links are in the [README](../README.md#downloads).
- Bootloader **locked** (the normal state). Battery above ~50%.

**Host**

- Linux (tested), macOS 13+, or Windows 10 22H2/Windows 11 x64, with `adb`, `fastboot`, `openssl`,
  Python 3.8+ (64-bit), and `payload-dumper-go` to extract images. See
  [Set up the host on Linux](setup.md), [macOS](setup-macos.md), or [Windows](setup-windows.md) for
  installation and checks. macOS and Windows have not been tested on a phone; treat them as
  experimental. WSL has not been verified. USB access in all required modes is essential.
- USB access to the phone in both Android and fastboot/fastbootd modes.
- Enough free disk space for both image sets and enough RAM to read the largest image into memory.
  The preparation script reads whole images into memory.

**Files**

| Set | Needed for | Files |
|---|---|---|
| Lineage images | install | `boot.img dtbo.img vendor_boot.img recovery.img vbmeta_system.img system.img vendor.img product.img system_ext.img odm.img vendor_dlkm.img` |
| Stock `.145` images | prepare + restore | `boot.img dtbo.img vendor_boot.img recovery.img vbmeta.img vbmeta_system.img` (plus the six logical images for restore) |

The installer's `prepare`, `install`, and `restore` commands fetch missing image sets
automatically. Use the [download command](install-easy.md#download-the-images) to fetch both sets in
advance, or [`--no-download`](install-easy.md#download-the-images) to run offline.

Alternatively, extract each `payload.bin` with `payload-dumper-go`, or use images from a matching
dump. **The stock images must be the exact images for the firmware on the phone.**

Optional: [TWRP for AI2202](https://dl.twrp.me/AI2202/) can provide a root adb shell. It must be
re-signed with your key; see [Debugging and recovery](#7-debugging-and-recovery).

---

## 3. File layout

```
zf9/
├── key.pem                  # generated on first prepare; BACK THIS UP
├── avb_pub.bin              # generated public key blob
├── downloads/               # payload files; ZIPs are deleted after extraction
├── out/                     # generated: vbmeta_rec.img, vbmeta_lineage.img, recovery_ours.img
└── images/
    ├── lineage/             # the 11 Lineage images above
    └── stock/               # the stock .145 images
```

---

## 4. Choose an installation method

- [Script-assisted installation](install-easy.md): less manual work, with important script
  limitations to review before use.
- [Manual installation](install-manual.md): individual fastboot steps and an image-preparation
  outline. The signing commands are not complete.

Read the safety warnings and requirements above before using either method. After installation,
return here for first boot, verification, and recovery.

---

## 5. First boot

- A yellow warning appears once (“Your device is loading a different operating system”); it clears.
- The screen may stay black for several minutes while `/data` is formatted and encrypted. A black
  screen can also indicate a failure.
- Avoid interrupting initialisation. When the UI appears, finish or skip setup; no SIM or account is
  needed.

To enable adb: **Settings → About phone → tap Build number 7×**, then
**Settings → System → Developer options → USB debugging**.

---

## 6. Verifying

With the phone connected over adb, you can check the properties without the installer:

```bash
adb shell getprop sys.boot_completed
adb shell getprop ro.build.fingerprint
adb shell getprop ro.boot.flash.locked
adb shell getprop ro.boot.vbmeta.device_state
adb shell getprop ro.boot.slot_suffix
adb shell getprop ro.crypto.state
```

Alternatively, run the script's `verify` command from the repository root:

```bash
python3 scripts/install_lineage_locked.py verify --workdir zf9
```

For the tested installation, expect:

```
sys.boot_completed          = 1
ro.build.fingerprint        = asus/rinko_davinci/davinci:16/BP4A.251205.006/...
ro.boot.flash.locked        = 1
ro.boot.vbmeta.device_state = locked
ro.boot.slot_suffix         = _a  # the tested slot; yours may be _b
ro.crypto.state             = encrypted
```

**`ro.boot.verifiedbootstate` normally reads `green` even on this install.** LineageOS deliberately
spoofs `ro.boot.*` values to pass SafetyNet. The real state is **yellow**, as TWRP (which does not
spoof) reports. Do not use the Android property to judge the boot state.

---

## 7. Debugging and recovery

- **TWRP** can provide a rescue shell. First re-sign it with the same private key used for the
  `recovery` chain; the script's CLI prepares only Lineage Recovery, not TWRP. Flash the re-signed
  image to `recovery_a`, but remember that recovery images
  are ramdisk-only: the bootloader pairs the recovery ramdisk with `boot_a`'s kernel. TWRP needs the
  stock `.145` kernel, so to use TWRP you must also flash the stock `boot`/`dtbo`/`vendor_boot` and
  `vbmeta_rec`. With the Lineage kernel in `boot_a`, TWRP will not start.
- **Lineage recovery** (`out/recovery_ours.img`) does work with the Lineage kernel. Keep it on
  `recovery_a` for normal rescue; use TWRP only when you need a root shell.
- **Slot marked unbootable:** a bootloader reboot usually clears it. If not, reflash the slot's
  images; `devinfo` is a decoy and `oem reset-*` is unlock-gated.
- **`Verify vbmeta ret: 5`:** the staging key does not match `avb_custom_key`. For restore, use the
  original private key with `--key PATH` or put it at `<workdir>/key.pem`.
- **Fastbootd stops responding after an interrupted flash:** hold Power ~15 s, then Vol Down +
  Power to return to the bootloader.
- **EDL** (`05c6:9008/900e`): hold Power ~15 s, then Vol Down + Power. This may not restore access.
- **`fastbootd` “Download is not allowed on locked devices”** applies to *physical* partitions; flash
  those from the bootloader. Logical partitions are writable from fastbootd.

---

## 8. Earlier failures

The [technical explanation](14-lineage-booted.md#how-the-phone-verifies-and-loads-lineageos)
describes why the first boot attempts failed. OTA sideload also failed when the target slot lacked
logical partitions in `super`; the tested installation writes those partitions from fastbootd.

---

## 9. Restore to stock

The script's `restore` command stages the stock boot chain. It needs the private key that matches
`avb_custom_key`; use `--key PATH` if the key is not at `<workdir>/key.pem`. To restore manually,
first make sure that `recovery_ours` and `vbmeta_rec` are signed with that key, and that the stock
images match the firmware. Then:

1. From the bootloader, flash `recovery_ours`, `vbmeta_rec`, and stock `boot`, `dtbo`, `vendor_boot`,
   and `vbmeta_system` to the active slot.
2. Erase userdata and enter fastbootd. Recreate and flash the six logical partitions with the stock
   images and their sizes.
3. Return to the bootloader. Flash stock `boot`, `dtbo`, `vendor_boot`, `recovery`, `vbmeta`, and
   `vbmeta_system` to the active slot. Reboot.

This procedure erases userdata. The ASUS-signed stock vbmeta should return the device to **green**;
the custom key in `DeviceInfo` is not used for an ASUS-signed stock chain.

---

## 10. Limitations

- The bootloader is still **locked**; this is not a fuse unlock.
- Each future ROM update must be re-signed the same way (rebuild `vbmeta_lineage` with the new
  images' descriptors and your key).
- The custom key is device-persistent. Flashing a different user key replaces it.
- No OTA support; updates are manual, via the same procedure.
