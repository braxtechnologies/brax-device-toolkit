# Brax Device Toolkit

A Windows app for maintaining a supported BraX3 phone. It guides you through
checking the USB connection, repairing assigned serial numbers and IMEIs, and
backing up or restoring those device records. You can also manage supported
Bluetooth and Wi-Fi factory addresses and download OS packages.

**This is a preview release. OS installation and formatting are currently
disabled while device-to-firmware compatibility checks are being completed.**
You can check a connection, download an OS package and review it, but the app
will not flash it.

## What can I use it for?

| Task | Current support |
| --- | --- |
| Repair serial numbers, IMEIs and Bluetooth/Wi-Fi factory addresses | BraX3 running BaseOS build 58 / P18 only; other models and OS versions are not supported for these writes |
| Back up and restore device records | Saves supported factory records locally; this is not a backup of photos, apps or the whole phone |
| Check the phone's connection | Detect Android or META service mode and guide supported mode changes |
| Prepare an OS installation | Check the Android connection, choose and download a package, verify its download checksum and review the operation |
| Install or format an OS | Unavailable in this release, including Download Only, Firmware Upgrade and format modes |

Use repair functions only with the identifiers assigned to that phone. The app
does not allocate new IMEIs or generate production identifiers.

## Download and start

1. Open [Releases](https://github.com/braxtechnologies/brax-device-toolkit/releases)
   and choose the latest preview.
2. Download `BraxDeviceToolkit-Windows.exe`, or download and extract the entire
   portable ZIP, then run `BraxRepairTool/BraxRepairTool.exe`.
3. If using the ZIP, keep the `_internal` folder beside the executable.
4. Follow the numbered steps in the app. Later steps unlock after the preceding
   checks succeed.

Python is included; you do not need to install it. The Windows builds are
currently unsigned.

## What else do I need?

| Requirement | What it is used for |
| --- | --- |
| Windows PC and a USB data cable | Run the app and connect the phone |
| Android platform-tools, including ADB | Identify a phone running Android, guide supported mode changes and check it after restarting |
| Appropriate phone USB drivers | Let Windows communicate with Android, META service mode and powered-off flash interfaces; these are separate connections |
| USB debugging enabled and authorized | Let this computer communicate with the phone through ADB |
| Assigned device identifiers | Supply the serial number, IMEIs or factory addresses for a repair |
| SP Flash Tool for Windows and an appropriate OS package | External flashing prerequisites; installing them does not enable the currently blocked flash operations |

ADB, USB drivers, SP Flash Tool and OS images are separate downloads. Extract
platform-tools completely and select its `adb.exe` in the app. Keep its companion
DLLs. A working ADB connection does not establish that the META or flash drivers
are also working.

The **Install OS** page can check the
[Brax flash-tool download folder](https://download.os-source.co/brax3/flash-tool/)
for a compatible Windows ZIP and checksum. If no Windows package is available,
the app explains that. You can also select an existing `SPFlashToolV6.exe`
installation. Keep the complete vendor folder: the executable alone is not enough.

## Using the app

Read the [step-by-step user guide](WINDOWS-REPAIR-APP.md) for connection setup,
repairs, backups, restoring records and OS preparation.

Use **Advanced tools → Open records** to find saved backups and
operation results. Keep backups private and retain them until you
have checked the result after restarting the phone.

## For contributors

Use Windows and Python 3.13 with Tk support:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe repair_app.py
.\build-repair-exe.ps1 -Python .\.venv\Scripts\python.exe
```

`requirements.txt` contains the runtime dependency (pyserial, for USB serial
communication). `requirements-build.txt` adds PyInstaller to create Windows
executables. Neither file is needed by users running the packaged app.

Protocol regression data lives in `tests/fixtures`; it checks packet encoding and
response parsing without a connected phone. The optional diagnostic CLI is
available with `python -m brax_snwriter --help`.

Build outputs are in `dist/release`: standalone EXE, portable ZIP and SHA256SUMS.
GitHub Actions tests and builds pushes and pull requests. A `v*` tag publishes
the artifacts as a preview release. Automated tests do not replace testing on
supported phones and clean Windows installations.

## License

Brax code is MIT licensed. Third-party software and Brax branding have separate
terms. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md).
