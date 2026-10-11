import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from brax_snwriter.modem import ModemSession, dhl_request, ft_local, tunnel_packet, MAGIC, IMEI_LID
from brax_snwriter.imei import decode_record, encode_record, rewrite_existing, write_assigned, restore_imeis
from brax_snwriter.session import IndeterminateWrite


def confirm(token, command, peer=b'', status=0):
    local = ft_local(token, command, struct.pack('<IHBB', IMEI_LID, 1, status, 0))
    peer_wire = struct.pack('<HBBHH', len(peer), 1, 0, 0, 0) + peer if peer else b''
    body = struct.pack('<IIIIHH', 161, 0, 163, 2, len(local), len(peer_wire)) + local + peer_wire
    header = struct.pack('<HH', 0x0920, len(body))
    dhl = MAGIC + header + b'\0\0\0' + bytes([sum(header) & 255]) + body
    return tunnel_packet(b'\xac\xca\0\xff' + struct.pack('<H', len(dhl)) + dhl, response=True)


class Stream(io.BytesIO):
    def __init__(self, received):
        super().__init__(received)
        self.sent = []
    def write(self, data):
        self.sent.append(data)
        return len(data)


class ModemTests(unittest.TestCase):
    def test_vendor_capture_read_request(self):
        expected = bytes.fromhex('550034d0acdbcfe5400124000300000000000000c80000000100000010000000010010000800750010ef00000100000000ff040000006a6535')
        self.assertEqual(dhl_request(ft_local(8,117,struct.pack('<IH2x',IMEI_LID,1))), expected)

    def test_vendor_blocked_write_packet(self):
        e=json.loads((Path(__file__).parent / 'fixtures/modem-write.json').read_text())
        self.assertEqual(dhl_request(ft_local(8,119,struct.pack('<IH2x',IMEI_LID,1)),
            bytes.fromhex('94104502237315f80700')),bytes.fromhex(e['hex']))

    def test_live_captured_read_confirm(self):
        for e in json.loads((Path(__file__).parent / 'fixtures/modem-read.json').read_text()):
            result = ModemSession(io.BytesIO(bytes.fromhex(e['rx']))).receive()
            self.assertGreaterEqual(len(result.local),8)

    def test_write_peer_raw_escaping_and_confirm(self):
        data = bytes.fromhex('555aa5555aa500000000')
        stream = Stream(confirm(1,120))
        ModemSession(stream, token=0).write_imei_record(1,data)
        self.assertEqual(stream.sent, [dhl_request(ft_local(1,119,struct.pack('<IH2x',IMEI_LID,1)),data)])
        # Confirm payload includes bytes that must stay raw inside the tunnel.
        result = ModemSession(io.BytesIO(confirm(2,118,data))).receive()
        self.assertEqual(result.peer,data)

    def test_wrong_token_and_rejection_are_uncertain_no_retry(self):
        for token,status in [(2,0),(1,1)]:
            stream=Stream(confirm(token,120,status=status)); session=ModemSession(stream,token=0)
            with self.assertRaises(IndeterminateWrite):session.write_imei_record(1,bytes(10))
            with self.assertRaises(RuntimeError):session.write_imei_record(1,bytes(10))
            self.assertEqual(len(stream.sent),1)

    def test_checksum_corruption_rejected(self):
        raw=bytearray(confirm(1,118,bytes(10)));raw[25]^=1
        with self.assertRaises(ValueError):ModemSession(io.BytesIO(raw)).receive()

    def test_alive_only_retries_silent_timeouts(self):
        stream=Stream(b'');s=ModemSession(stream,token=0)
        response=Mock();response.local=ft_local(1,9,bytes(4))
        s.receive=Mock(side_effect=[TimeoutError('silent'),response])
        s._alive()
        self.assertEqual(len(stream.sent),2)
        self.assertEqual(stream.sent[0],stream.sent[1])
        self.assertFalse(s.failed)

    def test_partial_handshake_not_retried(self):
        stream=Stream(b'');s=ModemSession(stream,token=0)
        def partial():
            s.response_started=True
            raise TimeoutError('partial')
        s.receive=partial
        with self.assertRaises(TimeoutError):s._alive()
        self.assertEqual(len(stream.sent),1)

    def test_native_compose_vector_and_blank_rejection(self):
        self.assertEqual(encode_record('490154203237518',7,0).hex(),'94104502237315f80700')
        self.assertEqual(decode_record(bytes.fromhex('94104502237315f80700')),'490154203237518')
        for raw in [b'\xff'*10,bytes(10),bytes.fromhex('94104502237315f90700')]:
            with self.assertRaises(ValueError):decode_record(raw)

    def fake(self, blank=False):
        data=bytes.fromhex('94104502237315f80700') if not blank else b'\xff'*10
        session=Mock()
        session.connect.return_value={'serial':'BRAXUNIT00000001'}
        session.read_imei_record.return_value=data
        session.query_imei.side_effect=lambda op,arg=0: struct.pack('<H',10)+bytes(20) if op==8 else data+bytes(10)+b'\x0a\0'
        return session,data

    def test_backed_up_transaction_and_all_record_readback(self):
        s,data=self.fake()
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'record'
            def writing(rid,content):
                journal=json.loads((directory/'imei-transaction.json').read_text())
                self.assertEqual(journal['status'],'write_outcome_pending')
                self.assertEqual(journal['records']['1']['hex'],data.hex())
            s.write_imei_record.side_effect=writing
            result=rewrite_existing(s,'BRAXUNIT00000001',['490154203237518'],directory)
            self.assertEqual(result['records_verified'],[1])
            self.assertEqual(s.read_imei_record.call_count,31)
            s.write_imei_record.assert_called_once_with(1,data)

    def test_blank_never_written(self):
        s,_=self.fake(blank=True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):rewrite_existing(s,'BRAXUNIT00000001',['490154203237518'],Path(tmp)/'record')
        s.write_imei_record.assert_not_called()

    def test_failed_write_is_recorded_without_retry(self):
        s,_=self.fake();s.write_imei_record.side_effect=IndeterminateWrite('lost response')
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'record'
            with self.assertRaises(IndeterminateWrite):rewrite_existing(s,'BRAXUNIT00000001',['490154203237518'],directory)
            self.assertEqual(json.loads((directory/'imei-transaction.json').read_text())['status'],'requires_reconciliation')
        self.assertEqual(s.write_imei_record.call_count,1)

    def test_initialize_blank_and_restore_exact_records(self):
        s,_=self.fake(blank=True)
        records={i:b'\xff'*10 for i in range(1,11)}
        s.read_imei_record.side_effect=lambda i:records[i]
        s.query_imei.side_effect=lambda op,arg=0: struct.pack('<H',10)+bytes(20) if op==8 else records[arg]+bytes(10)+b'\x0a\0'
        s.write_imei_record.side_effect=lambda i,data:records.__setitem__(i,data)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'record'
            result=write_assigned(s,'BRAXUNIT00000001',['490154203237518'],path,initialize_blank=True)
            self.assertEqual(result['status'],'assigned_values_written_and_read_back')
            self.assertEqual(records[1],encode_record('490154203237518'))
            result=restore_imeis(s,'BRAXUNIT00000001',path)
            self.assertEqual(result['status'],'all_records_match_original_backup')
            self.assertEqual(records,{i:b'\xff'*10 for i in range(1,11)})

    def test_blank_initialization_requires_explicit_flag(self):
        s,_=self.fake(blank=True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):write_assigned(s,'BRAXUNIT00000001',['490154203237518'],Path(tmp)/'record')
        s.write_imei_record.assert_not_called()

    def test_independent_value_disagreement_stops_before_write(self):
        s,_=self.fake()
        s.query_imei.side_effect=lambda op,arg=0:struct.pack('<H',10)+bytes(20) if op==8 else bytes(22)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):rewrite_existing(s,'BRAXUNIT00000001',['490154203237518'],Path(tmp)/'record')
        s.write_imei_record.assert_not_called()

    def test_restore_refuses_wrong_phone(self):
        s,_=self.fake()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'record'
            rewrite_existing(s,'BRAXUNIT00000001',['490154203237518'],path)
            s.reset_mock()
            with self.assertRaises(ValueError):restore_imeis(s,'655D5XWSA6PVNJUS',path)
            s.connect.assert_not_called()
            s.write_imei_record.assert_not_called()
