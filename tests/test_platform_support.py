import io
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from brax_snwriter import platform_support as host
from brax_snwriter.discovery import check_connection
from brax_snwriter.serial_record import BASEOS_FINGERPRINT


def usb_device(root, name, vid='0e8d', pid='201c', serial='BRAXUNIT00000001'):
    folder = Path(root)/name
    folder.mkdir()
    for key, value in (('idVendor', vid), ('idProduct', pid), ('serial', serial)):
        (folder/key).write_text(value+'\n')


class LocationParsing(unittest.TestCase):
    def test_interface_suffix_from_windows_and_linux(self):
        self.assertEqual(host.usb_interface('1-2:x.1'), 1)
        self.assertEqual(host.usb_interface('1-2.4:1.1'), 1)
        self.assertEqual(host.usb_interface('1-2:1.0'), 0)
        self.assertIsNone(host.usb_interface('1-2'))
        self.assertIsNone(host.usb_interface(None))
        self.assertEqual(host.usb_device_location('3-1.2:1.0'), '3-1.2')

    def test_serial_ports_for_both_hosts(self):
        for good in ('COM5', 'com12', '/dev/ttyACM0', '/dev/ttyUSB3'):
            self.assertTrue(host.is_serial_port(good), good)
        for bad in ('', 'COM0', '/dev/ttyS0', '/dev/ttyACM0;x', 'ttyACM0', None):
            self.assertFalse(host.is_serial_port(bad), bad)


class LinuxSysfs(unittest.TestCase):
    def test_selected_phone_location_from_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            usb_device(root, '1-4')
            usb_device(root, '1-3', serial='OTHERPHONE000001')
            usb_device(root, '2-1', vid='18d1')
            (Path(root)/'1-4:1.0').mkdir()  # interface entries are ignored
            (Path(root)/'usb1').mkdir()
            self.assertEqual(host.linux_usb_location('BRAXUNIT00000001', root=root), '1-4')

    def test_missing_or_duplicate_phone_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'did not report'):
                host.linux_usb_location('BRAXUNIT00000001', root=root)
            usb_device(root, '1-4'); usb_device(root, '3-1.2')
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                host.linux_usb_location('BRAXUNIT00000001', root=root)

    def test_check_connection_uses_sysfs_on_linux(self):
        replies = ['BRAXUNIT00000001 device', 'BRAXUNIT00000001', BASEOS_FINGERPRINT, 'normal']
        with patch('brax_snwriter.discovery.subprocess.check_output', side_effect=replies), \
             patch('brax_snwriter.discovery.os.name', 'posix'), \
             patch('brax_snwriter.discovery.linux_usb_location', return_value='1-4') as lookup:
            result = check_connection('adb', 'BRAXUNIT00000001')
        lookup.assert_called_once_with('BRAXUNIT00000001')
        self.assertEqual(result['usb_location'], '1-4')


class LinuxAdb(unittest.TestCase):
    def test_plain_adb_falls_back_to_sdk_location(self):
        with tempfile.TemporaryDirectory() as folder:
            adb = Path(folder)/'adb'; adb.touch(); adb.chmod(0o755)
            with patch.object(host.shutil, 'which', return_value=None), \
                 patch.object(host, 'adb_candidates', return_value=[Path(folder)/'missing', adb]):
                self.assertEqual(host.find_adb('adb'), str(adb.resolve()))
            self.assertIsNone(host.find_adb(''))

    @unittest.skipIf(host.IS_WINDOWS, 'execute permission is a POSIX check')
    def test_adb_must_be_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            adb = Path(folder)/'adb'; adb.touch(); adb.chmod(0o644)
            self.assertFalse(host.is_adb(str(adb)))
            adb.chmod(0o755)
            self.assertTrue(host.is_adb(str(adb)))


class LinuxSpFlashTool(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'download_agent').mkdir()
        self.tool = self.root/'SPFlashToolV6'
        self.tool.write_bytes(b'\x7fELF fixture - never launched')
        self.manifest = self.root/'download_agent'/'flash.xml'
        self.manifest.write_text('<flash-mode><project>k6835v1_64</project><dagent>DA_BR.bin</dagent>'
                                 '<scatter>../MT6835_Android_scatter.xml</scatter></flash-mode>')
        (self.root/'download_agent'/'DA_BR.bin').write_bytes(b'agent')
        (self.root/'MT6835_Android_scatter.xml').write_text(
            '<root><general><config_version><platform>MT6835</platform></config_version>'
            '<storage_type>EMMC</storage_type></general></root>')
        self.device = {'hw_code': '0x1209', 'handshake_verified': True,
                       'port': '/dev/ttyACM0', 'usb_location': '1-4:1.0'}

    def tearDown(self):
        self.temp.cleanup()

    def test_linux_tool_targets_usb_location_and_sets_library_path(self):
        from brax_snwriter import flash_backend
        plan = {'command': [str(self.tool), '-c', 'format', '-f', str(self.manifest), '-p', '1-4'],
                'mode': 'format', 'device': self.device, 'confirmation': 'ERASE /dev/ttyACM0'}
        process = Mock(pid=1, stdout=io.StringIO('All command exec done!\n'), wait=Mock(return_value=0))
        popen = Mock(return_value=process)
        port = SimpleNamespace(vid=0x0e8d, pid=0x2000, device='/dev/ttyACM0', location='1-4:1.0')
        with patch.object(flash_backend, 'prepare', return_value=plan):
            flash_backend.execute(plan, 'ERASE /dev/ttyACM0', self.root/'records', lambda *_: None,
                                  popen=popen, ports=[port])
        env = popen.call_args.kwargs['env']
        self.assertTrue(env['LD_LIBRARY_PATH'].startswith(str(self.root)+os.pathsep+str(self.root/'lib')))

    def test_prepare_builds_linux_command_with_physical_location(self):
        from brax_snwriter import flash_backend
        with patch.object(flash_backend, 'require_verified_compatibility'), \
             patch.object(flash_backend, 'review_scatter', return_value={'partitions': []}):
            plan = flash_backend.prepare(self.tool, self.manifest, 'format', self.device)
        self.assertEqual(plan['command'][plan['command'].index('-p')+1], '1-4')
        self.assertEqual(plan['confirmation'], 'ERASE /dev/ttyACM0')


if __name__ == '__main__':
    unittest.main()
