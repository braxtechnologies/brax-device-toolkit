"""Read-only snapshots and reviewed restores; no automatic writes or retries."""
import hashlib
import json
from pathlib import Path
from .serial_record import BASEOS_FINGERPRINT, PRODUCT_LID, SERIAL_OFFSET, SERIAL_WIDTH, _save_exclusive, restore_serial
from .imei import inspect_imeis, decode_record, restore_imeis
from .session import IndeterminateWrite


def serial_text(record):
    field = record[SERIAL_OFFSET:SERIAL_OFFSET+SERIAL_WIDTH]
    return field.split(b'\0',1)[0].decode('ascii', errors='replace') or '(empty stored serial)'


def imei_text(record):
    if record == b'\xff'*10:
        return '(uninitialized IMEI)'
    try:
        return decode_record(record)
    except ValueError:
        return '(unrecognized record)'


def capture(ap, modem, serial):
    if ap.property('ro.vendor.serialno') != serial or ap.property('ro.build.fingerprint') != BASEOS_FINGERPRINT:
        raise ValueError('Phone identity or OS changed. No records saved or written.')
    product = ap.read_record(PRODUCT_LID, 1)
    if len(product) != 1024:
        raise ValueError('Unexpected serial record size')
    _, records, _ = inspect_imeis(modem, serial)
    return {'serial':serial, 'fingerprint':BASEOS_FINGERPRINT, 'product':product.hex(),
            'imeis':{str(i):data.hex() for i,data in records.items()}}


def save_snapshot(snapshot, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    payload = json.dumps(snapshot, sort_keys=True).encode()
    _save_exclusive(directory/'snapshot.json', payload)
    _save_exclusive(directory/'snapshot.sha256', hashlib.sha256(payload).hexdigest().encode())
    return str(directory)


def describe(snapshot):
    return {'serial':serial_text(bytes.fromhex(snapshot['product'])),
            'SIM 1':imei_text(bytes.fromhex(snapshot['imeis']['1'])),
            'SIM 2':imei_text(bytes.fromhex(snapshot['imeis']['2']))}


def load_target(directory, scope, current):
    """Resolve a snapshot or existing transaction backup and check its phone binding."""
    directory = Path(directory)
    if (directory/scope).is_dir():
        directory = directory/scope
    if (directory/'snapshot.json').exists():
        payload = (directory/'snapshot.json').read_bytes()
        if hashlib.sha256(payload).hexdigest() != (directory/'snapshot.sha256').read_text().strip():
            raise ValueError('Backup checksum mismatch')
        target = json.loads(payload)
        if target['serial'] != current['serial'] or target['fingerprint'] != current['fingerprint']:
            raise ValueError('Snapshot phone identity does not match. If a serial write changed the identity, select that write transaction backup instead.')
        if len(bytes.fromhex(target['product'])) != 1024 or set(target['imeis']) != {str(i) for i in range(1,11)} or any(len(bytes.fromhex(x)) != 10 for x in target['imeis'].values()):
            raise ValueError('Incomplete backup')
        return {'kind':'snapshot','directory':str(directory),'target':target}
    target = dict(current, imeis=dict(current['imeis']))
    if scope == 'serial':
        manifest = json.loads((directory/'intent.json').read_text())
        original = (directory/'product-info.original.bin').read_bytes()
        if hashlib.sha256(original).hexdigest() != manifest['original_sha256'] or len(original) != 1024:
            raise ValueError('Serial backup checksum or size mismatch')
        if current['serial'] not in (manifest['old_serial'],manifest['new_serial']):
            raise ValueError('Serial backup belongs to another phone')
        if hashlib.sha256(bytes.fromhex(current['product'])).hexdigest() != manifest['intended_sha256']:
            raise ValueError('Current serial record no longer matches this transaction')
        target['product'] = original.hex()
    else:
        manifest = json.loads((directory/'imei-transaction.json').read_text())
        if manifest['identity']['serial'] != current['serial']:
            raise ValueError('IMEI backup identity differs. Restore serial and reboot first if needed.')
        for i, record in manifest['records'].items():
            if hashlib.sha256(bytes.fromhex(record['hex'])).hexdigest() != record['sha256']:
                raise ValueError('IMEI backup checksum mismatch')
            target['imeis'][i] = record['hex']
    return {'kind':'transaction','directory':str(directory),'target':target}


def restore_reviewed(ap, modem, selected_serial, plan, scope, journal_directory):
    """Recheck the full reviewed state, save a fresh backup, then restore one scope."""
    current = capture(ap, modem, selected_serial)
    if current != plan['current']:
        raise ValueError('Phone records changed since review. Review again; no write attempted.')
    target = plan['target']
    directory = Path(journal_directory)
    save_snapshot(current, directory)
    _save_exclusive(directory/'review.json', json.dumps({'scope':scope,'target':target}).encode())
    if plan['kind'] == 'transaction':
        _save_exclusive(directory/'write-attempted', b'Reconcile original transaction and this backup before retry.\n')
        if scope == 'serial':
            restore_serial(ap, plan['directory'])
        else:
            restore_imeis(modem, selected_serial, plan['directory'])
    else:
        if scope == 'serial':
            before, after = bytes.fromhex(current['product']), bytes.fromhex(target['product'])
            end = SERIAL_OFFSET+SERIAL_WIDTH
            if before[:SERIAL_OFFSET] != after[:SERIAL_OFFSET] or before[end:] != after[end:]:
                raise ValueError('Other product fields differ; refusing to overwrite unrelated data.')
        elif any(current['imeis'][str(i)] != target['imeis'][str(i)] for i in range(3,11)):
            raise ValueError('Other modem records differ; refusing to overwrite unrelated data.')
        _save_exclusive(directory/'write-attempted', b'Reconcile this backup before retry.\n')
        try:
            if scope == 'serial':
                ap.write_record(PRODUCT_LID, 1, bytes.fromhex(target['product']))
                if ap.read_record(PRODUCT_LID, 1).hex() != target['product']:
                    raise ValueError('Serial readback mismatch')
            else:
                for i in (1,2):
                    modem.write_imei_record(i, bytes.fromhex(target['imeis'][str(i)]))
                if any(modem.read_imei_record(i).hex() != target['imeis'][str(i)] for i in range(1,11)):
                    raise ValueError('IMEI readback mismatch')
        except Exception as exc:
            raise IndeterminateWrite('Restore may have been written. Keep the backup and reconcile before retrying.') from exc
    _save_exclusive(directory/'restored.json',json.dumps({'scope':scope,'record_readback_verified':True,
                     'reboot_persistence_verified':False}).encode())
    return {'scope':scope,'backup':str(directory),'record_readback_verified':True,'reboot_persistence_verified':False}
