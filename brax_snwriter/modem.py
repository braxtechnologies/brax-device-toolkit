"""MT6835 P18 modem META over DHL, implemented without vendor libraries.

The AP codec is different. Tunnel headers/checksums are escaped, but their
payload is raw. A modem local parameter has an additional four-byte KAL header.
"""
import functools
import secrets
import struct
import time
from .framing import Frame, ProtocolError
from .session import MetaSession, IndeterminateWrite
from .serial_record import BASEOS_FINGERPRINT, validate_serial

BASEBAND = 'MOLY.NR17.R1.MP3.RC.MP.V19.10.P18'
IMEI_LID = 61200
IMEI_RECORD_SIZE = 10
MAGIC = bytes.fromhex('acdbcfe5')
MAX_FRAME = 65535


def _xor(data):
    return functools.reduce(int.__xor__, data, 0)


def _escape(data, *, response=False):
    escaped = 0xa5 if response else 0x55
    return b''.join(b'\x5a\x01' if v == escaped else b'\x5a\x5a' if v == 0x5a else bytes([v]) for v in data)


def tunnel_packet(payload, *, response=False):
    if not 0 < len(payload) < MAX_FRAME - 1:
        raise ValueError('Invalid tunnel payload size')
    header = b'\x55' + struct.pack('>H', len(payload) + int(response)) + bytes([0xd8 if response else 0xd0])
    return header[:1] + _escape(header[1:], response=response) + payload + _escape(
        bytes([_xor(header + payload)]), response=response)


def ft_local(token, command, body=b''):
    if not 0 < token <= 65535 or not 0 <= command <= 65535 or len(body) > 32768:
        raise ValueError('Invalid FT request')
    return struct.pack('<BBHHH', 1, 0, len(body) + 8, token, command) + body


def dhl_request(local, peer=b''):
    peer_wire = struct.pack('<HBBHH', len(peer), 1, 0, 0, 0) + peer if peer else b''
    # MT6835 META request ILM: source 3, destination 0, message 200, SAP 1.
    body = struct.pack('<IIIIHH', 3, 0, 200, 1, len(local), len(peer_wire)) + local + peer_wire
    header = struct.pack('<HH', 0x0140, len(body))
    checksum = b'\x00\xff\x04\x00\x00\x00' + bytes([sum(body) & 255, sum(header) & 255])
    return tunnel_packet(MAGIC + header + body + checksum)


def parse_dhl_confirm(payload):
    if len(payload) < 18 or payload[:4] != b'\xac\xca\x00\xff':
        raise ProtocolError('Unsupported modem frame wrapper')
    if struct.unpack_from('<H', payload, 4)[0] != len(payload) - 6:
        raise ProtocolError('Modem wrapper length mismatch')
    data = payload[6:]
    if data[:4] != MAGIC:
        raise ProtocolError('Missing DHL signature')
    kind, body_length = struct.unpack_from('<HH', data, 4)
    if kind != 0x0920:
        raise ProtocolError(f'Unsupported DHL confirm type {kind:#x}')
    if data[11] != sum(data[4:8]) & 255:
        raise ProtocolError('DHL header checksum mismatch')
    body = data[12:]
    if len(body) != body_length or len(body) < 20:
        raise ProtocolError('DHL body length mismatch')
    source, destination, message, sap, local_len, peer_len = struct.unpack_from('<IIIIHH', body)
    if (source, destination, message, sap) != (161, 0, 163, 2):
        raise ProtocolError('Unexpected modem response route')
    if local_len < 8 or len(body) != 20 + local_len + peer_len:
        raise ProtocolError('Invalid modem local/peer sizes')
    local = body[20:20 + local_len]
    # The target does not initialize the reserved KAL byte; do not interpret it.
    if local[0] != 1 or struct.unpack_from('<H', local, 2)[0] != local_len:
        raise ProtocolError('Invalid modem local parameter header')
    peer = body[20 + local_len:]
    if peer:
        if len(peer) < 8:
            raise ProtocolError('Truncated modem peer buffer')
        length, references, reserved, headroom, tailroom = struct.unpack_from('<HBBHH', peer)
        if references != 1 or headroom or tailroom or len(peer) != length + 8:
            raise ProtocolError('Unsupported modem peer buffer layout')
        peer = peer[8:]
    return Frame(local, peer)


