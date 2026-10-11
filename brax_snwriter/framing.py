"""AP META framing derived from BSP PortHandle and UsbRxWatcher.

This module performs no I/O. Host requests escape 0x55; device replies escape
0xa5. Both escape 0x5a. Modem frames and software-flow-control are separate
protocols and are intentionally not silently treated as AP frames.
"""
from dataclasses import dataclass
from functools import reduce
from operator import xor
import struct

MAX_RAW = 65536
MAX_PEER = 61440


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    local: bytes
    peer: bytes = b""


def _escape(data: bytes, special: int) -> bytes:
    out = bytearray()
    for value in data:
        out.extend(b"\x5a\x01" if value == special else
                   b"\x5a\x5a" if value == 0x5a else bytes((value,)))
    return bytes(out)


def _unescape(data: bytes, special: int) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(data):
        value = data[pos]
        pos += 1
        if value == 0x5a:
            if pos == len(data):
                raise ProtocolError("truncated escape")
            following = data[pos]
            pos += 1
            if following not in (0x01, 0x5a):
                raise ProtocolError("unknown escape")
            value = special if following == 1 else 0x5a
        out.append(value)
    return bytes(out)


def encode(frame: Frame, *, direction: str) -> bytes:
    """Encode one complete AP frame. direction is 'request' or 'response'."""
    if direction not in ("request", "response"):
        raise ValueError("direction must be request or response")
    if not frame.local or len(frame.local) > 65535:
        raise ProtocolError("local data must contain 1..65535 bytes")
    if len(frame.peer) > MAX_PEER:
        raise ProtocolError("peer data exceeds BSP limit")
    peer = b"\x00" * 8 + frame.peer if frame.peer else b""
    body = struct.pack("<HH", len(frame.local), len(peer)) + frame.local + peer
    kind = 0x66 if direction == "request" else 0x60
    if len(body) + 5 > MAX_RAW or len(body) + 1 > 65535:
        raise ProtocolError("frame exceeds length field or BSP buffer")
    raw = b"\x55" + struct.pack(">H", len(body) + 1) + bytes((kind,)) + body
    raw += bytes((reduce(xor, raw, 0),))
    special = 0x55 if direction == "request" else 0xa5
    encoded = b"\x55" + _escape(raw[1:], special)
    # The reference uses a fixed 64 KiB output buffer, including escapes.
    if len(encoded) > MAX_RAW:
        raise ProtocolError("escaped frame exceeds BSP buffer")
    return encoded


def decode(data: bytes, *, direction: str) -> Frame:
    """Decode exactly one complete frame, rejecting corruption and trailing data."""
    if direction not in ("request", "response"):
        raise ValueError("direction must be request or response")
    if not data.startswith(b"\x55") or len(data) > MAX_RAW:
        raise ProtocolError("invalid frame boundary or length")
    raw = b"\x55" + _unescape(data[1:], 0x55 if direction == "request" else 0xa5)
    if len(raw) < 10:
        raise ProtocolError("truncated frame")
    if struct.unpack_from(">H", raw, 1)[0] + 4 != len(raw):
        raise ProtocolError("outer length mismatch")
    if reduce(xor, raw, 0):
        raise ProtocolError("checksum mismatch")
    if raw[3] != (0x66 if direction == "request" else 0x60):
        raise ProtocolError("unexpected direction or non-AP frame")
    local_len, peer_len = struct.unpack_from("<HH", raw, 4)
    if not local_len or local_len + peer_len + 9 != len(raw):
        raise ProtocolError("inner length mismatch")
    if peer_len and not 8 < peer_len <= MAX_PEER + 8:
        raise ProtocolError("invalid peer buffer length")
    local = raw[8:8 + local_len]
    peer = raw[8 + local_len + 8:-1] if peer_len else b""
    return Frame(local, peer)
