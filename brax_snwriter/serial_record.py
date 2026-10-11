"""MT6835 PRODUCT_INFO serial-only update, with backup and readback.

Profile scope is deliberately one exact firmware fingerprint. Live validation
of this source-derived profile remains required. No raw partition writes.
"""
import hashlib
import json
import os
from pathlib import Path
import re
from .session import IndeterminateWrite

BASEOS_FINGERPRINT = "BraX/baseos_brax3/brax3:16/BP4A.251205.006/58:user/release-keys"
PRODUCT_LID = 61
RECORD_SIZE = 1024
SERIAL_OFFSET = 116
SERIAL_WIDTH = 20


def validate_serial(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{16}", value):
        raise ValueError("this test profile requires 16 uppercase letters/digits")
    return value.encode("ascii")


def patch_serial(original, expected, replacement, *, initialize_blank=False):
    before = validate_serial(expected)
    after = validate_serial(replacement)
    if len(original) != RECORD_SIZE:
        raise ValueError("PRODUCT_INFO record size differs from confirmed source layout")
    field = original[SERIAL_OFFSET:SERIAL_OFFSET + SERIAL_WIDTH]
    if field == bytes(SERIAL_WIDTH) and not initialize_blank:
        raise ValueError("Stored serial field is empty. No serial write attempted. "
                         "To provision this phone, explicitly enable 'Allow an empty stored serial field' "
                         "in Advanced tools, then review the assigned identifiers again.")
    if field.split(b"\x00", 1)[0] != before and not (initialize_blank and field == bytes(SERIAL_WIDTH)):
        raise ValueError("stored serial does not match selected device")
    if before == after:
        raise ValueError("replacement serial must differ")
    return original[:SERIAL_OFFSET] + after.ljust(SERIAL_WIDTH, b"\x00") + original[SERIAL_OFFSET + SERIAL_WIDTH:]


def _save_exclusive(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def change_serial(session, expected, replacement, backup_directory, *, initialize_blank=False):
    """Verify identity, back up, recheck, write once, compare the entire record.

    Backup directory must not already exist. A successful return verifies the
    NVRAM record only; reboot persistence and ro.boot.serialno need a later check.
    """
    validate_serial(expected)
    validate_serial(replacement)
    if session.property("ro.build.fingerprint") != BASEOS_FINGERPRINT:
        raise ValueError("device firmware does not match the scoped BaseOS profile")
    if session.property("ro.vendor.serialno") != expected:
        raise ValueError("META endpoint is not the selected device")
    original = session.read_record(PRODUCT_LID, 1)
    modified = patch_serial(original, expected, replacement, initialize_blank=initialize_blank)
    directory = Path(backup_directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    _save_exclusive(directory / "product-info.original.bin", original)
    manifest = {
        "fingerprint": BASEOS_FINGERPRINT, "old_serial": expected,
        "new_serial": replacement, "lid": PRODUCT_LID, "record": 1,
        "size": RECORD_SIZE, "offset": SERIAL_OFFSET, "width": SERIAL_WIDTH,
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "intended_sha256": hashlib.sha256(modified).hexdigest(),
        "original_serial_field_hex": original[SERIAL_OFFSET:SERIAL_OFFSET + SERIAL_WIDTH].hex(),
        "initialize_blank": initialize_blank,
    }
    _save_exclusive(directory / "intent.json", json.dumps(manifest, indent=2).encode())
    if session.read_record(PRODUCT_LID, 1) != original:
        raise ValueError("record changed after backup; no write attempted")
    # A durable marker precedes transmission so interrupted runs never imply no write.
    _save_exclusive(directory / "write-attempted", b"reconcile before any retry\n")
    session.write_record(PRODUCT_LID, 1, modified)
    try:
        actual = session.read_record(PRODUCT_LID, 1)
        if actual != modified:
            raise ValueError("full-record readback mismatch")
    except (OSError, ValueError, RuntimeError) as exc:
        raise IndeterminateWrite("write sent but readback unverified; preserve backup, no retry") from exc
    result = {**manifest, "record_readback_verified": True, "reboot_persistence_verified": False}
    _save_exclusive(directory / "verified.json", json.dumps(result, indent=2).encode())
    return result


def restore_serial(session, backup_directory):
    """Restore the exact pre-test record only if device and current record match."""
    directory = Path(backup_directory)
    manifest = json.loads((directory / "intent.json").read_text())
    original = (directory / "product-info.original.bin").read_bytes()
    if hashlib.sha256(original).hexdigest() != manifest["original_sha256"]:
        raise ValueError("backup checksum mismatch")
    if session.property("ro.build.fingerprint") != BASEOS_FINGERPRINT:
        raise ValueError("firmware mismatch on restore")
    if session.property("ro.vendor.serialno") not in (manifest["old_serial"], manifest["new_serial"]):
        raise ValueError("wrong device identity on restore")
    expected = patch_serial(original, manifest["old_serial"], manifest["new_serial"],
                            initialize_blank=manifest["initialize_blank"])
    current = session.read_record(PRODUCT_LID, 1)
    if current != expected:
        raise ValueError("record changed since test; refusing to overwrite other changes")
    _save_exclusive(directory / "restore-attempted", b"reconcile before retry\n")
    session.write_record(PRODUCT_LID, 1, original)
    try:
        if session.read_record(PRODUCT_LID, 1) != original:
            raise ValueError("restored record differs from original")
    except (OSError, ValueError, RuntimeError) as exc:
        raise IndeterminateWrite("restore unverified; keep backup and reconcile") from exc
    _save_exclusive(directory / "restored.json", json.dumps({"original_sha256": manifest["original_sha256"],
                     "record_restored": True, "reboot_persistence_verified": False}).encode())
