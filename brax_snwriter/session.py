"""Transactions on an already active, authorized AP META serial endpoint.

This does not bypass authentication or enter META boot mode. Only the explicit
port passed by the caller is opened. No vendor DLL is used.
"""
import struct
import time
from .framing import Frame, ProtocolError, encode, decode, MAX_RAW
from .commands import build_property, read_property_response, match_response


class IndeterminateWrite(RuntimeError):
    """A request may have reached the device. Reconcile; never blindly retry."""


class MetaSession:
    def __init__(self, stream, timeout=10.0):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.stream = stream
        self.timeout = timeout
        self.token = 0
        self.failed = False

    def next_token(self):
        self.token = (self.token % 65535) + 1
        return self.token

    def _receive(self):
        deadline = time.monotonic() + self.timeout
        wire = bytearray()
        raw = bytearray()
        escaped = False
        total = None
        while time.monotonic() < deadline:
            byte = self.stream.read(1)
            if not byte:
                continue
            if len(byte) != 1:
                raise ProtocolError("transport violated read size")
            b = byte[0]
            wire.append(b)
            if len(wire) > MAX_RAW:
                raise ProtocolError("oversized response")
            if not raw:
                if b != 0x55:
                    raise ProtocolError("non-META data on selected endpoint")
                raw.append(b)
                continue
            if escaped:
                if b not in (1, 0x5a):
                    raise ProtocolError("invalid response escape")
                b = 0xa5 if b == 1 else 0x5a
                escaped = False
            elif b == 0x5a:
                escaped = True
                continue
            raw.append(b)
            if len(raw) == 3:
                total = struct.unpack_from(">H", raw, 1)[0] + 4
                if not 10 <= total <= MAX_RAW:
                    raise ProtocolError("invalid response length")
            if total is not None and len(raw) == total:
                return decode(bytes(wire), direction="response")
        raise TimeoutError("META response timed out")

    def exchange(self, frame, expected_command, *, mutating=False):
        if self.failed:
            raise RuntimeError("session invalid after failed exchange; reconnect and reconcile")
        packet = encode(frame, direction="request")
        token = struct.unpack_from("<H", frame.local)[0]
        try:
            if self.stream.write(packet) != len(packet):
                raise OSError("partial META write")
            response = self._receive()
            match_response(response, token=token, command=expected_command)
            return response
        except (OSError, ValueError) as exc:
            self.failed = True
            if mutating:
                raise IndeterminateWrite("write outcome unknown; backup retained, no retry performed") from exc
            raise

    def property(self, name):
        token = self.next_token()
        return read_property_response(self.exchange(build_property(token, name), 138), token)

    @staticmethod
    def _nvram_status(response, token, command, lid, record):
        body = match_response(response, token=token, command=command)
        if len(body) != 6:
            raise ProtocolError("unexpected NVRAM response size")
        actual_lid, actual_record, operation_ok, module_status = struct.unpack("<HHBB", body)
        if (actual_lid, actual_record) != (lid, record):
            raise ProtocolError("NVRAM record mismatch")
        if operation_ok != 1 or module_status != 0:
            raise ProtocolError("device reported NVRAM failure")

    def read_record(self, lid, record=1):
        token = self.next_token()
        frame = Frame(struct.pack("<HHHH", token, 22, lid, record))
        response = self.exchange(frame, 23)
        self._nvram_status(response, token, 23, lid, record)
        if not response.peer:
            raise ProtocolError("empty NVRAM record")
        return response.peer

    def write_record(self, lid, record, data):
        token = self.next_token()
        frame = Frame(struct.pack("<HHHH", token, 24, lid, record), data)
        response = self.exchange(frame, 25, mutating=True)
        try:
            self._nvram_status(response, token, 25, lid, record)
        except ProtocolError as exc:
            self.failed = True
            raise IndeterminateWrite("NVRAM write reported failure; reconcile saved backup") from exc


def open_serial(port, *, baudrate=115200):
    """pySerial uses OS serial APIs; install pyserial==3.5 separately."""
    import serial
    return serial.Serial(port=port, baudrate=baudrate, timeout=0.1,
                         write_timeout=5, xonxoff=False, rtscts=False, dsrdtr=False)
