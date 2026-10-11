"""Scoped BraX3 MAC record transactions. Never write calibration or retry writes."""
import hashlib
import json
import re
from pathlib import Path
from .serial_record import BASEOS_FINGERPRINT, _save_exclusive
from .session import IndeterminateWrite

# Compiled from k6835v1_64 cgen headers; exact full record sizes required.
PROFILES = {'bluetooth': (1, 2414, 0), 'wifi': (55, 2048, 4)}

def parse_address(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}', value):
        raise ValueError('Use six hexadecimal pairs, for example 02:12:34:56:78:9A.')
    raw = bytes.fromhex(value.replace(':', ''))
    if raw == bytes(6) or raw[0] & 1:
        raise ValueError('Use an assigned unicast address; zero, multicast and broadcast addresses are invalid.')
    return raw

def display(raw):
    return ':'.join(f'{b:02X}' for b in raw)

def capture(session, serial):
    if session.property('ro.build.fingerprint') != BASEOS_FINGERPRINT or session.property('ro.vendor.serialno') != serial:
        raise ValueError('Phone identity or firmware differs from the BraX3 profile.')
    result = {'serial': serial, 'fingerprint': BASEOS_FINGERPRINT, 'records': {}}
    for name, (lid, size, offset) in PROFILES.items():
        data = session.read_record(lid, 1)
        if len(data) != size:
            raise ValueError(f'{name}: record size {len(data)} differs from expected {size}; no write allowed.')
        result['records'][name] = data.hex()
    return result

def addresses(snapshot):
    return {name: display(bytes.fromhex(snapshot['records'][name])[offset:offset+6])
            for name, (_, _, offset) in PROFILES.items()}

def plan(snapshot, changes):
    if not changes or set(changes) - PROFILES.keys():
        raise ValueError('Select Bluetooth, Wi-Fi, or both.')
    target = dict(snapshot, records=dict(snapshot['records']))
    for name, value in changes.items():
        raw = parse_address(value)
        data = bytes.fromhex(snapshot['records'][name])
        _, size, offset = PROFILES[name]
        if len(data) != size: raise ValueError('Unexpected record size')
        target['records'][name] = (data[:offset]+raw+data[offset+6:]).hex()
    return {'before': snapshot, 'after': target}

def validate_plan(review):
    before, after = review['before'], review['after']
    if before['serial'] != after['serial'] or before['fingerprint'] != BASEOS_FINGERPRINT or after['fingerprint'] != BASEOS_FINGERPRINT:
        raise ValueError('Invalid backup identity')
    if set(before['records']) != set(PROFILES) or set(after['records']) != set(PROFILES):
        raise ValueError('Incomplete MAC backup')
    for name, (_, size, offset) in PROFILES.items():
        a, b = bytes.fromhex(before['records'][name]), bytes.fromhex(after['records'][name])
        if len(a) != size or len(b) != size or a[:offset] != b[:offset] or a[offset+6:] != b[offset+6:]:
            raise ValueError('Refusing a change to calibration or unrelated bytes')

def write_reviewed(session, serial, review, directory):
    validate_plan(review)
    if review['before']['serial'] != serial or capture(session, serial) != review['before']:
        raise ValueError('Phone records changed since review. Read and review again.')
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=False)
    payload = json.dumps(review, sort_keys=True).encode()
    _save_exclusive(folder/'mac-transaction.json', payload)
    _save_exclusive(folder/'mac-transaction.sha256', hashlib.sha256(payload).hexdigest().encode())
    if capture(session, serial) != review['before']:
        raise ValueError('Records changed after backup; no write attempted.')
    _save_exclusive(folder/'write-attempted', b'Reconcile this transaction before retrying.\n')
    try:
        for name, (lid, _, _) in PROFILES.items():
            if review['before']['records'][name] != review['after']['records'][name]:
                session.write_record(lid, 1, bytes.fromhex(review['after']['records'][name]))
        if capture(session, serial) != review['after']:
            raise ValueError('Full-record readback mismatch')
    except Exception as exc:
        raise IndeterminateWrite(f'MAC write needs recovery. Keep backup {folder}; no retry performed.') from exc
    _save_exclusive(folder/'readback-verified', b'Reboot persistence still requires verification.\n')
    return str(folder)

def restore_plan(directory, current):
    folder = Path(directory)
    payload = (folder/'mac-transaction.json').read_bytes()
    if hashlib.sha256(payload).hexdigest() != (folder/'mac-transaction.sha256').read_text().strip():
        raise ValueError('MAC backup checksum mismatch')
    old = json.loads(payload)
    validate_plan(old)
    if current['serial'] != old['before']['serial'] or current['fingerprint'] != old['before']['fingerprint']:
        raise ValueError('Backup belongs to a different phone or firmware')
    # Permit an interrupted two-record transaction, but never unexplained changes.
    for name in PROFILES:
        if current['records'][name] not in (old['before']['records'][name], old['after']['records'][name]):
            raise ValueError('Current records no longer match this backup transaction')
    return {'before': current, 'after': old['before']}
