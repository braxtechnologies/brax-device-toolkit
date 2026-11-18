# Using Brax Device Toolkit on Linux

The toolkit runs from source on Debian, Ubuntu and Linux Mint (x86-64). It's the same
app as the Windows build, with the same supported phones and the same safety checks.
Use the [Windows guide](WINDOWS-REPAIR-APP.md) for the workflow itself. This page covers only what's
different on Linux.

**Status:** Linux support is new. On November 18, 2026, these steps passed on a
BraX3 running BaseOS 1.0 (Android 16), connected to a Linux Mint 22.3 PC at USB 2
high speed:

- ADB detection and the Install OS phone check
- Looking up the phone's USB location (`3-4`) in sysfs. The same location was
  matched after the phone switched to preloader mode.
- The powered-off preloader handshake on `/dev/ttyACM0` (`hw_code 0x1209`)
- Native storage inspection: the stock Download Agent loaded into RAM, the 233 GiB
  eMMC and its 68-partition table were read, and the reboot command was accepted.
  The phone then booted normally.

Not yet verified from Linux: META entry and return, identifier repair and record
backup (these need BaseOS build 58 / P18), native BaseOS installation and SP Flash
Tool flashing. Check results carefully after the first runs of those flows.

## Requirements

| Requirement | Debian / Ubuntu / Mint |
| --- | --- |
| Python 3.11+ with Tk and venv | `sudo apt install python3 python3-venv python3-tk` |
| ADB | `sudo apt install adb`, or Android SDK platform-tools (`~/Android/Sdk/platform-tools/adb` is found automatically) |
| USB/serial access | udev rules in `linux/70-brax3.rules` (installed by the setup script) |
| SP Flash Tool (optional) | The **Linux** v6 build, extracted as a whole folder; the Windows build does not run here |

No USB drivers are needed: the kernel's `cdc_acm` driver exposes the preloader,
BROM and META ports as `/dev/ttyACM*`.

## Setup

```sh
git clone https://github.com/braxtechnologies/brax-device-toolkit.git
cd brax-device-toolkit
./linux/setup.sh            # creates .venv, offers udev rules and a menu entry
./linux/brax-device-toolkit # start the app
```

The setup script asks before running anything with `sudo`. If you prefer to do it by hand:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo install -m 0644 linux/70-brax3.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
.venv/bin/python repair_app.py
```

Replug the phone after installing the rules.

## Differences from Windows

| Topic | Windows | Linux |
| --- | --- | --- |
| ADB executable | `adb.exe` | `adb` (found on `PATH` or in common SDK folders) |
| META / boot ports | `COM11` | `/dev/ttyACM1` |
| USB location of the phone | PowerShell PnP location paths | sysfs (`/sys/bus/usb/devices`), e.g. `1-4` or `3-1.2` |
| SP Flash Tool | `SPFlashToolV6.exe`; `-p` takes a COM port | `SPFlashToolV6` (or `SPFlashToolV6.sh`); `-p` takes the USB location, e.g. `1-4` |
| Records folder | `%USERPROFILE%\BraxDeviceToolkit` | `~/BraxDeviceToolkit` |

## Troubleshooting

- **Permission denied on `/dev/ttyACM*`:** install the udev rules and replug the phone,
  or add yourself to `dialout` (`sudo usermod -aG dialout $USER`, then log out and in).
- **META entry or the preloader handshake times out:** ModemManager may be probing the
  port. The udev rules tell it to ignore vendor `0e8d`. If it still interferes, run
  `sudo systemctl stop ModemManager` while servicing the phone.
- **`adb devices` shows `no permissions`:** the udev rules are missing or the phone
  was not replugged after installing them.
- **USB location changed between steps:** keep the phone on the same physical port and
  avoid USB hubs that re-enumerate. Some USB 3 ports put the phone on a different bus
  when it changes mode; if that happens, use a USB 2 port.
- **Native flashing requires a root-hub port:** the experimental native engine
  accepts locations like `1-4`, not hub locations like `1-4.2`. This also applies on Windows.
