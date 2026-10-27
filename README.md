# LineageOS on a locked ZenFone 9

This repo documents a method to install LineageOS on the ASUS Zenfone 9 (WW/US version) running the latest available firmware without a bootloader unlock.

I love this phone. It was the perfect small form factor phone and it's a shame the industry does not support this size any more. Great cameras, great battery life. ASUS only provided 2 years of software updates for the Zenfone 9 when their competitors were offering 5+. Furthermore, ASUS initially offered (or intended to offer?) bootloader unlocking for the device but then reneged on that promise and required users to ship the device to them to unlock it for a significant fee. 

With the rise of LLMs, I thought I'd experiment to see if we could install something more modern on this beloved device. I'm extremely pleased with the results so far. 

This was *heavily* assisted by LLMs, particularly Deepseek v4.1 Flash 1017. I am not an Android developer, I offer no warranty or guarantee any assistance. I've done what I can to keep this as easy as possible.

The total cost using Deepseek via Openrouter for this finding was only $5.43 USD. It's now extremely inexpensive to regain control of your hardware and I'm very excited to attempt this on other devices laying around my home.

The installation script will create a bunch of files, including signing keys specific to your device, and copies of the firmware installed to the device as well as a copy of the stock firmware. I highly recommend backing these up somewhere safe.

LLM Docs Below

---

LineageOS booted on the ASUS ZenFone 9 (AI2202) with a locked bootloader and a custom Android
Verified Boot (AVB) key. This is not a bootloader unlock. The phone shows a warning at boot that it is
loading a different operating system.

**Flashing erases data and can leave the phone unrecoverable.** Read the
[requirements and safety warnings](docs/15-install-guide.md) before starting.

- [Host setup](docs/setup.md)
- [Script-assisted installation](docs/install-easy.md)
- [Manual installation](docs/install-manual.md) — image-signing commands are incomplete
- [How the installation works and tested evidence](docs/14-lineage-booted.md)

## Downloads

- **Tested ROM:** [rinko-23.2-20260810-UNOFFICIAL-davinci.zip](https://download.lynnrin.moe/ROM/ASUS%20Zenfone%209/LineageOS/rinko-23.2-20260810-UNOFFICIAL-davinci.zip)
  ([all builds](https://download.lynnrin.moe/ROM/ASUS%20Zenfone%209/LineageOS/)). This is an
  unofficial build; other builds are not covered by the test. SHA-256 of the downloaded ZIP:
  `f466719609a6ad5e2db42e5e8e48a7f1b232a4348d883c31f93e21225c237bbc`.
- **Stock ASUS firmware:** [WW-34.0304.2004.145 ZIP](https://dlcdnets.asus.com/pub/ASUS/ZenFone/AI2202/UL-ASUS_AI2202-ASUS-34.0304.2004.145-1.1.1-2407-user.zip)
  or the [ASUS support page](https://www.asus.com/us/supportonly/zenfone%209/helpdesk_bios/). Use
  firmware that matches the phone. The tested ZIP's SHA-256 is
  `c5f45bef35e0827cbd8f613e81618523af2d3666b6966204451f7ab37b7d3dc8`. The installer can download
  and verify both ZIPs and extract their images with the `download` command; see
  [script-assisted installation](docs/install-easy.md#download-the-images). `prepare`, `install`,
  and `restore` download missing image sets automatically.

## Acknowledgements

Thanks to the [LineageOS project](https://lineageos.org/) and to Lynnrin for the
[unofficial ZenFone 9 LineageOS 23.2 build on XDA Forums](https://xdaforums.com/t/rom-16-unofficial-davinci-ai2202-lineageos-23-2-for-asus-zenfone-9.4789947/).
The XDA instructions assume an unlocked bootloader; this repository documents a different method.

Keep device dumps, raw logs, and signing keys private. Git ignore rules do not encrypt them or remove
them from Git history.
