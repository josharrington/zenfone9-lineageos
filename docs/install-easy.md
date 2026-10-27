# Script-assisted installation

This method uses `scripts/install_lineage_locked.py` to prepare and flash the images. It requires
less manual work, but it is **not safe to run unattended**. The manual flashing method is described
in [Manual installation](install-manual.md).

## Before you start

Read the [shared safety warnings, requirements, and file layout](15-install-guide.md). The script
rewrites partitions and attempts to erase userdata. There is no verified EDL unbrick path.

Review these limitations before you run the script:

- Connect only the target phone. The script can select the first live device, including after a
  reboot, even when `--serial` is specified. It warns rather than stops for an unexpected product.
- A failed `fastboot -w` does not stop the script, and the script does not report its result. Do not
  rely on it to confirm the wipe; do not use `--no-wipe` for a first installation.
- The script does not check image provenance or verify every image against its AVB descriptor before
  it flashes. Verify image sources, checksums, compatibility, and the signing chain independently.
- The script uses image file sizes to create logical partitions. Use raw, non-sparse images; the
  file size of a sparse image is not its expanded partition size.
- The script's `restore` command needs the private key that matches the device's `avb_custom_key`,
  plus the Lineage recovery image to build the intermediate recovery chain. Use `--key PATH` if the
  key is not at `<workdir>/key.pem`.
- The script stops if `fastboot -w` fails. Do not continue after a failed wipe.
- `restore`, `install`, and `prepare` can start in fastbootd; the script returns to the bootloader
  when physical partitions are required.
- `--dry-run` does not reliably simulate preparation or device detection. Do not use it as proof
  that a real run will succeed.
- The script does not verify that the firmware and image sets match the phone.

## Download the images

`prepare`, `install`, and `restore` download only the missing images they need:

| Task | Downloads if missing |
|---|---|
| `prepare` | Stock `boot`, `dtbo`, `vendor_boot`, `vbmeta_system`; LineageOS `boot`, `dtbo`, `vendor_boot`, `recovery`, `vbmeta_system`, `vendor`, `odm`, `vendor_dlkm` |
| `install` | The `prepare` stock images and all 11 LineageOS images |
| `restore` | All 12 stock images and the LineageOS recovery image |

Use the `download` command to fetch both sets in advance:

```bash
python3 scripts/install_lineage_locked.py download
```

The command fetches the tested ASUS `.145` firmware and LineageOS ZIPs, verifies both SHA-256
values, extracts `payload.bin` from each, and creates the stock and Lineage images. After extraction,
the script deletes each ZIP and keeps `payload.bin` for later retries. Use `--keep-zip` to keep the
ZIPs, or `--zip-only` to stop after the verified downloads.

Use `--no-download` with `prepare`, `install`, or `restore` to keep the run offline. The command
stops if an image is missing. Use `--force` to download both ZIPs again.

The download steps need `payload-dumper-go`. `doctor` warns if it is missing. Use these options to
select one image set:

- `--stock-only` downloads only the ASUS firmware and stock images.
- `--lineage-only` downloads only the LineageOS ROM and images.

To use different ZIPs, supply their URLs and SHA-256 values:

```bash
python3 scripts/install_lineage_locked.py download \
  --firmware-url <URL> --firmware-sha256 <SHA256> \
  --rom-url <URL> --rom-sha256 <SHA256>
```

