# Set up the host on Windows

> **Untested:** The Windows instructions have not been tested on a phone. The Linux path is the
> tested one. Treat Windows support as experimental.

These instructions target Windows 10 22H2 or Windows 11 on x64. WSL is not covered.

Use PowerShell. You need at least 30GB of free drive space.

## 1. Install the tools

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Google.PlatformTools -e
winget install --id ShiningLight.OpenSSL.Light -e
```

Restart the terminal after the installs. `Google.PlatformTools` provides `adb` and `fastboot` through
winget shims. `ShiningLight.OpenSSL.Light` provides `openssl`; if it is not on `PATH`, add its `bin`
directory:

```powershell
$openssl = (Get-ChildItem 'C:\Program Files' -Filter 'OpenSSL*' -Directory |
  Select-Object -First 1).FullName + '\bin'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
[Environment]::SetEnvironmentVariable('Path', "$userPath;$openssl", 'User')
```

Restart the terminal again. Python is invoked with the `py -3` launcher, so it does not need to be
on `PATH`.

## 2. Install payload-dumper-go

There is no winget package. Download the latest Windows release and put it on `PATH`:

```powershell
$release = Invoke-RestMethod https://api.github.com/repos/ssut/payload-dumper-go/releases/latest
$asset = $release.assets | Where-Object { $_.name -like '*windows_amd64.tar.gz' } |
  Select-Object -First 1
Invoke-WebRequest $asset.browser_download_url -OutFile "$env:TEMP\payload-dumper-go.tar.gz"
tar -xzf "$env:TEMP\payload-dumper-go.tar.gz" -C "$env:TEMP"
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\payload-dumper-go" | Out-Null
Move-Item "$env:TEMP\payload-dumper-go.exe" "$env:LOCALAPPDATA\payload-dumper-go\" -Force
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
[Environment]::SetEnvironmentVariable('Path', "$userPath;$env:LOCALAPPDATA\payload-dumper-go", 'User')
```

Restart the terminal. `tar` is included with Windows 10 and later.

## 3. Check the tools

```powershell
py -3 --version
adb version
fastboot --version
openssl version
payload-dumper-go -h
```

Python must be 3.8 or later and 64-bit. `openssl version` must report OpenSSL 3.x.

## 4. Install the USB driver

This is the part Windows needs that Linux does not. The phone needs a driver in Android mode for
`adb`, and a bootloader/fastboot driver for flashing.

1. Download and stage the Google USB driver:

   ```powershell
   Invoke-WebRequest https://dl.google.com/android/repository/usb_driver_r13-windows.zip `
     -OutFile "$env:TEMP\usb_driver.zip"
   Expand-Archive "$env:TEMP\usb_driver.zip" "$env:TEMP\usb_driver" -Force
   ```

   If that link is stale, use the current package from
   [Google's USB driver page](https://developer.android.com/studio/run/win-usb).

2. In an **administrator** PowerShell, add the driver package:

   ```powershell
   pnputil /add-driver "$env:TEMP\usb_driver\usb_driver\android_winusb.inf" /install
   ```

3. Connect the phone with USB debugging enabled and check `adb devices`. If Windows still shows an
   unknown device, open **Device Manager**, right-click the phone, choose **Update driver → Browse
   my computer → Let me pick from a list**, and select **Android ADB Interface** (or **Android
   Composite ADB Interface**).

4. Reboot the phone to the bootloader (`adb reboot bootloader`) and repeat the Device Manager step
   for **Android Bootloader Interface**. Fastbootd can enumerate as another device; repeat once more
   if `fastboot devices` is empty in fastbootd.

If the Google driver does not bind, install the USB driver from the
[ASUS ZenFone 9 support page](https://www.asus.com/us/supportonly/zenfone%209/helpdesk_download/).
Use Zadig/WinUSB only as a last resort for the bootloader interface; it can break `adb`.

## 5. Check the installer

From the repository root:

```powershell
py -3 scripts/install_lineage_locked.py doctor
```

`doctor` stops if `adb`, `fastboot`, or `openssl` is missing, if Python is older than 3.8, or if
Python is 32-bit. It warns about missing image files and a missing `payload-dumper-go`. Check its
output before you continue.

Use a short work directory such as `C:\zf9`; deep paths can hit the 260-character limit. The script
passes paths as separate arguments, so spaces in the repository path are safe.

## 6. Root

Native `root` patching is Linux-only: it runs a Linux `magiskboot` binary taken from the Magisk
APK. On Windows, patch `zf9\images\lineage\boot.img` in the Magisk app on the phone, copy the patched
image back, and run:

```powershell
py -3 scripts/install_lineage_locked.py root --patched-boot .\zf9\boot_magisk.img
```

See [Root with Magisk](install-easy.md#root-with-magisk) for the full flow.

## Troubleshooting

- **`adb` or `fastboot` is not found:** restart the terminal. If it still fails, add
  `%LOCALAPPDATA%\Microsoft\WinGet\Links` to `PATH`, or use the official platform-tools ZIP.
- **`fastboot devices` is empty:** the bootloader driver is not bound. Repeat the Device Manager
  step; try another cable and a rear USB port, not a hub.
- **The phone disappears after `fastboot reboot fastboot`:** fastbootd is a new USB device. Install
  the driver for it once, then rerun the command.
- **`openssl` is not found:** add the OpenSSL `bin` directory to `PATH` as shown above.
- **Antivirus blocks the download or the patched image:** allow the repository directory and retry.
- **USB selective suspend drops the phone:** disable it for the USB root hub in Power Options, or
  keep the phone awake during the flash.
- **EDL detection:** the installer only checks for EDL (`lsusb`) on Linux. On Windows, use the
  Power/Volume Down recovery hint in the main guide if the phone stops responding.
