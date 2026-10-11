import random
import struct
import unittest
import json
from pathlib import Path
from brax_snwriter.framing import Frame, ProtocolError, encode, decode
from brax_snwriter.commands import (alive, build_property,
                                   read_property_response)


class ProtocolTests(unittest.TestCase):
    def test_captured_bsp_cpp_vectors(self):
        vectors = json.loads((Path(__file__).resolve().parent / "fixtures" / "ap-frame-vectors.json").read_text())
        for vector in vectors:
            frame = Frame(bytes.fromhex(vector["local_hex"]), bytes.fromhex(vector["peer_hex"]))
            expected = bytes.fromhex(vector["expected_response_hex"])
            self.assertEqual(encode(frame, direction="response"), expected)
            self.assertEqual(decode(expected, direction="response"), frame)

    def test_reference_alive_vector(self):
        # Manually calculated from FT_H, dummy, mixed-endian lengths and XOR.
        request = bytes.fromhex("55000d660800000001000800000000003f")
        self.assertEqual(encode(alive(1), direction="request"), request)
        self.assertEqual(decode(request, direction="request"), alive(1))

    def test_direction_specific_escape_vectors(self):
        request = bytes.fromhex("55000866030000005a015a5aa592")
        response = bytes.fromhex("5500086003000000555a5a5a0194")
        frame = Frame(bytes.fromhex("555aa5"))
        self.assertEqual(encode(frame, direction="request"), request)
        self.assertEqual(encode(frame, direction="response"), response)
        self.assertEqual(decode(request, direction="request"), frame)
        self.assertEqual(decode(response, direction="response"), frame)

    def test_all_byte_values_in_peer(self):
        frame = Frame(b"\x01\x00\xaf\x00", bytes(range(256)))
        for direction in ("request", "response"):
            self.assertEqual(decode(encode(frame, direction=direction), direction=direction), frame)

    def test_random_payloads(self):
        rng = random.Random(731)
        for _ in range(100):
            frame = Frame(rng.randbytes(rng.randrange(1, 100)), rng.randbytes(rng.randrange(1000)))
            for direction in ("request", "response"):
                self.assertEqual(decode(encode(frame, direction=direction), direction=direction), frame)

    def test_corruption_rejected(self):
        good = encode(alive(1), direction="request")
        for i in range(len(good)):
            corrupt = bytearray(good)
            corrupt[i] ^= 1
            with self.assertRaises(ProtocolError):
                decode(bytes(corrupt), direction="request")

    def test_truncation_and_trailing_data(self):
        good = encode(alive(1), direction="request")
        for i in range(len(good)):
            with self.assertRaises(ProtocolError):
                decode(good[:i], direction="request")
        with self.assertRaises(ProtocolError):
            decode(good + b"\x00", direction="request")

    def test_bad_escapes(self):
        for suffix in (b"\x5a", b"\x5a\x00", b"\x5a\x02"):
            with self.assertRaises(ProtocolError):
                decode(b"\x55" + suffix, direction="request")

    def test_limits(self):
        for frame in (Frame(b""), Frame(b"x" * 65536), Frame(b"x", b"y" * 61441),
                      Frame(b"\x55" * 40000)):
            with self.assertRaises(ProtocolError):
                encode(frame, direction="request")

    def test_direction_required(self):
        with self.assertRaises(ValueError):
            encode(alive(1), direction="modem")
        with self.assertRaises(ProtocolError):
            decode(encode(alive(1), direction="request"), direction="response")

    def test_property_structure(self):
        self.assertEqual(len(build_property(7, "ro.product.model").local), 68)
        for name in ("", "x" * 64, "a\x00b", "é"):
            with self.assertRaises(ValueError):
                build_property(7, name)

    def test_property_response(self):
        response = Frame(struct.pack("<HH128si", 7, 138, b"BraX3", 0))
        self.assertEqual(read_property_response(response, 7), "BraX3")
        with self.assertRaises(ProtocolError):
            read_property_response(response, 8)
        with self.assertRaises(ProtocolError):
            read_property_response(Frame(struct.pack("<HH128si", 7, 138, b"", 1)), 7)


    def test_token_validation(self):
        for token in (-1, 65536, True, 1.0):
            with self.assertRaises(ValueError):
                alive(token)


if __name__ == "__main__":
    unittest.main()