The firmware must match the phone. The tested links and hashes are in the
[README](../README.md#downloads).

## Install

Run these commands from the repository root. The script downloads missing images into the
[shared file layout](15-install-guide.md#3-file-layout):

```bash
python3 scripts/install_lineage_locked.py doctor
python3 scripts/install_lineage_locked.py prepare
# Connect the phone (USB debugging enabled), then:
python3 scripts/install_lineage_locked.py install
```

`prepare` downloads missing images unless you use `--no-download`. Back up `zf9/key.pem` securely
after preparation. `install` runs preparation again. `doctor` warns about missing image files but does not necessarily fail; check its output before
continuing. `install` then:

1. Reads the product, lock state, and active slot. It does not enforce the expected product or lock
   state.
2. Flashes the user key to `avb_custom_key`.
3. Stages the stock kernel, Lineage Recovery, and `vbmeta_rec` so recovery can boot.
4. Runs `fastboot -w` to erase userdata and metadata, then reboots to fastbootd.
5. Deletes, creates, and writes the six logical partitions using the Lineage image file sizes.
6. Returns to the bootloader and flashes the Lineage physical boot chain.
7. Reboots the phone.

If `fastboot reboot fastboot` does not reach fastbootd, the script pauses and asks you to select
**Advanced → Reboot to fastbootd** (or **Enter fastboot**) in Lineage Recovery.

After installation, follow the shared [first boot and verification instructions](15-install-guide.md#5-first-boot).

## Root with Magisk

The `root` command follows the [official Magisk installation flow](https://topjohnwu.github.io/Magisk/install.html)
for a device with boot ramdisk: patch `boot.img`, flash the patched image, then install the Magisk
app and complete its additional setup. It also re-signs the top-level `vbmeta` with your AVB key,
because the bootloader stays locked. Verification stays enabled (`--flags 0`); only `boot_<slot>`
and `vbmeta_<slot>` are rewritten, so userdata is kept.

Requirements:

- The original private key that matches `avb_custom_key` (`zf9/key.pem`, or `--key PATH`). The
  command never generates a new key.
- `images/lineage/boot.img` from the same Lineage build that is installed.
- Magisk v27 or newer. By default `root` downloads the latest official APK to
  `zf9/downloads/`; pass `--magisk-apk PATH` to use a specific APK instead.

Put the phone in the bootloader, or leave it in LineageOS with USB debugging enabled. The script
reboots it to the bootloader itself when adb or fastbootd is reachable; otherwise put it there with
Volume Down + Power, then run:

```bash
python3 scripts/install_lineage_locked.py root
```

The script:

1. Runs Magisk's own `boot_patch.sh` against this device's `boot.img` with the device's pre-init
   partition.
2. Computes a new AVB hash descriptor for the patched image and rebuilds `vbmeta_lineage`.
3. Installs the Magisk app over adb when the phone is reachable and also copies the APK to
   `/sdcard/Download/`.
4. Flashes `boot_<slot>` and `vbmeta_<slot>` and reboots.

### Finish Magisk setup

After the phone boots, open the **Magisk** app, tap **Reinstall** on the Magisk card, and choose
the **recommended** option. Let it finish and approve the reboot it asks for. Magisk is not fully
usable until that step completes.

If the app is not installed (for example, the phone was in the bootloader and had no adb), install
it manually:

```bash
adb install -r zf9/downloads/Magisk-v31.1.apk
```

or copy that APK to the phone and open it from Files. The script also leaves a copy at
`/sdcard/Download/` when adb was available.

Check the Magisk home screen afterwards: it should show the installed version (for example
`31.1`). If it still says “Not installed”, the patched boot image did not boot; check the active
slot and rerun `root`.

The script patches `boot.img` on the host with Magisk’s official `boot_patch.sh`, using this
device’s own image and pre-init partition. Do not copy `boot_magisk.img` to another phone or reuse
it after a ROM update. Also do not run the guide’s
`fastboot flash vbmeta --disable-verity --disable-verification` step: this install relies on a
locked bootloader with verification enabled, and the script re-signs `vbmeta` instead.

To build the images without touching the phone:

```bash
python3 scripts/install_lineage_locked.py root --patch-only --magisk-apk Magisk-v31.1.apk
```

The artifacts are `zf9/out/boot_magisk.img` and `zf9/out/vbmeta_lineage.img`. Use `--no-download`
to stop instead of downloading missing images, or `--preinit-device PATH` if a future Magisk
version needs a different pre-init partition (the default is `/dev/block/by-name/metadata`).

Limitations:

- Root is lost when a ROM image is reflashed or restored; run `root` again afterwards.
- A wrong key in `avb_custom_key` makes the phone fail verification. Keep `key.pem` backed up.
- Play Integrity and some banking or wallet apps may fail. The boot state is `yellow` with the
  custom key, and Magisk itself is detectable.
- If the phone does not boot after rooting, use `unroot` below.

## Remove root

Run this with the phone in LineageOS (USB debugging enabled), fastbootd, or the bootloader. The
script moves it to the bootloader itself when it can:

```bash
python3 scripts/install_lineage_locked.py unroot
```

`unroot` flashes the original Lineage `boot` and a freshly signed `vbmeta`; it does not erase data.
Magisk's `/data/adb` files remain but are inactive. A data wipe or `restore` removes them.
