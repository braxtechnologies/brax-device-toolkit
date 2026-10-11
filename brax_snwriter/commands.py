"""Source-derived AP structures. No transport or device-writing side effects."""
import struct
from .framing import Frame, ProtocolError


def _u16(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("expected unsigned 16-bit integer")
    return value


def alive(token: int) -> Frame:
    return Frame(struct.pack("<HHI", _u16(token), 8, 0))


def version(token: int) -> Frame:
    return Frame(struct.pack("<HH", _u16(token), 51))


def build_property(token: int, name: str) -> Frame:
    value = name.encode("ascii")
    if not value or len(value) > 63 or b"\x00" in value:
        raise ValueError("property must be 1..63 ASCII bytes without NUL")
    return Frame(struct.pack("<HH64s", _u16(token), 137, value))


def match_response(frame: Frame, *, token: int, command: int) -> bytes:
    if len(frame.local) < 4:
        raise ProtocolError("missing response header")
    actual_token, actual_command = struct.unpack_from("<HH", frame.local)
    if (actual_token, actual_command) != (_u16(token), _u16(command)):
        raise ProtocolError("response token or command mismatch")
    return frame.local[4:]


def read_property_response(frame: Frame, token: int) -> str:
    body = match_response(frame, token=token, command=138)
    if len(body) != 132:
        raise ProtocolError("unexpected build-property response size")
    value, status = struct.unpack("<128si", body)
    if status != 0:
        raise ProtocolError(f"target reported build-property failure ({status})")
    return value.split(b"\x00", 1)[0].decode("utf-8", errors="strict")
