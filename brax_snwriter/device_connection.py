"""Verified transitions between Android and META service mode."""
import subprocess
import time
import struct
import os
from .framing import Frame, encode
from .meta_boot import enter_meta
from .session import open_serial, MetaSession
from .serial_record import BASEOS_FINGERPRINT, validate_serial
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def return_normal(adb, port, serial_number, *, timeout=180, report=None, expected_after=None):
    """Source-defined AP FT_REBOOT_REQ (118); target sends no confirm."""
    validate_serial(serial_number)
    expected_after = expected_after or serial_number
    validate_serial(expected_after)
    report = report or (lambda event: None)
    with open_serial(port) as stream:
        session = MetaSession(stream)
        if session.property('ro.vendor.serialno') != serial_number or session.property('ro.build.fingerprint') != BASEOS_FINGERPRINT:
            raise ValueError('META identity/firmware mismatch; reboot not sent')
        packet = encode(Frame(struct.pack('<HHII', session.next_token(), 118, 0, 0)), direction='request')
        if stream.write(packet) != len(packet):
            raise RuntimeError('Partial reboot command; inspect phone state before retrying')
        stream.flush()
    report({'state': 'normal_reboot_requested', 'serial': serial_number})
    deadline = time.monotonic() + timeout
    last_report = 0
    while time.monotonic() < deadline:
        try:
            def prop(name):
                return subprocess.check_output([adb, '-s', expected_after, 'shell', 'getprop', name],
                    text=True, stderr=subprocess.DEVNULL, timeout=5, creationflags=NO_WINDOW).strip()
            if prop('ro.bootmode') == 'normal' and prop('sys.boot_completed') == '1':
                if prop('ro.serialno') != expected_after or prop('ro.build.fingerprint') != BASEOS_FINGERPRINT:
                    raise ValueError('Normal-mode identity mismatch')
                return {'state': 'normal_boot_verified', 'serial': expected_after}
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        if time.monotonic() - last_report >= 15:
            report({'state': 'waiting_for_normal_boot', 'serial': expected_after})
            last_report = time.monotonic()
        time.sleep(1)
    raise TimeoutError('Normal boot not verified within timeout; do not assume reboot failed')


def boot_meta(adb, serial_number, usb_location, *, timeout=60, report=None):
    """usb_location must be established from the selected phone's USB topology.

    This function negotiates boot mode and queries identity. It never writes a
    factory record. The caller receives the newly assigned COM port only after
    firmware and serial validation. Port numbers are never reused by assumption.
    """
    from serial.tools import list_ports
    validate_serial(serial_number)
    if not usb_location or ':' in usb_location:
        raise ValueError("supply the physical USB location without an interface suffix")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    report = report or (lambda event: None)

    def adb_getprop(name):
        return subprocess.check_output([adb, '-s', serial_number, 'shell', 'getprop', name],
                                       text=True, timeout=10, creationflags=NO_WINDOW).strip()

    def matching_ports():
        return [p for p in list_ports.comports()
                if p.vid == 0x0e8d and p.location and p.location.split(':')[0] == usb_location]

    if adb_getprop('ro.build.fingerprint') != BASEOS_FINGERPRINT or adb_getprop('ro.serialno') != serial_number:
        raise ValueError("ADB target is not the expected BaseOS device")
    if matching_ports():
        raise ValueError("selected physical port already has a META/preloader endpoint; inspect first")
    # Watch enumeration while adb exits: the preloader may appear and disappear
    # before a blocking adb reboot process returns on Windows.
    reboot = subprocess.Popen([adb, '-s', serial_number, 'reboot'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    report({'state': 'reboot_requested', 'serial': serial_number})
    deadline = time.monotonic() + timeout
    negotiated = False
    while time.monotonic() < deadline:
        ports = matching_ports()
        preloader = [p for p in ports if p.pid == 0x2000 and p.device]
        ap = [p for p in ports if p.pid == 0x200e and p.device and p.location.endswith(':x.1')]
        if len(preloader) > 1 or len(ap) > 1:
            raise RuntimeError("ambiguous USB endpoints; no selection made")
        if preloader and not negotiated:
            try:
                stream = open_serial(preloader[0].device)
            except OSError as exc:
                # Enumeration can precede the COM device becoming openable.
                # No transport write has occurred; re-enumerate before retrying.
                report({'state': 'preloader_port_pending', 'port': preloader[0].device,
                        'error': str(exc)})
                time.sleep(0.02)
                continue
            with stream:
                transcript = enter_meta(stream)
            negotiated = True
            report({'state': 'preloader_acknowledged', 'transcript': transcript})
        if negotiated and ap:
            try:
                stream = open_serial(ap[0].device)
            except OSError as exc:
                report({'state': 'meta_port_pending', 'port': ap[0].device, 'error': str(exc)})
                time.sleep(0.02)
                continue
            with stream:
                session = MetaSession(stream, timeout=5)
                if session.property('ro.vendor.serialno') != serial_number:
                    raise ValueError("META serial mismatch")
                if session.property('ro.build.fingerprint') != BASEOS_FINGERPRINT:
                    raise ValueError("META firmware mismatch")
            result = {'state': 'meta_verified', 'serial': serial_number, 'port': ap[0].device,
                      'usb_location': ap[0].location, 'fingerprint': BASEOS_FINGERPRINT}
            report(result)
            reboot.wait(timeout=10)
            if reboot.returncode:
                raise RuntimeError('ADB reboot command failed despite a META response')
            return result
        if reboot.poll() not in (None, 0):
            raise RuntimeError('ADB reboot command failed')
        time.sleep(0.02)
    if reboot.poll() is None:
        reboot.kill()
        reboot.wait(timeout=5)
    raise TimeoutError("selected phone did not expose a verified META endpoint within timeout")
