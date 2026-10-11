"""Bounded, read-only MediaTek boot-port probe; never downloads or starts a DA.

USB IDs: SP Flash Tool V6.2404 usb_settings.xml. Handshake and information
command framing cross-checked with mtkclient Library/Port.py and mtk_preloader.py.
This is independent diagnostic code, not a flashing implementation.
"""
import threading
import time
import struct

import serial
from serial.tools.list_ports import comports

BOOT_IDS = {0x0003: 'BROM', 0x2000: 'Preloader'}


class ProbeCancelled(Exception):
    pass


def candidates(ports):
    return [p for p in ports if p.vid == 0x0e8d and p.pid in BOOT_IDS]


def inspect_port(port, cancel, serial_factory=serial.Serial):
    """Handshake then issue only FD/FC/D8 information commands.

    Holding a boot connection can delay normal boot. No watchdog disable,
    memory write, flash write, authentication submission or DA upload is sent.
    """
    # Windows can enumerate the COM name before CreateFile can open it.
    # Only retry opening: never restart a partly completed command sequence.
    for attempt in range(5):
        if cancel.is_set():
            raise ProbeCancelled('Boot connection check cancelled.')
        try:
            link = serial_factory(port.device, baudrate=115200, timeout=.05,
                                  write_timeout=.5)
            break
        except serial.SerialException:
            if attempt == 4:
                raise
            cancel.wait(.05)
    with link:
        end = time.monotonic() + 2
        step = 0
        sequence = b'\xa0\x0a\x50\x05'
        while step < len(sequence):
            if cancel.is_set():
                raise ProbeCancelled('Boot connection check cancelled.')
            if time.monotonic() >= end:
                raise TimeoutError('Boot port appeared, but the handshake timed out. Reconnect the powered-off phone.')
            byte = sequence[step]
            if link.write(bytes([byte])) != 1:
                raise IOError('Incomplete boot handshake write')
            reply = link.read(1)
            step = step + 1 if reply == bytes([byte ^ 0xff]) else 0
        link.timeout = .5

        def read_exact(count):
            data = bytearray()
            deadline = time.monotonic() + 2
            while len(data) < count:
                if cancel.is_set():
                    raise ProbeCancelled('Boot connection check cancelled.')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Incomplete boot information response')
                chunk = link.read(count - len(data))
                if not chunk:
                    raise TimeoutError('Boot information response timed out')
                data.extend(chunk)
            return bytes(data)

        def command(value, size):
            packet = bytes([value])
            if link.write(packet) != 1 or read_exact(1) != packet:
                raise ValueError(f'Boot command 0x{value:02x} was not acknowledged')
            return read_exact(size)

        code, initial_version = struct.unpack('>HH', command(0xfd, 4))
        subcode, hw_version, sw_version, status = struct.unpack('>HHHH', command(0xfc, 8))
        if status != 0:
            raise ValueError(f'Hardware version command failed: 0x{status:04x}')
        config, status = struct.unpack('>IH', command(0xd8, 6))
        if status != 0:
            raise ValueError(f'Security configuration command failed: 0x{status:04x}')
        return {'port': port.device, 'usb_location': port.location,
                'mode': BOOT_IDS[port.pid], 'handshake_verified': True,
                'hw_code': f'0x{code:04x}', 'initial_hw_version': initial_version,
                'hw_subcode': subcode, 'hw_version': hw_version, 'sw_version': sw_version,
                'target_config': f'0x{config:08x}',
                'secure_boot': bool(config & 1), 'serial_link_auth': bool(config & 2),
                'download_agent_auth': bool(config & 4), 'root_certificate_required': bool(config & 16),
                'download_agent_tested': False, 'flash_ready': False}


def wait_and_probe(timeout=60, cancel=None, enumerate_ports=comports,
                   probe=inspect_port, event=lambda row: None):
    cancel = cancel or threading.Event()
    deadline = time.monotonic() + timeout
    attempted = set()
    last_error = None
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise ProbeCancelled('Boot connection check cancelled.')
        ports = candidates(enumerate_ports())
        if len(ports) > 1:
            raise ValueError('Multiple MediaTek boot ports found. Connect only the phone to test.')
        present = {(p.device, p.location) for p in ports}
        attempted.intersection_update(present)
        if ports:
            port = ports[0]
            identity = (port.device, port.location)
            if identity not in attempted:
                attempted.add(identity)
                event({'state': 'boot_port_detected', 'port': port.device, 'mode': BOOT_IDS[port.pid]})
                try:
                    return probe(port, cancel)
                except (serial.SerialException, OSError, ValueError) as exc:
                    last_error = str(exc)
                    event({'state': 'probe_failed', 'port': port.device, 'error': last_error})
        cancel.wait(.05)
    if last_error:
        raise TimeoutError('Boot port detected, but the check failed: ' + last_error)
    raise TimeoutError('No preloader/BROM port appeared during the listening window. Unplug the phone, power it fully off, start the check and reconnect USB. USB debugging is not required.')
