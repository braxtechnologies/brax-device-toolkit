import tempfile
import unittest
from pathlib import Path
from brax_snwriter import mac_records as m
from brax_snwriter.session import IndeterminateWrite

class Phone:
    def __init__(self):
        self.data={lid:bytes((i%251 for i in range(size))) for lid,size,_ in m.PROFILES.values()}
        self.writes=[]
    def property(self,k): return m.BASEOS_FINGERPRINT if k=='ro.build.fingerprint' else 'PHONE'
    def read_record(self,lid,record): return self.data[lid]
    def write_record(self,lid,record,data): self.writes.append(lid);self.data[lid]=data

class MacTests(unittest.TestCase):
    def test_reject_invalid_addresses(self):
        for value in ['00:00:00:00:00:00','FF:FF:FF:FF:FF:FF','01:12:34:56:78:90','123456789012','02:GG:00:00:00:01']:
            with self.assertRaises(ValueError):m.parse_address(value)
    def test_selected_bytes_only_and_restore(self):
        p=Phone();before=m.capture(p,'PHONE');review=m.plan(before,{'wifi':'02:12:34:56:78:9A'})
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)/'write';m.write_reviewed(p,'PHONE',review,folder)
            self.assertEqual(p.writes,[55]);self.assertEqual(p.data[1].hex(),before['records']['bluetooth'])
            original=bytes.fromhex(before['records']['wifi']);self.assertEqual(p.data[55][:4],original[:4]);self.assertEqual(p.data[55][10:],original[10:])
            restore=m.restore_plan(folder,m.capture(p,'PHONE'));m.write_reviewed(p,'PHONE',restore,Path(t)/'restore')
            self.assertEqual(m.capture(p,'PHONE'),before)
    def test_calibration_tamper_refused(self):
        p=Phone();r=m.plan(m.capture(p,'PHONE'),{'bluetooth':'02:12:34:56:78:90'})
        r['after']['records']['bluetooth']=r['after']['records']['bluetooth'][:-2]+'FF'
        with self.assertRaises(ValueError):m.validate_plan(r)
    def test_stale_review_does_not_write(self):
        p=Phone();r=m.plan(m.capture(p,'PHONE'),{'wifi':'02:12:34:56:78:90'});p.data[55]=bytes(2048)
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):m.write_reviewed(p,'PHONE',r,Path(t)/'write')
        self.assertFalse(p.writes)
    def test_wrong_profile_or_size_refused(self):
        p=Phone();p.data[1]=bytes(1024)
        with self.assertRaises(ValueError):m.capture(p,'PHONE')
        with self.assertRaises(ValueError):m.capture(Phone(),'OTHER')
    def test_interrupted_second_write_backup_and_restore(self):
        p=Phone();before=m.capture(p,'PHONE');r=m.plan(before,{'bluetooth':'02:12:34:56:78:90','wifi':'02:12:34:56:78:92'})
        real=p.write_record
        def failing(lid,rec,data):
            if lid==55: raise TimeoutError()
            real(lid,rec,data)
        p.write_record=failing
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)/'write'
            with self.assertRaises(IndeterminateWrite):m.write_reviewed(p,'PHONE',r,folder)
            self.assertTrue((folder/'write-attempted').exists());self.assertFalse((folder/'readback-verified').exists())
            restoration=m.restore_plan(folder,m.capture(p,'PHONE'));p.write_record=real
            m.write_reviewed(p,'PHONE',restoration,Path(t)/'restore');self.assertEqual(m.capture(p,'PHONE'),before)
