"""MT6835 preloader META negotiation from the local BSP meta.h/main.c.

Only for the selected preloader endpoint. No download agent, flash writes,
authentication bypass or persistent boot-flag modification is performed.
"""
import struct
import time


class MetaBootError(RuntimeError):
    pass


def enter_meta(stream, timeout=20):
    deadline = time.monotonic() + timeout
    v1 = struct.pack("<IIBBBB", 12, 1, 0, 0, 0, 0)
    v2 = struct.pack("<IIBBBBB3x", 16, 2, 0, 0, 0, 0, 0)
    sent = False
    pending = bytearray()
    transcript = []
    tokens = (b"READY", b"ATEM0001", b"ATEM0002", b"ATEMATEX", b"ATEMATEM",
              b"METAFORB", b"METASLA", b"LOCK")

    def send(data):
        if stream.write(data) != len(data):
            raise MetaBootError("partial preloader handshake write")

    while time.monotonic() < deadline:
        byte = stream.read(1)
        if not byte:
            continue
        pending.extend(byte)
        if not any(t.startswith(pending) for t in tokens):
            raise MetaBootError("unexpected preloader response; no fallback attempted")
        if bytes(pending) not in tokens:
            continue
        token = bytes(pending)
        pending.clear()
        transcript.append(token.decode())
        if token in (b"METAFORB", b"METASLA", b"LOCK"):
            raise MetaBootError("device requires authorized factory authentication or disallows META")
        if token == b"READY":
            if not sent:
                send(b"METAMETA")
                send(v1)
                sent = True
        elif not sent:
            raise MetaBootError("unexpected acknowledgement before request")
        elif token == b"ATEM0001":
            send(v1)
        elif token == b"ATEM0002":
            send(v2)
        else:
            if token == b"ATEMATEX":
                send(b"DISCONNECT")
            return transcript
    raise MetaBootError("preloader META negotiation timed out")
