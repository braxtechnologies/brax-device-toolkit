from pathlib import Path
import struct
import tempfile
import unittest
from brax_snwriter.framing import Frame, encode, decode
from brax_snwriter.session import MetaSession, IndeterminateWrite
from brax_snwriter.serial_record import BASEOS_FINGERPRINT, patch_serial, change_serial, restore_serial

OLD = "BRAXUNIT00000001"
NEW = "1RK6USMQL7N0CXTO"


def record():
    result = bytearray(bytes(range(256)) * 4)
    result[116:136] = OLD.encode().ljust(20, b"\x00")
    return bytes(result)


class DeviceFixture:
    def __init__(self):
        self.data = record()
        self.writes = 0
        self.reads = 0
        self.fingerprint = BASEOS_FINGERPRINT
        self.serial = OLD
        self.corrupt = False
        self.race = False

    def property(self, key):
        return self.fingerprint if key == "ro.build.fingerprint" else self.serial

    def read_record(self, lid, rec):
        assert (lid, rec) == (61, 1)
        self.reads += 1
        if self.race and self.reads == 2:
            self.data = b"!" + self.data[1:]
        return self.data

    def write_record(self, lid, rec, data):
        assert (lid, rec) == (61, 1)
        self.writes += 1
        self.data = (b"!" + data[1:]) if self.corrupt else data


class StreamFixture:
    def __init__(self, fail=False):
        self.response = bytearray()
        self.fail = fail
        self.writes = 0

    def write(self, wire):
        self.writes += 1
        req = decode(wire, direction="request")
        token, command, lid, rec = struct.unpack("<HHHH", req.local)
        peer = record() if command == 22 else b""
        self.response.extend(encode(Frame(struct.pack("<HHHHBB", token, command + 1, lid, rec, 1, 0), peer), direction="response"))
        return len(wire) - 1 if self.fail else len(wire)

    def read(self, size):
        data = bytes(self.response[:size])
        del self.response[:size]
        return data


class SerialTests(unittest.TestCase):
    def test_blank_serial_requires_explicit_initialization(self):
        with self.assertRaises(ValueError): patch_serial(bytes(1024), OLD, NEW)
        changed = patch_serial(bytes(1024), OLD, NEW, initialize_blank=True)
        self.assertEqual(changed[116:136], NEW.encode()+bytes(4))
        with self.assertRaises(ValueError): patch_serial(b'x'*1024, OLD, NEW, initialize_blank=True)

    def test_restore_preserves_original_blank_field(self):
        device = DeviceFixture()
        device.data = bytes(1024)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'backup'
            change_serial(device, OLD, NEW, dest, initialize_blank=True)
            device.serial = NEW
            restore_serial(device, dest)
            self.assertEqual(device.data, bytes(1024))
            self.assertTrue((dest / 'restored.json').exists())

    def test_restore_refuses_unrelated_changes(self):
        device = DeviceFixture()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'backup'
            change_serial(device, OLD, NEW, dest)
            device.data = b'!' + device.data[1:]
            with self.assertRaises(ValueError): restore_serial(device, dest)
            self.assertEqual(device.writes, 1)

    def test_only_serial_bytes_change(self):
        a = record()
        b = patch_serial(a, OLD, NEW)
        self.assertEqual(a[:116], b[:116])
        self.assertEqual(a[136:], b[136:])
        self.assertEqual(b[116:136], NEW.encode() + b"\x00" * 4)

    def test_wrong_record_rejected(self):
        for data in (record()[:-1], b"x" * 1024):
            with self.assertRaises(ValueError):
                patch_serial(data, OLD, NEW)

    def test_invalid_serials(self):
        for new in (OLD, "abc", "x" * 16, "A" * 20, "A" * 15 + "\x00"):
            with self.assertRaises(ValueError):
                patch_serial(record(), OLD, new)

    def test_backup_precedes_single_write_and_full_readback(self):
        device = DeviceFixture()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "backup"
            original_write = device.write_record
            def checked_write(*args):
                self.assertEqual((dest / "product-info.original.bin").read_bytes(), record())
                self.assertTrue((dest / "write-attempted").exists())
                original_write(*args)
            device.write_record = checked_write
            result = change_serial(device, OLD, NEW, dest)
            self.assertTrue(result["record_readback_verified"])
            self.assertFalse(result["reboot_persistence_verified"])
            self.assertEqual(device.writes, 1)
            self.assertEqual(device.reads, 3)

    def test_wrong_firmware_and_device_do_not_write(self):
        for field in ("fingerprint", "serial"):
            d = DeviceFixture()
            setattr(d, field, "wrong")
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    change_serial(d, OLD, NEW, Path(tmp) / "backup")
            self.assertEqual(d.writes, 0)

    def test_existing_backup_not_overwritten(self):
        d = DeviceFixture()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                change_serial(d, OLD, NEW, tmp)
        self.assertEqual(d.writes, 0)

    def test_changed_record_aborts_before_write(self):
        d = DeviceFixture()
        d.race = True
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                change_serial(d, OLD, NEW, Path(tmp) / "backup")
        self.assertEqual(d.writes, 0)

    def test_corrupt_readback_no_automatic_retry(self):
        d = DeviceFixture()
        d.corrupt = True
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "backup"
            with self.assertRaises(IndeterminateWrite):
                change_serial(d, OLD, NEW, dest)
            self.assertFalse((dest / "verified.json").exists())
            self.assertTrue((dest / "product-info.original.bin").exists())
        self.assertEqual(d.writes, 1)

    def test_actual_packet_read_write(self):
        stream = StreamFixture()
        session = MetaSession(stream)
        self.assertEqual(session.read_record(61), record())
        session.write_record(61, 1, record())
        self.assertEqual(stream.writes, 2)

    def test_partial_write_invalidates_session(self):
        stream = StreamFixture(fail=True)
        session = MetaSession(stream)
        with self.assertRaises(IndeterminateWrite):
            session.write_record(61, 1, record())
        with self.assertRaises(RuntimeError):
            session.write_record(61, 1, record())
        self.assertEqual(stream.writes, 1)