class ModemSession:
    def __init__(self, stream, *, timeout=5, token=None):
        if timeout <= 0:
            raise ValueError('Invalid timeout')
        self.stream, self.timeout = stream, timeout
        self.token = token if token is not None else secrets.randbelow(60000) + 1
        self.failed = False
        self.response_started = False
        self.alive_token = None

    def _read(self, count, deadline):
        data = bytearray()
        while len(data) < count and time.monotonic() < deadline:
            chunk = self.stream.read(count - len(data))
            if len(chunk) > count - len(data):
                raise ProtocolError('Transport read exceeded requested size')
            data.extend(chunk)
        if len(data) != count:
            raise TimeoutError('Modem response timed out')
        return bytes(data)

    def _escaped_byte(self, deadline):
        b = self._read(1, deadline)[0]
        if b == 0x5a:
            b = self._read(1, deadline)[0]
            if b not in (1, 0x5a):
                raise ProtocolError('Invalid tunnel escape')
            return 0xa5 if b == 1 else b
        return b

    def receive(self):
        deadline = time.monotonic() + self.timeout
        self.response_started = False
        if self._read(1, deadline) != b'\x55':
            raise ProtocolError('Unexpected data on modem endpoint')
        self.response_started = True
        header = b'\x55' + bytes(self._escaped_byte(deadline) for _ in range(3))
        length = struct.unpack_from('>H', header, 1)[0]
        if header[3] != 0xd8 or not 1 < length <= MAX_FRAME:
            raise ProtocolError('Unexpected tunnel channel or length')
        payload = self._read(length - 1, deadline)
        checksum = self._escaped_byte(deadline)
        if _xor(header + payload) != checksum:
            raise ProtocolError('Modem tunnel checksum mismatch')
        return parse_dhl_confirm(payload)

    def exchange(self, command, body=b'', peer=b'', *, mutating=False):
        if self.failed:
            raise RuntimeError('Modem session invalid; reconcile before reconnecting')
        self.token = self.token % 65535 + 1
        local = ft_local(self.token, command, body)
        packet = dhl_request(local, peer)
        try:
            if self.stream.write(packet) != len(packet):
                raise OSError('Partial modem request write')
            response = self.receive()
            # An alive retry can leave one delayed identical handshake confirm.
            # Discard only that exact non-mutating token/command, never an NVRAM confirm.
            for _ in range(2):
                if command == 8 or struct.unpack_from('<HH', response.local, 4) != (self.alive_token, 9):
                    break
                response = self.receive()
            if struct.unpack_from('<HH', response.local, 4) != (self.token, command + 1):
                raise ProtocolError('Modem response token/command mismatch')
            return response
        except (OSError, ValueError, RuntimeError) as exc:
            self.failed = True
            if mutating:
                raise IndeterminateWrite('Modem write outcome unknown. No automatic retry; preserve backup.') from exc
            raise

    def connect(self, expected_serial):
        validate_serial(expected_serial)
        ap = MetaSession(self.stream, timeout=self.timeout)
        if ap.property('ro.vendor.serialno') != expected_serial or ap.property('ro.build.fingerprint') != BASEOS_FINGERPRINT:
            raise ValueError('Selected modem endpoint belongs to a different phone/firmware')
        token = ap.next_token()
        response = ap.exchange(Frame(struct.pack('<HHIII', token, 147, 1, 0, 0)), 148)
        if len(response.local) != 1036 or response.local[8] != 0 or struct.unpack_from('<II', response.local, 12) != (2, 2):
            raise ValueError('Phone does not support the validated DHL/tunnel channel')
        # META's AP endpoint appears before the modem is ready on a fresh boot.
        deadline = time.monotonic() + 60
        while True:
            token = ap.next_token()
            state = ap.exchange(Frame(struct.pack('<HHIII', token, 147, 11, 0, 0)), 148)
            if len(state.local) == 1036 and state.local[8] == 0 and struct.unpack_from('<I', state.local, 12)[0] == 2:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Modem did not reach ready state')
            time.sleep(0.25)
        self._alive()
        version = self.exchange(51)
        strings = version.local[8:].split(b'\0')
        if BASEBAND.encode() not in strings or b'MT6835' not in strings:
            raise ValueError('Modem version does not match the P18 record profile')
        return {'serial': expected_serial, 'baseband': BASEBAND, 'transport': 'DHL over AP META tunnel'}

    def _alive(self):
        original_timeout = self.timeout
        self.timeout = min(3, self.timeout)
        try:
            for attempt in range(3):
                previous_token = self.token
                try:
                    self.exchange(8, bytes(4))
                    self.alive_token = self.token
                    return
                except TimeoutError:
                    if self.response_started or attempt == 2:
                        raise
                    # Only a completely silent, read-only alive request is retried.
                    self.failed = False
                    self.token = previous_token
        finally:
            self.timeout = original_timeout

    @staticmethod
    def _check_record_response(response, record):
        if len(response.local) != 16:
            raise ProtocolError('Invalid NVRAM confirm size')
        lid, rid, status = struct.unpack_from('<IHB', response.local, 8)
        if lid != IMEI_LID or rid != record or status != 0:
            raise ProtocolError(f'Modem NVRAM operation rejected or mismatched (LID={lid}, record={rid}, status={status})')

    def read_imei_record(self, record):
        if record not in range(1, 11):
            raise ValueError('IMEI record must be 1..10 for the P18 profile')
        response = self.exchange(117, struct.pack('<IH2x', IMEI_LID, record))
        self._check_record_response(response, record)
        if len(response.peer) != IMEI_RECORD_SIZE:
            raise ProtocolError('Wrong modem IMEI record length')
        return response.peer

    def write_imei_record(self, record, data):
        if record not in range(1, 11) or len(data) != IMEI_RECORD_SIZE:
            raise ValueError('Invalid IMEI record')
        response = self.exchange(119, struct.pack('<IH2x', IMEI_LID, record), data, mutating=True)
        try:
            self._check_record_response(response, record)
        except ProtocolError as exc:
            self.failed = True
            raise IndeterminateWrite('Modem write reported rejection; reconcile saved record before retry.') from exc

    def query_imei(self, operation, argument=0):
        if operation not in (0, 1, 8) or not 0 <= argument <= 10:
            raise ValueError('Only documented read-only IMEI queries are supported')
        response = self.exchange(73, struct.pack('<BBH10x', operation, 0, argument))
        if len(response.local) != 36 or response.local[8] != operation:
            raise ProtocolError('Unexpected IMEI query response')
        status = struct.unpack_from('<I', response.local, 32)[0]
        if status:
            raise ProtocolError(f'Modem IMEI query rejected (status={status})')
        return response.local[10:32]
