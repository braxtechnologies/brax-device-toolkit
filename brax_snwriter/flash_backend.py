"""Explicit adapter to the installed SP Flash Tool CLI; not a native writer."""
import hashlib
import json
import re
import subprocess
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from .flash_plan import review_scatter
from .compatibility import require_verified_compatibility

MODES = {
    'download': ('Download Only', 'Write the images selected by the package. A selected userdata image replaces user data. Unselected factory partitions are not intentionally written.'),
    'firmware-upgrade': ('Firmware Upgrade', 'Use the vendor upgrade operation to update firmware and handle partition-layout changes. Treat this as data-destructive; retention of factory records has not been validated on BraX3.'),
    'format': ('Format Only', 'Erase flash using the vendor format operation. This does not install an OS. It may remove factory identity and calibration records and leave the phone unable to boot.'),
    'format-download': ('Format + Download', 'Run the vendor format-and-download operation. It may erase factory identity and calibration records before installing the selected firmware.'),
}


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def xml(path):
    with Path(path).open('rb') as source:
        data = source.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Unsupported firmware XML')
    return ET.fromstring(data), data


def prepare(tool, manifest, mode, device):
    if mode not in MODES:
        raise ValueError('Unsupported operation')
    tool, manifest = Path(tool).resolve(), Path(manifest).resolve()
    if not tool.is_file() or tool.name.lower() != 'spflashtoolv6.exe':
        raise ValueError('Choose the installed SPFlashToolV6.exe')
    if device.get('handshake_verified') is not True or device.get('hw_code') != '0x1209' or not re.fullmatch(r'COM[1-9][0-9]*', device.get('port', '')) or not device.get('usb_location'):
        raise ValueError('Check the powered-off BraX3 connection first')
    require_verified_compatibility(device, manifest, mode)
    root, _ = xml(manifest)
    if root.tag != 'flash-mode' or root.findtext('project') != 'k6835v1_64':
        raise ValueError('Expected a BraX3 MT6835 flash.xml')
    if root.findtext('dagent') != 'DA_BR.bin' or root.findtext('scatter') != '../MT6835_Android_scatter.xml':
        raise ValueError('Unsupported package agent/scatter layout')
    base = manifest.parent.parent
    scatter = base / 'MT6835_Android_scatter.xml'
    _, scatter_data = xml(scatter)
    plan = review_scatter(scatter_data, 'EMMC')
    paths = [tool, manifest, scatter, manifest.parent/'DA_BR.bin']
    if mode != 'format':
        paths += [base/p['image'] for p in plan['partitions']]
    fingerprints = {}
    for path in paths:
        if path != tool and not path.resolve().is_relative_to(base):
            raise ValueError('Package file leaves package directory')
        fingerprints[str(path)] = digest(path)
    command = [str(tool), '-c', mode, '-f', str(manifest), '-p', device['port'], '-t', 'with', '-l', 'USB', '-r']
    if mode != 'format':
        command.append('-b')
    return {'command': command, 'mode': mode, 'device': dict(device), 'files': fingerprints,
            'partitions': plan['partitions'] if mode != 'format' else [],
            'confirmation': ('ERASE ' if mode != 'download' else 'INSTALL ') + device['port'],
            'backend': 'External SP Flash Tool', 'publisher_signature_verified': False}


def execute(plan, confirmation, records, emit, *, popen=subprocess.Popen, ports=None):
    if confirmation != plan['confirmation']:
        raise ValueError('Operation confirmation does not match the reviewed port')
    # Rebuild to reject changed files, mode, device or command after review.
    current = prepare(plan['command'][0], plan['command'][4], plan['mode'], plan['device'])
    if current != plan:
        raise ValueError('The reviewed inputs changed; review again before starting')
    if ports is None:
        from serial.tools.list_ports import comports
        ports = comports()
    from .boot_probe import candidates
    detected = candidates(ports)
    if len(detected) > 1 or any(p.device != plan['device']['port'] or p.location != plan['device']['usb_location'] for p in detected):
        raise ValueError('Boot connection differs from the reviewed phone; no command started')
    folder = Path(records)/('flash-'+uuid.uuid4().hex)
    folder.mkdir(parents=True)
    (folder/'review.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    result = {'state': 'starting', 'mode': plan['mode'], 'boot_verified': False,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'record': str(folder)}
    result_path = folder/'result.json'
    result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    emit('External SP Flash Tool: connect ONLY the reviewed powered-off phone now. Keep USB connected until the operation finishes.')
    try:
        with (folder/'debug.log').open('w', encoding='utf-8') as log:
            process = popen(plan['command'], cwd=Path(plan['command'][0]).parent,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            result['pid'] = process.pid
            result['state'] = 'running'
            result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
            success = False
            for line in process.stdout:
                log.write(line)
                log.flush()
                emit(line.rstrip())
                success |= line.strip() == 'All command exec done!'
            result['exit_code'] = process.wait()
            if result['exit_code'] or not success:
                raise RuntimeError('Vendor operation failed or completion was not confirmed. Preserve the phone connection and inspect the debug log: '+str(folder))
        result['state'] = 'tool_completed'
        return result
    except Exception as exc:
        result['state'] = 'failed_or_unconfirmed'
        result['error'] = str(exc)
        raise
    finally:
        result['finished_utc'] = datetime.now(timezone.utc).isoformat()
        result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
