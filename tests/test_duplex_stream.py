import queue
import threading
import unittest

from brax_snwriter.duplex_stream import DuplexStream
from brax_snwriter.da_protocol import Agent
from tests.test_native_flash import frame


class BackpressureLink:
    write_timeout = 1

    def __init__(self):
        self.incoming = queue.Queue()
        self.drained = threading.Event()
        self.calls = 0

    def read(self, count):
        try:
            byte = self.incoming.get(timeout=.01)
        except queue.Empty:
            return b''
        if self.incoming.empty():
            self.drained.set()
        return byte

    def write(self, data):
        self.calls += 1
        if self.calls == 1:
            self.drained.clear()
            for byte in frame(b'\x01\0\0\0alive',2):
                self.incoming.put(bytes([byte]))
            if not self.drained.wait(1):
                raise TimeoutError('write blocked until incoming USB data is drained')
        if self.calls == 2:
            for byte in frame('OK'):
                self.incoming.put(bytes([byte]))
        return len(data)


class DuplexTests(unittest.TestCase):
    def test_receive_continues_while_write_is_blocked(self):
        link = BackpressureLink()
        logs = []
        with DuplexStream(link) as stream:
            agent = Agent(stream,logs.append,timeout=2)
            agent.ack(0)
            agent.expect_ok()
        self.assertEqual(logs,['alive'])
        self.assertEqual(link.calls,2)  # Header and payload; no resend.
        self.assertFalse(stream.thread.is_alive())

    def test_worker_failure_is_reported(self):
        class Broken:
            def read(self,count):
                raise OSError('disconnected')
        with self.assertRaises(OSError):
            with DuplexStream(Broken()) as stream:
                stream.thread.join(1)
                stream.read(1)
        self.assertFalse(stream.thread.is_alive())
