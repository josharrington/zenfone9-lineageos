# Manual installation

This method describes the image preparation and fastboot operations without using the installer to
flash the phone. The [script-assisted method](install-easy.md) performs these steps automatically.

## Before you start

Read the [shared safety warnings, requirements, and file layout](15-install-guide.md). This method
erases userdata and rewrites partitions. There is no verified EDL unbrick path.

**This is an outline, not a complete set of image-signing commands.** It does not provide all
commands needed to generate the AVB public-key blob, re-sign recovery, and build both vbmeta images.
Do not start flashing until you have prepared and verified those artifacts and a recovery path.
The installer can prepare the artifacts from local images; if you use it, read its
[limitations](install-easy.md#before-you-start) first.

The commands below assume that the current directory is `zf9/` from the shared layout and that
slot `a` is active. Confirm the active slot before writing anything. If it is `b`, use `_b` throughout.
Connect only the target phone and verify the selected device before every write.

## 1. Prepare the images

1. Generate a 4096-bit AVB key and its public-key blob:

   If `key.pem` already exists, do not overwrite it. Use the existing key for this device.

   ```bash
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out key.pem
   ```

   Keep `key.pem` private and back it up securely. The blob is `num_bits | n0inv | n | rr`,
   big-endian, where `n0inv = -1/n mod 2^32` and `rr = 2^(2·num_bits) mod n`. The installer can
   generate and verify the blob as `avb_pub.bin`.

2. Re-sign the Lineage recovery with the key. Keep its existing recovery hash descriptor, replace
   the embedded vbmeta public key and signature, and update the AVB footer's `vbmeta_size`.
   Save it as `out/recovery_ours.img`.

3. Build two vbmeta images by **copying descriptors from the images' own AVB footers**:

   - `out/vbmeta_rec.img`: stock `boot`, `dtbo`, and `vendor_boot` hash descriptors; chain
     `recovery` to the custom key at rollback location 1; chain `vbmeta_system` to the stock ASUS
     key at rollback location 2.
   - `out/vbmeta_lineage.img`: Lineage `boot`, `dtbo`, and `vendor_boot` hash descriptors; Lineage
     `vendor`, `odm`, and `vendor_dlkm` hashtree descriptors; chain `recovery` to the custom key at
     rollback location 1; chain `vbmeta_system` to the Lineage key at rollback location 2.

   Sign both with the custom key using `SHA256_RSA4096`, flags 0, and rollback 0. The signature is
   PKCS#1 v1.5 over `SHA256(vbmeta_header ‖ aux)`. Verify the images before flashing them.

## 2. Reach fastbootd

From the bootloader, with the active slot confirmed, run each command separately. Confirm that each
command succeeds before you run the next one. Do not paste the entire block as an unchecked batch.

```bash
fastboot flash avb_custom_key avb_pub.bin
fastboot flash boot_a          images/stock/boot.img
fastboot flash dtbo_a          images/stock/dtbo.img
fastboot flash vendor_boot_a   images/stock/vendor_boot.img
fastboot flash vbmeta_system_a images/stock/vbmeta_system.img
fastboot flash recovery_a      out/recovery_ours.img
fastboot flash vbmeta_a        out/vbmeta_rec.img
fastboot -w
fastboot reboot fastboot
```

The `fastboot -w` command erases userdata and metadata. Check that it succeeds before proceeding.
If the phone shows the recovery UI instead of fastbootd, choose **Advanced → Reboot to fastbootd**.

## 3. Write the logical partitions

The following sizes are for the tested Lineage image set only. Check the sizes and image format for
your build; do not use these numbers for a different build or for sparse images.

| Partition | Size (bytes) |
|---|---:|
| `system_a` | 1,934,188,544 |
| `vendor_a` | 1,186,603,008 |
| `product_a` | 3,883,347,968 |
| `system_ext_a` | 1,289,457,664 |
| `odm_a` | 1,593,344 |
| `vendor_dlkm_a` | 75,460,608 |

In fastbootd, repeat these operations for each of the six partitions. Replace `<name>` and `<size>`
with the partition name and size; the placeholders are not literal shell arguments.

```text
fastboot delete-logical-partition <name>_a
fastboot create-logical-partition <name>_a <size>
fastboot flash <name>_a images/lineage/<name>.img
```

For the tested image set, `super` is 9,126,805,504 bytes and the six images total 8,370,651,136 bytes.

## 4. Flash the final boot chain

Return to the bootloader before flashing physical partitions. Run each command separately and confirm
that it succeeds before you continue:

```bash
fastboot reboot bootloader
fastboot flash boot_a          images/lineage/boot.img
fastboot flash dtbo_a          images/lineage/dtbo.img
fastboot flash vendor_boot_a   images/lineage/vendor_boot.img
fastboot flash recovery_a      out/recovery_ours.img
fastboot flash vbmeta_system_a images/lineage/vbmeta_system.img
fastboot flash vbmeta_a        out/vbmeta_lineage.img
fastboot reboot
```

**Do not flash the ROM's own `vbmeta.img`.** It sets `flags=3` (verification disabled), which a
locked bootloader ignores; you would lose the descriptors.

After installation, follow the shared [first boot and verification instructions](15-install-guide.md#5-first-boot).
