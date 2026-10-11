import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from brax_snwriter.firmware_guide import read_android
from brax_snwriter.repair_ui import RepairApp


class AndroidPreflight(unittest.TestCase):
    def test_unauthorized_has_instructions_and_never_queries_phone(self):
        with patch('brax_snwriter.firmware_guide.subprocess.check_output', return_value='device123 unauthorized') as adb:
            with self.assertRaisesRegex(ValueError, 'Allow USB debugging'):
                read_android('adb')
            self.assertEqual(adb.call_count, 1)

    def test_multiple_devices_and_wireless_are_rejected(self):
        for listing in ('one device\ntwo device', '192.0.2.1:5555 device', 'emulator-5554 device'):
            with self.subTest(listing=listing), patch('brax_snwriter.firmware_guide.subprocess.check_output', return_value=listing) as adb:
                with self.assertRaises(ValueError): read_android('adb')
                self.assertEqual(adb.call_count, 1)

    def test_reads_current_os_without_rebooting(self):
        replies = ['test device', 'test', 'BraX3', 'brax3', 'test/fingerprint', '16', 'iode-test', 'normal', '1']
        with patch('brax_snwriter.firmware_guide.subprocess.check_output', side_effect=replies) as adb:
            observed = read_android('adb')
            self.assertEqual(observed['ro.lineage.version'], 'iode-test')
            self.assertTrue(all('reboot' not in c.args[0] for c in adb.call_args_list))


class FirmwareWizardGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = patch('brax_snwriter.ui_base.Path.home', return_value=Path(self.temp.name))
        self.home.start()
        self.root = tk.Tk(); self.root.withdraw()
        self.app = RepairApp(self.root)
        self.guide = self.app.firmware_guide

    def tearDown(self):
        self.root.destroy(); self.home.stop(); self.temp.cleanup()

    def test_future_actions_remain_locked_after_app_refresh(self):
        self.app.refresh_gates()
        for stage, widget, _ in self.guide.controls:
            if stage > 0:
                self.assertIn('disabled', widget.state())

    def test_prerequisite_shares_adapter_path_and_no_windows_download_stays_locked(self):
        prerequisite = self.app.flash_prerequisite
        self.assertIs(self.app.flash_fields['tool'], self.app.firmware_tool)
        prerequisite.run = lambda label, operation, done: done(operation())
        with patch('brax_snwriter.flash_prerequisite.windows_packages', return_value=[]):
            prerequisite.check_catalog()
        self.app.refresh_gates()
        self.assertIn('disabled', prerequisite.download_button.state())
        self.assertIn('Linux ZIP does not work', prerequisite.message.get())

    def test_connection_success_unlocks_only_package_step(self):
        phone = {'ro.product.model':'BraX3', 'ro.build.version.release':'16',
                 'ro.lineage.version':'test', 'ro.build.fingerprint':'test'}
        self.guide.run = lambda label, operation, done: done(operation())
        with patch('brax_snwriter.firmware_guide.read_android', return_value=phone):
            self.guide.check_phone()
        self.assertEqual(self.guide.stage, 1)
        for stage, widget, _ in self.guide.controls:
            if stage > 1: self.assertIn('disabled', widget.state())

    def test_package_change_clears_review_and_acknowledgment(self):
        self.guide.stage = 2; self.guide.downloaded = {'path':'test.zip'}
        self.guide.ack.set(True)
        self.guide.selected.set('different.zip')
        self.assertEqual(self.guide.stage, 1)
        self.assertIsNone(self.guide.downloaded)
        self.assertFalse(self.guide.ack.get())

    def test_compatibility_failure_never_advances_to_shutdown(self):
        self.guide.stage = 2; self.guide.phone = {'serial':'test'}
        self.guide.downloaded = {'path':'test.zip'}; self.guide.ack.set(True)
        self.guide.run = lambda label, operation: operation()
        with patch('brax_snwriter.firmware_guide.read_android', return_value=self.guide.phone), \
             patch('brax_snwriter.flash_prerequisite.check_installation'):
            with self.assertRaisesRegex(ValueError, 'Flashing blocked'):
                self.guide.review()
        self.assertEqual(self.guide.stage, 2)


if __name__ == '__main__': unittest.main()
