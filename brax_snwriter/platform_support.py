"""Host differences between Windows and Linux (Debian, Ubuntu, Mint).

Everything that depends on the host OS lives here so device logic stays shared.
USB locations use pySerial's format on both systems: ``<bus>-<port>[.<port>...]``
optionally followed by ``:<config>.<interface>``. Windows derives it from PnP
location paths; Linux reads it directly from sysfs.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

IS_WINDOWS = os.name == 'nt'
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
ADB_NAME = 'adb.exe' if IS_WINDOWS else 'adb'
SYSFS_USB = Path('/sys/bus/usb/devices')
_SERIAL_PORT = re.compile(r'COM[1-9][0-9]*|/dev/tty(?:ACM|USB)[0-9]+', re.I)


def is_serial_port(value):
    """A Windows COM port or a Linux USB serial device node."""
    return bool(_SERIAL_PORT.fullmatch(value or ''))


def usb_device_location(location):
    """Physical port part of a pySerial location, without the interface suffix."""
    return (location or '').split(':')[0]


def usb_interface(location):
    """Interface number from '1-2:x.1' (Windows) or '1-2:1.1' (Linux), else None."""
    match = re.fullmatch(r'[^:]+:[^.]+\.(\d+)', location or '')
    return int(match.group(1)) if match else None


def is_adb(value):
    path = Path(value)
    return (path.is_absolute() and path.is_file() and path.name.lower() == ADB_NAME
            and (IS_WINDOWS or os.access(path, os.X_OK)))


def adb_candidates():
    """Common platform-tools locations, used only when the user typed plain 'adb'."""
    if IS_WINDOWS:
        return [Path('C:/Program Files/platform-tools/adb.exe')]
    home = Path.home()
    paths = [home/'Android/Sdk/platform-tools/adb', home/'platform-tools/adb',
             Path('/usr/lib/android-sdk/platform-tools/adb'), Path('/usr/bin/adb')]
    if os.environ.get('ANDROID_HOME'):
        paths.insert(0, Path(os.environ['ANDROID_HOME'])/'platform-tools/adb')
    return paths


def find_adb(value):
    """Resolve a typed ADB value to an absolute executable path, or None."""
    value = (value or '').strip().strip('"')
    if not value:
        return None
    expanded = os.path.expandvars(os.path.expanduser(value))
    candidate = shutil.which(expanded)
    if candidate:
        return str(Path(candidate).resolve())
    if Path(expanded).is_absolute():
        return str(Path(expanded))
    if value.lower() in ('adb', 'adb.exe'):
        for path in adb_candidates():
            if path.is_file():
                return str(path.resolve())
    return None


def open_folder(path):
    """Show a folder in the desktop file manager without waiting for it."""
    if IS_WINDOWS:
        os.startfile(str(path))
        return
    opener = shutil.which('xdg-open')
    if not opener:
        raise OSError('xdg-open was not found. Open this folder manually: '+str(path))
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def _read(path):
    try:
        return path.read_text(encoding='ascii', errors='replace').strip()
    except OSError:
        return ''


def linux_usb_location(serial, vid=0x0e8d, pid=0x201c, root=SYSFS_USB):
    """Physical USB location of the Android-mode phone whose USB serial is ``serial``.

    Reads sysfs only. The returned value matches pySerial's Linux port locations,
    so later META/preloader ports can be tied to the same physical connection.
    """
    found = []
    for device in Path(root).iterdir() if Path(root).is_dir() else []:
        if ':' in device.name or not re.fullmatch(r'\d+-\d+(?:\.\d+)*', device.name):
            continue
        if (_read(device/'idVendor'), _read(device/'idProduct'), _read(device/'serial')) == \
                (f'{vid:04x}', f'{pid:04x}', serial):
            found.append(device.name)
    if not found:
        raise ValueError('The phone is visible to ADB, but Linux did not report it as a USB device '
                         '(0e8d:201c). Connect it directly by USB cable and check again.')
    if len(found) > 1:
        raise ValueError('USB location is ambiguous; no phone was rebooted')
    return found[0]
