import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from brax_snwriter.device_connection import boot_meta, return_normal
from brax_snwriter.framing import decode
import struct
from brax_snwriter.serial_record import BASEOS_FINGERPRINT


class DeviceConnectionTests(unittest.TestCase):
    def run_scenario(self, *, bad_adb=False, bad_meta=False):
        ports = MagicMock()
        pre = SimpleNamespace(vid=0x0e8d, pid=0x2000, location='1-1', device='COM5')
        pending = SimpleNamespace(vid=0x0e8d, pid=0x200e, location='1-1:x.1', device='')
        ap = SimpleNamespace(vid=0x0e8d, pid=0x200e, location='1-1:x.1', device='COM11')
        ports.comports.side_effect = [[], [pre], [pending], [ap]]
        serial = ModuleType('serial')
        tools = ModuleType('serial.tools')
        tools.list_ports = ports
        serial.tools = tools
        with patch.dict(sys.modules, {'serial': serial, 'serial.tools': tools}), \
             patch('brax_snwriter.device_connection.subprocess.check_output', side_effect=[
                 'wrong' if bad_adb else BASEOS_FINGERPRINT, 'BRAXUNIT00000001']), \
             patch('brax_snwriter.device_connection.subprocess.Popen') as reboot, \
             patch('brax_snwriter.device_connection.open_serial') as opened, \
             patch('brax_snwriter.device_connection.enter_meta', return_value=['READY','ATEMATEX']), \
             patch('brax_snwriter.device_connection.MetaSession') as session, \
             patch('brax_snwriter.device_connection.time.sleep'):
            session.return_value.property.side_effect = [
                'WRONG' if bad_meta else 'BRAXUNIT00000001', BASEOS_FINGERPRINT]
            reboot.return_value.poll.return_value = 0
            reboot.return_value.returncode = 0
            if bad_adb or bad_meta:
                with self.assertRaises(ValueError):
                    boot_meta('adb', 'BRAXUNIT00000001', '1-1')
                if bad_adb:
                    reboot.assert_not_called()
                    opened.assert_not_called()
            else:
                result = boot_meta('adb', 'BRAXUNIT00000001', '1-1')
                self.assertEqual(result['port'], 'COM11')
                self.assertEqual([c.args[0] for c in opened.call_args_list], ['COM5','COM11'])
                self.assertEqual(reboot.call_args.args[0], ['adb','-s','BRAXUNIT00000001','reboot'])

    def test_reenumeration_waits_for_assigned_com_name(self): self.run_scenario()
    def test_wrong_adb_device_not_rebooted(self): self.run_scenario(bad_adb=True)
    def test_wrong_meta_identity_rejected(self): self.run_scenario(bad_meta=True)

    def test_normal_reboot_is_source_defined_and_targeted(self):
        serial='BRAXUNIT00000001'
        with patch('brax_snwriter.device_connection.open_serial') as opened, \
             patch('brax_snwriter.device_connection.MetaSession') as session, \
             patch('brax_snwriter.device_connection.subprocess.check_output',side_effect=['normal','1',serial,BASEOS_FINGERPRINT]) as adb:
            session.return_value.property.side_effect=[serial,BASEOS_FINGERPRINT]
            session.return_value.next_token.return_value=3
            stream=opened.return_value.__enter__.return_value
            stream.write.side_effect=len
            result=return_normal('adb','COM10',serial)
            frame=decode(stream.write.call_args.args[0],direction='request')
            self.assertEqual(frame.local,struct.pack('<HHII',3,118,0,0))
            self.assertEqual(result['state'],'normal_boot_verified')
            self.assertTrue(all(c.args[0][1:3]==['-s',serial] for c in adb.call_args_list))

    def test_normal_reboot_refuses_wrong_meta_phone(self):
        with patch('brax_snwriter.device_connection.open_serial') as opened, \
             patch('brax_snwriter.device_connection.MetaSession') as session:
            session.return_value.property.return_value='WRONG'
            with self.assertRaises(ValueError):return_normal('adb','COM10','BRAXUNIT00000001')
            opened.return_value.__enter__.return_value.write.assert_not_called()

    def test_normal_reboot_tracks_new_serial_after_provisioning(self):
        old,new='BRAXUNIT00000001','WK6XLNYXAEL7TEST'
        with patch('brax_snwriter.device_connection.open_serial') as opened, \
             patch('brax_snwriter.device_connection.MetaSession') as session, \
             patch('brax_snwriter.device_connection.subprocess.check_output',side_effect=['normal','1',new,BASEOS_FINGERPRINT]) as adb:
            session.return_value.property.side_effect=[old,BASEOS_FINGERPRINT]
            session.return_value.next_token.return_value=3
            opened.return_value.__enter__.return_value.write.side_effect=len
            result=return_normal('adb','COM10',old,expected_after=new)
            self.assertEqual(result['serial'],new)
            self.assertTrue(all(c.args[0][1:3]==['-s',new] for c in adb.call_args_list))
