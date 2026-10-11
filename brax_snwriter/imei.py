"""Backed-up, same-value IMEI rewrites for the validated P18 modem profile."""
import hashlib
import json
import os
import struct
from pathlib import Path
from .modem import BASEBAND
from .session import IndeterminateWrite


def validate_imei(value):
    if len(value) != 15 or not value.isascii() or not value.isdigit() or len(set(value)) == 1:
        raise ValueError('IMEI must contain 15 assigned ASCII digits, not a blank/repeated value')
    total = sum((int(c) * 2 // 10 + int(c) * 2 % 10) if i % 2 else int(c)
                for i, c in enumerate(value))
    if total % 10:
        raise ValueError('IMEI check digit does not match')
    return value


def decode_record(data):
    if len(data) != 10 or data[7] >> 4 != 15:
        raise ValueError('IMEI record is blank, masked, or has an unsupported encoding')
    digits = [n for b in data[:7] for n in (b & 15, b >> 4)] + [data[7] & 15]
    if any(d > 9 for d in digits):
        raise ValueError('IMEI record contains non-decimal data; no write allowed')
    return validate_imei(''.join(map(str, digits)))


def encode_record(imei, svn=0, pad=0):
    validate_imei(imei)
    if not 0 <= svn <= 255 or not 0 <= pad <= 255:
        raise ValueError('SVN and pad must be bytes')
    digits = [int(c) for c in imei] + [15]
    return bytes(digits[i] | (digits[i+1] << 4) for i in range(0,16,2)) + bytes([svn,pad])


def _save(path, value):
    """Flush transaction state before issuing a command that can change NVRAM."""
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def inspect_imeis(session, expected_serial):
    identity = session.connect(expected_serial)
    count = struct.unpack_from('<H', session.query_imei(8))[0]
    if count != 10:
        raise ValueError('IMEI record count does not match the P18 profile')
    records = {i: session.read_imei_record(i) for i in range(1, 11)}
    values = {}
    for i, data in records.items():
        try:
            values[i] = {'imei': decode_record(data), 'valid': True}
        except ValueError as exc:
            values[i] = {'valid': False, 'reason': str(exc)}
    return identity, records, values


def rewrite_existing(session, expected_serial, assigned_imeis, backup_directory):
    return write_assigned(session, expected_serial, assigned_imeis, backup_directory, existing_only=True)


def write_assigned(session, expected_serial, assigned_imeis, backup_directory, *, existing_only=False, initialize_blank=False):
    """Write only records 1/2, only after two read paths match assigned values.

    No rollback/retry is automatic. A partial transaction remains recorded for
    operator reconciliation; a successful response alone never means verified.
    """
    assigned = [validate_imei(v) for v in assigned_imeis]
    if not 1 <= len(assigned) <= 2 or len(set(assigned)) != len(assigned):
        raise ValueError('Supply one or two distinct existing assigned IMEIs')
    identity, records, values = inspect_imeis(session, expected_serial)
    directory = Path(backup_directory)
    directory.mkdir(parents=True, exist_ok=False)
    journal = {'identity': identity, 'baseband': BASEBAND,
               'operation': 'rewrite_existing_only' if existing_only else 'write_assigned',
               'status': 'backed_up', 'writes_attempted': [], 'records_verified': [],
               'reboot_persistence_verified': False,
               'records': {str(i): {'hex': data.hex(), 'sha256': hashlib.sha256(data).hexdigest()}
                           for i, data in records.items()}}
    path = directory / 'imei-transaction.json'
    _save(path, journal)
    try:
        planned = {}
        for i, assigned_value in enumerate(assigned, 1):
            if existing_only and (not values[i]['valid'] or values[i]['imei'] != assigned_value):
                raise ValueError(f'Record {i} does not match the existing assigned IMEI; no write allowed')
            if not values[i]['valid']:
                if not initialize_blank or records[i] != b'\xff' * 10:
                    raise ValueError(f'Record {i} is not valid; explicit FF-record initialization is required')
                planned[i] = encode_record(assigned_value)
            else:
                planned[i] = encode_record(assigned_value, *records[i][8:])
            queried = session.query_imei(1, i)
            if len(queried) != 22 or queried[20] != 10 or queried[:10] != records[i]:
                raise ValueError(f'Independent IMEI query disagrees with record {i}; no write allowed')
        journal['planned'] = {str(i): data.hex() for i, data in planned.items()}
        _save(path, journal)
        # Check all records again immediately before the first write.
        if any(session.read_imei_record(i) != data for i, data in records.items()):
            raise ValueError('Modem records changed since backup; no write allowed')
        for i in range(1, len(assigned) + 1):
            journal['status'] = 'write_outcome_pending'
            journal['writes_attempted'].append(i)
            _save(path, journal)
            session.write_imei_record(i, planned[i])
            if session.read_imei_record(i) != planned[i]:
                raise IndeterminateWrite(f'Record {i} readback differs; stop and reconcile the backup')
            journal['records_verified'].append(i)
            _save(path, journal)
        if any(session.read_imei_record(i) != planned.get(i, data) for i, data in records.items()):
            raise IndeterminateWrite('Full modem IMEI readback differs from backup')
        journal['status'] = 'same_values_written_and_read_back' if existing_only else 'assigned_values_written_and_read_back'
        _save(path, journal)
    except Exception as exc:
        journal['status'] = 'requires_reconciliation' if journal['writes_attempted'] else 'refused_before_write'
        journal['error'] = str(exc)
        _save(path, journal)
        raise
    return {'status': journal['status'], 'records_verified': journal['records_verified'],
            'backup': str(directory), 'reboot_persistence_verified': False}


def restore_imeis(session, expected_serial, backup_directory):
    directory = Path(backup_directory)
    journal = json.loads((directory / 'imei-transaction.json').read_text(encoding='utf-8'))
    if journal['identity']['serial'] != expected_serial or journal['baseband'] != BASEBAND:
        raise ValueError('Backup belongs to a different phone/profile')
    before = {int(i): bytes.fromhex(v['hex']) for i,v in journal['records'].items()}
    if set(before) != set(range(1,11)) or any(len(v) != 10 or hashlib.sha256(v).hexdigest() != journal['records'][str(i)]['sha256'] for i,v in before.items()):
        raise ValueError('Backup is incomplete or its hashes do not match')
    targets = journal['writes_attempted']
    if not targets or len(set(targets)) != len(targets) or not set(targets) <= {1,2}:
        raise ValueError('No valid written records to restore')
    planned = {int(i): bytes.fromhex(v) for i,v in journal['planned'].items()}
    _, current, _ = inspect_imeis(session, expected_serial)
    for i,data in current.items():
        if data != before[i] and (i not in targets or data != planned.get(i)):
            raise ValueError(f'Record {i} differs from both backup and planned write; reconcile manually')
    result = {'status': 'restore_started', 'writes_attempted': [], 'verified': [], 'reboot_persistence_verified': False}
    path = directory / 'imei-restore.json'
    if path.exists():
        raise ValueError('A restore journal already exists; reconcile it before another restore')
    _save(path,result)
    try:
        for i in targets:
            if current[i] == before[i]:
                continue
            result['writes_attempted'].append(i); _save(path,result)
            session.write_imei_record(i,before[i])
            if session.read_imei_record(i) != before[i]:
                raise IndeterminateWrite(f'Record {i} restoration readback mismatch')
            result['verified'].append(i); _save(path,result)
        if any(session.read_imei_record(i) != data for i,data in before.items()):
            raise IndeterminateWrite('Full restored record set does not match backup')
        result['status'] = 'all_records_match_original_backup'
        _save(path,result)
    except Exception as exc:
        result['status']='requires_reconciliation';result['error']=str(exc);_save(path,result)
        raise
    return result
