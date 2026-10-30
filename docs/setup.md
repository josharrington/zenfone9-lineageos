# Set up the host on Linux

These instructions use Debian or Ubuntu. On another Linux distribution, use the equivalent packages.
For macOS, see [Set up the host on macOS](setup-macos.md). For Windows, see
[Set up the host on Windows](setup-windows.md). WSL has not been verified.

Please ensure you have at least 30GB of drive space.

## 1. Install the tools

```bash
sudo apt update
sudo apt install android-tools-adb android-tools-fastboot openssl python3
```

`android-tools-adb` and `android-tools-fastboot` install `adb` and `fastboot`. You can also install
the Android SDK Platform-Tools from Google.

## 2. Check the tools

```bash
python3 --version
adb version
fastboot --version
openssl version
```

Python must be 3.8 or later.

On Linux, adb must have permission to access the phone. If `adb devices` does not list the phone,
install the distribution's Android udev rules or use the udev rules from Android SDK Platform-Tools.

## 3. Install payload-dumper-go

Download a release for your platform from
[payload-dumper-go releases](https://github.com/ssut/payload-dumper-go/releases). Extract the
executable and put it on your `PATH`:

```bash
tar -xzf payload-dumper-go_*_linux_amd64.tar.gz
sudo install -m 0755 payload-dumper-go /usr/local/bin/
command -v payload-dumper-go
```

## 4. Check the installer

From the repository root:

```bash
python3 scripts/install_lineage_locked.py doctor
```

`doctor` stops if `adb`, `fastboot`, or `openssl` is missing, if Python is older than 3.8, or if
Python is 32-bit. It warns about missing image files and a missing `payload-dumper-go`. Check its
output before you continue. The installer needs `payload-dumper-go` to download missing images.

## Optional tools

- The analysis scripts need `capstone`: `sudo apt install python3-capstone`.
- `scripts/phonecam.sh` needs `ffmpeg` and `v4l-utils`: `sudo apt install ffmpeg v4l-utils`.
