"""Read-only checks of what this computer needs before a phone is touched.

Nothing here writes to a phone, installs software or stops another program.
Each check returns one Check: a state, a sentence for the person using the app
and the name of the action that fixes it. The window lives in computer_card.py.
"""
from collections import namedtuple
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import urllib.request

from . import firmware_catalog
from .device_connection import NO_WINDOW

Check = namedtuple('Check', 'key title state message fix detail', defaults=(None, ''))
# States. Only MISSING on a required item stops the guided steps.
OK, MISSING, WARNING, OPTIONAL, UNKNOWN = 'ok', 'missing', 'warning', 'optional', 'unknown'

PLATFORM_TOOLS_PAGE = 'https://developer.android.com/tools/releases/platform-tools'
# USB product IDs a BraX3 uses; other MediaTek devices (a Bluetooth adapter,
# for one) share the vendor ID and are ignored.
PHONE_MODES = {'201C': 'Android with USB debugging', '200E': 'META service mode',
               '2000': 'powered-off connection (preloader)', '0003': 'powered-off connection (boot ROM)'}
LOW_DISK_BYTES = 10 * 1024**3


def adb_candidates(saved, data):
    """Places an adb.exe is commonly found, most specific first."""
    seen, found = set(), []
    def add(value):
        if not value:
            return
        path = Path(os.path.expandvars(os.path.expanduser(str(value).strip().strip('"'))))
        key = str(path).lower()
        if path.is_absolute() and key not in seen:
            seen.add(key); found.append(path)
    add(saved)
    add(shutil.which(saved) if saved else None)
    add(shutil.which('adb'))
    add(Path(data)/'tools'/'platform-tools'/'adb.exe')
    for folder in ('%ProgramFiles%/platform-tools', '%LOCALAPPDATA%/Android/Sdk/platform-tools',
                   '%SystemDrive%/platform-tools', '%USERPROFILE%/Downloads/platform-tools',
                   '%USERPROFILE%/platform-tools', '%SystemDrive%/adb'):
        expanded = os.path.expandvars(folder)
        if '%' not in expanded:
            add(Path(expanded)/'adb.exe')
    return found


def adb_version(path, runner=subprocess.run):
    """Start `adb version`, which reads no phone and starts no ADB server."""
    result = runner([str(path), 'version'], capture_output=True, text=True, timeout=10,
                    creationflags=NO_WINDOW)
    match = re.search(r'^Version\s+(\S+)', result.stdout or '', re.M)
    if result.returncode != 0 or 'Android Debug Bridge' not in (result.stdout or ''):
        raise ValueError('It did not answer as Android Debug Bridge')
    return match[1] if match else 'unknown version'


def check_adb(saved, data, runner=subprocess.run):
    problems = []
    for path in adb_candidates(saved, data):
        if not path.is_file():
            continue
        try:
            version = adb_version(path, runner)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            problems.append(f'{path}: {exc}')
            continue
        return Check('adb', 'ADB (Android platform-tools)', OK, f'Version {version} at {path}', detail=str(path))
    if problems:
        return Check('adb', 'ADB (Android platform-tools)', MISSING,
                     'An adb.exe was found but does not run. Extract the whole platform-tools ZIP and keep '
                     'adb.exe beside its DLL files.', 'adb', '\n'.join(problems))
    return Check('adb', 'ADB (Android platform-tools)', MISSING,
                 'Not found on this computer. Download Android platform-tools, extract the whole ZIP, '
                 'then choose the adb.exe inside it.', 'adb')


def windows_snapshot(runner=subprocess.run):
    """One PowerShell start: MediaTek USB devices present now and running adb.exe paths."""
    if os.name != 'nt':
        return None
    script = ("$ErrorActionPreference='SilentlyContinue'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
              "$d=@(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\\VID_0E8D*' } | "
              "ForEach-Object { @{id=$_.InstanceId; status=[string]$_.Status; name=[string]$_.FriendlyName} }); "
              "$a=@(Get-Process adb | ForEach-Object { [string]$_.Path }); "
              "@{devices=$d; adb=$a} | ConvertTo-Json -Compress -Depth 4")
    exe = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = runner([str(exe), '-NoProfile', '-NonInteractive', '-Command', script],
                    capture_output=True, timeout=30, creationflags=NO_WINDOW)
    data = json.loads(result.stdout.decode('utf-8-sig'))
    return {'devices': list(data.get('devices') or []), 'adb': [p for p in (data.get('adb') or []) if p]}


def check_drivers(snapshot):
    title = 'Phone USB driver'
    if snapshot is None:
        return Check('driver', title, UNKNOWN, 'Could not ask Windows about USB devices.')
    modes = {}
    for device in snapshot['devices']:
        match = re.match(r'USB\\VID_0E8D&PID_([0-9A-F]{4})', str(device.get('id', '')).upper())
        if match and match[1] in PHONE_MODES:
            # A composite phone lists several entries; one without a driver is enough to fail.
            working = str(device.get('status', '')).upper() == 'OK'
            modes[match[1]] = modes.get(match[1], True) and working
    if not modes:
        return Check('driver', title, UNKNOWN,
                     'No BraX3 is connected, so the driver cannot be checked yet. Each connection (Android, '
                     'META, powered-off) uses its own driver; this line updates when you check again with the phone attached.')
    broken = [PHONE_MODES[pid] for pid, working in modes.items() if not working]
    if broken:
        return Check('driver', title, MISSING,
                     'Windows sees the phone in '+', '.join(broken)+' but has no working driver for it. '
                     'Install the phone USB driver, then reconnect the cable.', 'driver')
    return Check('driver', title, OK, 'Working for: '+', '.join(PHONE_MODES[pid] for pid in modes)+
                 '. Other connection modes are checked when the phone is in them.')


