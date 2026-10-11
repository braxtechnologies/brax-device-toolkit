import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from brax_snwriter import advanced_records as r
from brax_snwriter.imei import encode_record


def snapshot():
    product=bytearray(1024);product[116:132]=b'BRAXTEST00000001'
    return {'serial':'BRAXTEST00000001','fingerprint':r.BASEOS_FINGERPRINT,
            'product':bytes(product).hex(),
            'imeis':{str(i):encode_record('490154203237518').hex() for i in range(1,11)}}


class AdvancedRecordTests(unittest.TestCase):
    def test_backup_picker_remembers_saved_folder_and_handles_missing_folder(self):
        from brax_snwriter.advanced_ui import backup_start_folder, remember_backup_folder
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)
            self.assertEqual(backup_start_folder(data),data)
            first=data/'snapshots'/'first'
            r.save_snapshot(snapshot(),first)
            self.assertEqual(backup_start_folder(data),first)
            chosen=data/'selected';chosen.mkdir()
            remember_backup_folder(data,chosen)
            self.assertEqual(backup_start_folder(data),chosen.resolve())
            chosen.rmdir()
            self.assertEqual(backup_start_folder(data),first)

    def test_snapshot_checksum_identity_and_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'backup';value=snapshot()
            r.save_snapshot(value,directory)
            self.assertEqual(r.load_target(directory,'serial',value)['target'],value)
            self.assertEqual(r.describe(value)['serial'],'BRAXTEST00000001')
            with self.assertRaisesRegex(ValueError,'identity'):
                r.load_target(directory,'imei',{**value,'serial':'OTHERPHONE000001'})
            (directory/'snapshot.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'checksum'):
                r.load_target(directory,'serial',value)

    def test_review_changed_records_refuses_before_any_write(self):
        ap,modem=MagicMock(),MagicMock();value=snapshot()
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'capture',return_value={**value,'serial':'OTHERPHONE000001'}):
            with self.assertRaisesRegex(ValueError,'changed since review'):
                r.restore_reviewed(ap,modem,value['serial'],{'current':value},'serial',Path(tmp)/'restore')
        ap.write_record.assert_not_called();modem.write_imei_record.assert_not_called()

    def test_serial_restore_backs_up_before_write_and_verifies(self):
        value=snapshot();target=copy.deepcopy(value)
        product=bytearray.fromhex(value['product']);product[116:136]=bytes(20);target['product']=product.hex()
        ap,modem=MagicMock(),MagicMock();ap.read_record.return_value=bytes(product)
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'capture',return_value=value):
            directory=Path(tmp)/'restore'
            def write(*args):
                self.assertTrue((directory/'write-attempted').exists())
                self.assertTrue((directory/'snapshot.json').exists())
            ap.write_record.side_effect=write
            result=r.restore_reviewed(ap,modem,value['serial'],{'kind':'snapshot','current':value,'target':target},'serial',directory)
            self.assertTrue(result['record_readback_verified'])
            self.assertFalse(result['reboot_persistence_verified'])
        modem.write_imei_record.assert_not_called()

    def test_unrelated_product_changes_block_restore(self):
        value=snapshot();target=copy.deepcopy(value);target['product']='ff'+value['product'][2:]
        ap,modem=MagicMock(),MagicMock()
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'capture',return_value=value):
            with self.assertRaisesRegex(ValueError,'unrelated'):
                r.restore_reviewed(ap,modem,value['serial'],{'kind':'snapshot','current':value,'target':target},'serial',Path(tmp)/'restore')
        ap.write_record.assert_not_called()

    def test_modem_partial_failure_is_indeterminate(self):
        value=snapshot();target=copy.deepcopy(value);target['imeis']['1']='ff'*10
        ap,modem=MagicMock(),MagicMock();modem.write_imei_record.side_effect=OSError('disconnected')
        with tempfile.TemporaryDirectory() as tmp, patch.object(r,'capture',return_value=value):
            with self.assertRaises(r.IndeterminateWrite):
                r.restore_reviewed(ap,modem,value['serial'],{'kind':'snapshot','current':value,'target':target},'imei',Path(tmp)/'restore')
        self.assertEqual(modem.write_imei_record.call_count,1)

    def test_detection_without_phone_does_not_open_serial(self):
        from brax_snwriter.advanced_ui import detect
        with patch('serial.tools.list_ports.comports',return_value=[]), \
             patch('brax_snwriter.advanced_ui.subprocess.check_output',return_value='List of devices attached\n'), \
             patch('brax_snwriter.advanced_ui.open_serial') as serial:
            with self.assertRaisesRegex(ValueError,'not visible'):detect('adb')
            serial.assert_not_called()

    def test_meta_and_adb_same_phone_are_one_device(self):
        from types import SimpleNamespace
        from brax_snwriter.advanced_ui import detect
        serial='BRAXTEST00000001'
        port=SimpleNamespace(vid=0x0e8d,pid=0x200e,device='COM13',location='1-2:x.1')
        session=MagicMock();session.property.side_effect=[serial,r.BASEOS_FINGERPRINT]
        with patch('serial.tools.list_ports.comports',return_value=[port]), \
             patch('brax_snwriter.advanced_ui.open_serial'), \
             patch('brax_snwriter.advanced_ui.MetaSession',return_value=session), \
             patch('brax_snwriter.advanced_ui.subprocess.check_output',side_effect=[serial+' device\nemulator-5582 device',serial,r.BASEOS_FINGERPRINT,'meta']):
            result=detect('adb')
        self.assertEqual(result['mode'],'meta');self.assertEqual(result['port'],'COM13')

    def test_meta_and_different_adb_phone_are_rejected(self):
        from types import SimpleNamespace
        from brax_snwriter.advanced_ui import detect
        port=SimpleNamespace(vid=0x0e8d,pid=0x200e,device='COM13',location='1-2:x.1')
        session=MagicMock();session.property.side_effect=['BRAXTEST00000001',r.BASEOS_FINGERPRINT]
        with patch('serial.tools.list_ports.comports',return_value=[port]), \
             patch('brax_snwriter.advanced_ui.open_serial'), \
             patch('brax_snwriter.advanced_ui.MetaSession',return_value=session), \
             patch('brax_snwriter.advanced_ui.subprocess.check_output',return_value='OTHERPHONE000001 device'):
            with self.assertRaisesRegex(ValueError,'different phones'):detect('adb')
