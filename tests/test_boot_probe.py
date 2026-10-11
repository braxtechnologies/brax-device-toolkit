import struct
import threading
import unittest
from types import SimpleNamespace
from brax_snwriter.boot_probe import candidates, inspect_port, wait_and_probe, ProbeCancelled


class Link:
    def __init__(self, bad_status=False):
        self.writes = []
        self.responses = {
            0xa0: b'\x5f', 0x0a: b'\xf5', 0x50: b'\xaf', 5: b'\xfa',
            0xfd: b'\xfd'+struct.pack('>HH', 0x1234, 0),
            0xfc: b'\xfc'+struct.pack('>HHHH', 1, 2, 3, int(bad_status)),
            0xd8: b'\xd8'+struct.pack('>IH', 7, 0),
        }
        self.buffer = b''
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def write(self, data):
        self.writes.append(data)
        self.buffer += self.responses[data[0]]
        return len(data)
    def read(self, count):
        # Deliberately fragment responses to exercise stream reads.
        chunk, self.buffer = self.buffer[:1], self.buffer[1:]
        return chunk


class BootProbeTests(unittest.TestCase):
    def port(self, pid=0x2000):
        return SimpleNamespace(device='COM5', vid=0x0e8d, pid=pid, location='1-1')

    def test_excludes_bluetooth_meta_and_da(self):
        self.assertEqual(len(candidates([self.port(p) for p in [3,0x2000,0x2001,0x200e,0x7925]])),2)

    def test_read_only_handshake_and_fragmented_responses(self):
        link=Link()
        result=inspect_port(self.port(),threading.Event(),lambda *a,**k:link)
        self.assertEqual(result['hw_code'],'0x1234')
        self.assertTrue(result['download_agent_auth'])
        self.assertFalse(result['flash_ready'])
        self.assertEqual(b''.join(link.writes),bytes([0xa0,10,0x50,5,0xfd,0xfc,0xd8]))

    def test_bad_status_stops_before_next_command(self):
        link=Link(True)
        with self.assertRaises(ValueError):inspect_port(self.port(),threading.Event(),lambda *a,**k:link)
        self.assertNotIn(b'\xd8',link.writes)

    def test_windows_enumeration_before_port_ready_is_retried(self):
        from unittest.mock import Mock
        import serial
        link=Link()
        factory=Mock(side_effect=[serial.SerialException('port not ready'),link])
        self.assertTrue(inspect_port(self.port(),threading.Event(),factory)['handshake_verified'])
        self.assertEqual(factory.call_count,2)

    def test_two_phones_not_probed(self):
        with self.assertRaises(ValueError):wait_and_probe(enumerate_ports=lambda:[self.port(),self.port()])

    def test_cancel_does_not_open_port(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(ProbeCancelled):wait_and_probe(cancel=cancel)

    def test_missing_phone_is_a_listening_timeout(self):
        with self.assertRaisesRegex(TimeoutError,'USB debugging is not required'):
            wait_and_probe(timeout=.001,enumerate_ports=lambda:[])
