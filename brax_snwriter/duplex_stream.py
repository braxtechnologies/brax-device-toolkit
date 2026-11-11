"""Drain USB input while the agent is applying backpressure to a host write."""
import queue
import threading


class DuplexStream:
    def __init__(self, stream):
        self.stream = stream
        self.chunks = queue.Queue(maxsize=64)
        self.pending = bytearray()
        self.stopping = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._read, name='da-usb-reader', daemon=True)

    @property
    def write_timeout(self):
        return self.stream.write_timeout

    @write_timeout.setter
    def write_timeout(self, value):
        self.stream.write_timeout = value

    def _read(self):
        try:
            while not self.stopping.is_set():
                size = max(1, min(getattr(self.stream, 'in_waiting', 0), 65536))
                data = self.stream.read(size)
                if data:
                    # At most 4 MiB can accumulate. Never silently drop bytes.
                    self.chunks.put(data, timeout=1)
        except Exception as exc:
            if not self.stopping.is_set():
                self.error = exc

    def read(self, count):
        if not self.pending:
            try:
                self.pending.extend(self.chunks.get(timeout=.05))
            except queue.Empty:
                if self.error:
                    raise OSError('USB receive worker failed') from self.error
                return b''
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data

    def write(self, data):
        if self.error:
            raise OSError('USB receive worker failed') from self.error
        return self.stream.write(data)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stopping.set()
        cancel = getattr(self.stream, 'cancel_read', None)
        if cancel:
            cancel()
        self.thread.join(2)
        if self.thread.is_alive():
            raise OSError('USB receive worker did not stop; reconnect before reuse')
        if exc[0] is None and (self.error or self.pending or not self.chunks.empty()):
            raise OSError('Unexpected pending USB input at transaction completion') from self.error
