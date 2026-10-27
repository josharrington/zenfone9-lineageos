# How the locked-bootloader installation works

On 2026-10-26, LineageOS booted on one `ASUS_AI2202` with a locked bootloader. The test used ASUS
firmware `WW-34.0304.2004.145` and `rinko-23.2-20260810-UNOFFICIAL-davinci.zip`. Other firmware,
ROM builds, and devices have not been verified. See the [README](../README.md) for downloads.

## What the method uses

On the tested firmware, the bootloader accepted two operations while it was locked:

1. It accepted writes to the tested boot-chain partitions.
2. It accepted a public key written to `avb_custom_key` and a `vbmeta` image signed by the matching
   private key.

This combination lets the owner install and verify a boot chain with a custom key. It does not forge
an ASUS signature, bypass signature verification, or unlock the bootloader. This document does not
establish why ASUS permits these operations or whether other firmware permits them.

The security consequence is that a person who can write an accepted custom key and boot-chain images
can install an operating system that ASUS did not sign, even while the bootloader reports locked.
This does not establish that all partitions are writable, that existing user data can be read, or
that another device has the same behavior.

The bootloader shows a warning that the phone is loading a different operating system. The boot state
is `yellow` when the custom key is used. The bootloader remains locked.

## How the images are prepared

Android Verified Boot (AVB) uses signed metadata in `vbmeta` images. The metadata includes
descriptors: records that identify a partition and its expected hash or hash-tree root. A chain
descriptor identifies a partition with its own AVB metadata and the public key used to verify it.

The installation uses a custom AVB key pair. The private key signs metadata on the host; the public
key is written to `avb_custom_key` on the phone. Keep the private key secure and back it up. It is
needed to sign later images for the same trust chain.

The preparation produces:

- A re-signed Lineage Recovery image, with its recovery hash descriptor retained.
- A temporary top-level `vbmeta_rec` for recovery. It describes stock `boot`, `dtbo`, and
  `vendor_boot`; it chains recovery to the custom key and `vbmeta_system` to the stock key.
- A final top-level `vbmeta_lineage`. It describes the Lineage `boot`, `dtbo`, and `vendor_boot`
  images, and the Lineage `vendor`, `odm`, and `vendor_dlkm` hash trees. It chains recovery to the
  custom key and `vbmeta_system` to the Lineage key.

Both top-level images are signed with the custom private key. Their verification flags are `0`:
verification is not disabled.

## How the images are installed

1. The installer writes the custom public key to `avb_custom_key` from the bootloader.
2. It stages the stock `boot`, `dtbo`, `vendor_boot`, and `vbmeta_system` images with the re-signed
   Lineage Recovery and `vbmeta_rec`. This lets the phone start recovery with a matching boot chain.
3. After the data wipe, it starts fastbootd from recovery. Fastbootd writes the six Lineage logical
   partitions in `super`: `system`, `system_ext`, `product`, `vendor`, `vendor_dlkm`, and `odm`.
4. It returns to the bootloader and writes the Lineage physical boot-chain images, the re-signed
   recovery, and `vbmeta_lineage` to the active slot. It then reboots.

The installation process writes physical boot-chain partitions from the bootloader and logical
partitions from fastbootd. A previous OTA sideload failed because the target slot did not have the
required logical partitions in `super`.

Recovery on this device is a ramdisk-only image. The bootloader pairs it with the active slot's
`boot` kernel. In the test, TWRP required the stock kernel; the re-signed Lineage Recovery also worked
with the Lineage kernel.

## How the phone verifies and loads LineageOS

On the tested device, the bootloader accepts the top-level `vbmeta` signature against the installed
custom public key. It uses the descriptors to verify the boot-chain images. It then loads the kernel
and boot data for the selected slot. Android's first-stage initialization mounts the logical
partitions and uses AVB and dm-verity to verify their contents as they are accessed.

The Lineage `fstab.qcom` selects two sets of descriptors:

| Logical partitions | Descriptor source |
|---|---|
| `vendor`, `vendor_dlkm`, `odm` | Top-level `vbmeta_lineage` |
| `system`, `system_ext`, `product` | Chained Lineage `vbmeta_system` |

The first attempts used top-level descriptors copied from stock firmware. Those descriptors matched
the stock vendor images, not the Lineage images. First-stage initialization therefore failed. The
successful image uses the descriptors from the Lineage images themselves.

The top-level `vbmeta` also chains recovery to the custom public key. When recovery is used, the
re-signed recovery image must match that chain.

## Root

The `root` command patches the active slot's `boot` image with Magisk and recomputes its AVB hash
descriptor over the patched bytes, because Magisk replaces the boot image's own vbmeta and footer.
It rebuilds the top-level `vbmeta` with that descriptor and signs it with the same custom key, so
verification stays enabled and the bootloader stays locked. Only `boot` and `vbmeta` are rewritten,
and userdata is kept. `unroot` puts the unpatched `boot` and a freshly signed `vbmeta` back. See
[Root with Magisk](install-easy.md#root-with-magisk).

## Tested vbmeta command

The successful top-level Lineage vbmeta was built with AOSP `avbtool` 1.4.0 using the descriptors
from the Lineage images:

```bash
avbtool make_vbmeta_image \
  --output vbmeta_fixed.img --key key.pem --algorithm SHA256_RSA4096 \
  --flags 0 --rollback_index 0 \
  --include_descriptors_from_image boot.img \
  --include_descriptors_from_image dtbo.img \
  --include_descriptors_from_image vendor_boot.img \
  --include_descriptors_from_image vendor.img \
  --include_descriptors_from_image odm.img \
  --include_descriptors_from_image vendor_dlkm.img \
  --chain_partition recovery:1:avb_pub.bin \
  --chain_partition vbmeta_system:2:vbmeta_system_key.bin
```

This command alone does not prepare the keys or re-sign recovery. The
[manual installation outline](install-manual.md) lists the required artifacts.

## Observed state

[Phone screenshot: model, Android version, LineageOS version, and build number](images/device-build.png).

```text
sys.boot_completed          = 1
ro.build.fingerprint        = asus/rinko_davinci/davinci:16/BP4A.251205.006/eng.rinko:user/release-keys
ro.boot.flash.locked        = 1
ro.boot.vbmeta.device_state = locked
ro.boot.slot_suffix         = _a
ro.boot.veritymode          = enforcing
ro.crypto.state             = encrypted
```

LineageOS reported `ro.boot.verifiedbootstate=green`; a non-spoofing TWRP recovery reported `yellow`.
Do not use the LineageOS property alone to determine the boot state. A fastboot-only stock restore
was tested, but it requires an accessible bootloader and compatible recovery. EDL recovery was not
verified.
