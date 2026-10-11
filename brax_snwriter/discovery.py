"""Read-only Windows USB topology lookup for an explicitly selected phone."""
import json
import os
from pathlib import Path
import re
import subprocess
from .serial_record import BASEOS_FINGERPRINT, validate_serial
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def adb_states(output):
    return {parts[0]: parts[1] for line in output.splitlines()
            if len(parts := line.split()) >= 2 and parts[1] in
            ('device', 'unauthorized', 'offline', 'recovery', 'sideload')}


def debugging_guidance(state):
    if state == 'unauthorized':
        return 'Authorization pending. Unlock the phone and accept Allow USB debugging, then refresh and check again.'
    if state == 'offline':
        return 'Phone is offline. Unlock it, reconnect the USB cable, then refresh and check again.'
    if state:
        return 'Phone is not in normal Android mode. Boot Android before checking USB debugging.'
    return ('Phone not visible to ADB. Enable Developer options and USB debugging, unlock the phone, '
            'and check the data cable/USB driver. If already in META mode, use Detect META phone in Advanced tools.')


def usb_location_from_path(path):
    parts = list(re.finditer(r'USBROOT\((\d+)\)|#USB\((\d+)\)', path))
    if len(parts) < 2 or parts[0].group(1) is None:
        raise ValueError('Windows did not provide a usable USB topology path')
    return str(int(parts[0].group(1))+1)+'-'+'.'.join(p.group(2) for p in parts[1:])


def check_connection(adb, serial):
    if not serial:
        raise ValueError('Select a phone after Refresh devices. '+debugging_guidance(None))
    validate_serial(serial)
    listing = subprocess.check_output([adb,'devices','-l'], text=True, stderr=subprocess.PIPE,
                                     timeout=10, creationflags=NO_WINDOW)
    state = adb_states(listing).get(serial)
    if state != 'device':
        raise ValueError(debugging_guidance(state))
    props = {k:subprocess.check_output([adb,'-s',serial,'shell','getprop',k],
             text=True,stderr=subprocess.PIPE,timeout=10,creationflags=NO_WINDOW).strip()
             for k in ('ro.serialno','ro.build.fingerprint','ro.bootmode')}
    if props['ro.serialno'] != serial or props['ro.build.fingerprint'] != BASEOS_FINGERPRINT:
        raise ValueError('Identifier repair supports BaseOS build 58 / P18. Select the BaseOS phone; iodé and emulators are not supported.')
    if props['ro.bootmode'] != 'normal':
        raise ValueError('Phone is already outside normal mode. Refresh ports and select its META COM port.')
    if os.name != 'nt':
        raise ValueError('Automatic USB topology discovery is Windows-only; enter the verified physical location manually')
    # Serial is restricted to 16 ASCII uppercase alphanumeric characters above.
    script = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    script += f"$p=Get-PnpDeviceProperty -InstanceId 'USB\\VID_0E8D&PID_201C\\{serial}' -KeyName DEVPKEY_Device_LocationPaths; @($p.Data) | ConvertTo-Json -Compress"
    exe = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    raw = subprocess.check_output([str(exe),'-NoProfile','-NonInteractive','-Command',script],
                                  timeout=25,stderr=subprocess.PIPE,creationflags=NO_WINDOW)
    paths = json.loads(raw.decode('utf-8-sig'))
    if isinstance(paths,str):paths=[paths]
    locations={usb_location_from_path(p) for p in paths if 'USBROOT(' in p}
    if len(locations)!=1:
        raise ValueError('USB location is ambiguous; no phone was rebooted')
    return {'serial':serial,'usb_location':locations.pop(),'fingerprint':BASEOS_FINGERPRINT,'bootmode':'normal'}