def check_other_adb(snapshot, chosen):
    title = 'Other ADB programs'
    if snapshot is None:
        return Check('other_adb', title, UNKNOWN, 'Could not list running programs.')
    chosen = str(chosen or '').lower()
    others = sorted({p for p in snapshot['adb'] if p.lower() != chosen})
    if others:
        return Check('other_adb', title, WARNING,
                     'Another ADB is running and can take the phone away from this app: '+'; '.join(others)+
                     '. Close the program that started it (Android Studio, a phone manager or a second copy of this app).')
    return Check('other_adb', title, OK, 'No other ADB is running.')


def check_agent(value):
    title = 'Download Agent (native install)'
    if not value:
        return Check('agent', title, OPTIONAL,
                     'Not chosen. Only the experimental native BaseOS installation needs it.', 'agent')
    from .native_flash import stock_agent
    try:
        stock_agent(value)
    except OSError:
        return Check('agent', title, WARNING, 'The chosen file can no longer be read: '+str(value), 'agent')
    except Exception as exc:  # includes struct errors on a truncated file
        return Check('agent', title, WARNING, f'{exc}. Chosen file: {value}', 'agent')
    return Check('agent', title, OK, 'The inspected stock agent: '+str(value))


def check_flash_tool(value):
    title = 'SP Flash Tool (external flashing)'
    if not value:
        return Check('flash_tool', title, OPTIONAL,
                     'Not set up. Only the external flash and format tools under Install OS need it; '
                     'identifier repair and the native installation do not.', 'flash_tool')
    from .flash_prerequisite import check_installation
    try:
        check_installation(value)
    except (ValueError, OSError) as exc:
        return Check('flash_tool', title, WARNING, str(exc), 'flash_tool')
    return Check('flash_tool', title, OK, 'Required files found at '+str(Path(value).parent))


def check_disk(data, usage=shutil.disk_usage):
    title = 'Free disk space'
    try:
        free = usage(str(data)).free
    except OSError as exc:
        return Check('disk', title, UNKNOWN, f'Could not read free space for {data}: {exc}')
    text = f'{free/1024**3:.1f} GB free where records and downloads are saved ({data})'
    if free < LOW_DISK_BYTES:
        return Check('disk', title, WARNING, text+'. An OS download and its unpacked files can need about 10 GB.', 'records')
    return Check('disk', title, OK, text)


def check_server(opener=urllib.request.urlopen):
    title = 'Download server'
    host = firmware_catalog.ROOT.split('/')[2]
    try:
        with opener(firmware_catalog.allowed_url(firmware_catalog.ROOT), timeout=8) as response:
            response.read(1)
    except Exception as exc:
        return Check('server', title, WARNING,
                     f'Cannot reach {host}. OS downloads will not work until it can be reached; '
                     'identifier repair does not need it.', detail=f'{type(exc).__name__}: {exc}')
    return Check('server', title, OK, f'{host} answered.')


def server_not_checked():
    return Check('server', 'Download server', UNKNOWN, 'Not contacted at start-up. Check again to test it.')


def run_checks(saved_adb, data, agent='', flash_tool='', network=False,
               runner=subprocess.run, opener=urllib.request.urlopen):
    """All checks in display order. Safe to call from a background thread."""
    adb = check_adb(saved_adb, data, runner)
    try:
        snapshot = windows_snapshot(runner)
    except (OSError, ValueError, subprocess.SubprocessError):
        snapshot = None
    return [adb, check_drivers(snapshot), check_other_adb(snapshot, adb.detail if adb.state == OK else ''),
            check_agent(agent), check_flash_tool(flash_tool), check_disk(data),
            check_server(opener) if network else server_not_checked()]


def required_missing(checks):
    """Items that are absent. Only a missing ADB locks the first guided step."""
    return [c for c in checks if c.state == MISSING]


def summary(checks):
    missing = required_missing(checks)
    if missing:
        return 'Needs attention: '+'; '.join(c.title for c in missing)
    warnings = [c.title for c in checks if c.state == WARNING]
    adb = next((c for c in checks if c.key == 'adb'), None)
    text = 'Ready · '+('ADB v'+adb.message[1:] if adb else 'checks passed')
    return text+(' · Warnings: '+', '.join(warnings) if warnings else '')


def report(checks):
    """Plain text for a support request. Holds no phone serial number."""
    marks = {OK: 'OK', MISSING: 'MISSING', WARNING: 'WARNING', OPTIONAL: 'not set up', UNKNOWN: 'not checked'}
    lines = ['Brax Device Toolkit · computer check', 'Windows: '+platform.platform(), '']
    for check in checks:
        lines.append(f'[{marks[check.state]}] {check.title}: {check.message}')
        if check.detail and check.detail not in check.message:
            lines.append('    '+check.detail.replace('\n', '\n    '))
    return '\n'.join(lines)
