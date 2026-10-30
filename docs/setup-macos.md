# Set up the host on macOS

> **Untested:** The macOS instructions have not been tested on a phone. The Linux path is the
> tested one. Treat macOS support as experimental.

These instructions target macOS 13 or later on Apple Silicon or Intel. WSL is not covered.

You need at least 30GB of free drive space.

## 1. Install the tools

The commands use [Homebrew](https://brew.sh/). Install it first if `brew --version` fails.

```bash
brew install python openssl@3 payload-dumper-go
brew install --cask android-platform-tools
```

`android-platform-tools` provides `adb` and `fastboot`. `payload-dumper-go` is in Homebrew; if the
formula is unavailable, download the `darwin_arm64` or `darwin_amd64` release from
[payload-dumper-go releases](https://github.com/ssut/payload-dumper-go/releases) instead.

macOS ships LibreSSL as `/usr/bin/openssl`, not OpenSSL 3. Put Homebrew's OpenSSL first on `PATH`:

```bash
echo 'export PATH="$(brew --prefix openssl@3)/bin:$PATH"' >> ~/.zshrc
exec zsh
```

Use `~/.bash_profile` instead of `~/.zshrc` if your shell is bash.

## 2. Check the tools

```bash
python3 --version
adb version
fastboot --version
openssl version
payload-dumper-go -h
```

Python must be 3.8 or later and 64-bit. `openssl version` must report **OpenSSL 3.x**, not LibreSSL.
The installer uses OpenSSL 3 `pkeyutl` behaviour to sign the AVB images.

macOS needs no udev rules. If `adb devices` does not list the phone, unlock it, approve the USB
debugging prompt, and try another cable or USB-C port.

## 3. Check the installer

From the repository root:

```bash
python3 scripts/install_lineage_locked.py doctor
```

`doctor` stops if `adb`, `fastboot`, or `openssl` is missing, if Python is older than 3.8, or if
Python is 32-bit. It warns about missing image files, a missing `payload-dumper-go`, and LibreSSL.
Check its output before you continue.

## 4. Root

Native `root` patching is Linux-only: it runs a Linux `magiskboot` binary taken from the Magisk
APK. On macOS, patch `zf9/images/lineage/boot.img` in the Magisk app on the phone, copy the patched
image back, and run:

```bash
python3 scripts/install_lineage_locked.py root --patched-boot /path/to/magisk_patched.img
```

See [Root with Magisk](install-easy.md#root-with-magisk) for the full flow.

## Troubleshooting

- **`fastboot` does not see the phone:** pass the serial explicitly with
  `--serial <serial>`. The installer uses the serial as the fastboot selector on macOS.
- **`adb devices` shows `unauthorized`:** unlock the phone and approve the debugging prompt.
- **`openssl version` says LibreSSL:** the Homebrew `PATH` line above is missing or the terminal
  has not been restarted.
- **`payload-dumper-go: command not found`:** run `brew link payload-dumper-go` or use the release
  binary.
- **EDL detection:** the installer only checks for EDL (`lsusb`) on Linux. On macOS, use the
  Power/Volume Down recovery hint in the main guide if the phone stops responding.
