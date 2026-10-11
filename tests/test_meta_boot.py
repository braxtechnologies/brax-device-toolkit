import unittest
from brax_snwriter.meta_boot import enter_meta, MetaBootError


class Port:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = []
    def read(self, n):
        b = bytes(self.response[:n]); del self.response[:n]; return b
    def write(self, data):
        self.sent.append(data); return len(data)


class BootTests(unittest.TestCase):
    def test_version_negotiation(self):
        p = Port(b"READYREADYATEM0001ATEM0002ATEMATEX")
        self.assertEqual(enter_meta(p)[-1], "ATEMATEX")
        self.assertEqual(p.sent[0], b"METAMETA")
        self.assertEqual(p.sent[1].hex(), "0c0000000100000000000000")
        self.assertEqual(p.sent[3].hex(), "10000000020000000000000000000000")
        self.assertEqual(p.sent[-1], b"DISCONNECT")

    def test_authentication_is_not_bypassed(self):
        for response in (b"METAFORB", b"METASLA", b"LOCK"):
            p = Port(b"READY" + response)
            with self.assertRaises(MetaBootError): enter_meta(p)
            self.assertEqual(len(p.sent), 2)

    def test_unknown_and_unsolicited_rejected(self):
        for response in (b"WHAT", b"ATEMATEM"):
            p = Port(response)
            with self.assertRaises(MetaBootError): enter_meta(p)
            self.assertFalse(p.sent)
