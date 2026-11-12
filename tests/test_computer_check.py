"""Computer checks; no real program, device or network is used."""
import json
import subprocess
import tempfile
import tkinter as tk
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from brax_snwriter import computer_check as checks
from brax_snwriter.repair_ui import RepairApp

Done = namedtuple('Done', 'returncode stdout')
ADB_TEXT = 'Android Debug Bridge version 1.0.41\nVersion 35.0.2-12147458\nInstalled as C:\\x\\adb.exe\n'


def snapshot(devices=(), adb=()):
    return {'devices': list(devices), 'adb': list(adb)}


class ComputerChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.adb = self.data/'tools'/'platform-tools'/'adb.exe'
        # Keep the search inside the fixture folder, whatever this computer has installed.
        self.env = patch.dict('os.environ', {'ProgramFiles': str(self.data/'pf'), 'LOCALAPPDATA': str(self.data/'la'),
                                             'SystemDrive': str(self.data/'sd'), 'USERPROFILE': str(self.data/'up')})
        self.which = patch('brax_snwriter.computer_check.shutil.which', return_value=None)
        self.env.start(); self.which.start()

    def tearDown(self):
        self.which.stop(); self.env.stop(); self.temp.cleanup()

    def test_missing_adb_names_the_fix(self):
        result = checks.check_adb('adb', self.data, runner=lambda *a, **k: self.fail('nothing to run'))
        self.assertEqual((result.state, result.fix), (checks.MISSING, 'adb'))
        self.assertIn('platform-tools', result.message)

    def test_adb_in_tools_folder_is_found_and_only_asked_its_version(self):
        self.adb.parent.mkdir(parents=True); self.adb.touch()
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return Done(0, ADB_TEXT)
        result = checks.check_adb('adb', self.data, runner=runner)
        self.assertEqual(result.state, checks.OK)
        self.assertEqual(result.detail, str(self.adb))
        self.assertIn('35.0.2', result.message)
        self.assertEqual(calls, [[str(self.adb), 'version']])

    def test_adb_that_cannot_start_is_reported_as_incomplete(self):
        self.adb.parent.mkdir(parents=True); self.adb.touch()
        def runner(command, **kwargs):
            raise OSError('missing AdbWinApi.dll')
        result = checks.check_adb(str(self.adb), self.data, runner=runner)
        self.assertEqual(result.state, checks.MISSING)
        self.assertIn('does not run', result.message)
        self.assertIn('AdbWinApi.dll', result.detail)

    def test_driver_states(self):
        self.assertEqual(checks.check_drivers(None).state, checks.UNKNOWN)
        # A MediaTek Bluetooth adapter shares the vendor ID and is not a phone.
        other = {'id': 'USB\\VID_0E8D&PID_7925\\000000000', 'status': 'Error'}
        self.assertEqual(checks.check_drivers(snapshot([other])).state, checks.UNKNOWN)
        phone = {'id': 'USB\\VID_0E8D&PID_201C\\BRAXUNIT00000001', 'status': 'OK'}
        self.assertEqual(checks.check_drivers(snapshot([phone])).state, checks.OK)
        child = {'id': 'USB\\VID_0E8D&PID_201C&MI_01\\7&1&0&0001', 'status': 'Error'}
        broken = checks.check_drivers(snapshot([phone, child]))
        self.assertEqual((broken.state, broken.fix), (checks.MISSING, 'driver'))
        self.assertIn('USB debugging', broken.message)

    def test_second_adb_is_a_warning_and_the_chosen_one_is_not(self):
        chosen = 'C:\\tools\\adb.exe'
        self.assertEqual(checks.check_other_adb(snapshot(adb=[chosen.upper()]), chosen).state, checks.OK)
        result = checks.check_other_adb(snapshot(adb=[chosen, 'D:\\studio\\adb.exe']), chosen)
        self.assertEqual(result.state, checks.WARNING)
        self.assertIn('D:\\studio\\adb.exe', result.message)

    def test_optional_tools_do_not_count_as_missing(self):
        results = [checks.check_agent(''), checks.check_flash_tool('')]
        self.assertEqual([r.state for r in results], [checks.OPTIONAL, checks.OPTIONAL])
        self.assertEqual(checks.required_missing(results), [])
        wrong = self.data/'DA_BR.bin'; wrong.write_bytes(b'not an agent')
        self.assertEqual(checks.check_agent(str(wrong)).state, checks.WARNING)
        self.assertEqual(checks.check_agent(str(self.data/'gone.bin')).state, checks.WARNING)
        self.assertEqual(checks.check_flash_tool(str(self.data/'SPFlashToolV6.exe')).state, checks.WARNING)

    def test_disk_and_server(self):
        Usage = namedtuple('Usage', 'free')
        self.assertEqual(checks.check_disk(self.data, usage=lambda path: Usage(2*1024**3)).state, checks.WARNING)
        self.assertEqual(checks.check_disk(self.data, usage=lambda path: Usage(50*1024**3)).state, checks.OK)
        def offline(url, timeout):
            raise OSError('no route')
        result = checks.check_server(opener=offline)
        self.assertEqual(result.state, checks.WARNING)
        self.assertIn('download.os-source.co', result.message)

    def test_run_checks_stays_local_and_report_has_no_serial(self):
        payload = {'devices': [{'id': 'USB\\VID_0E8D&PID_201C\\BRAXUNIT00000001', 'status': 'OK', 'name': 'x'}], 'adb': []}
        def runner(command, **kwargs):
            if command[-1] == 'version':
                return Done(0, ADB_TEXT)
            return Done(0, json.dumps(payload).encode())
        self.adb.parent.mkdir(parents=True); self.adb.touch()
        with patch('brax_snwriter.computer_check.os.name', 'nt'):
            results = checks.run_checks('adb', self.data, runner=runner,
                                        opener=lambda *a, **k: self.fail('no network at start-up'))
        self.assertEqual([r.key for r in results],
                         ['adb', 'driver', 'other_adb', 'agent', 'flash_tool', 'disk', 'server'])
        self.assertEqual(results[1].state, checks.OK)
        self.assertEqual(results[-1].state, checks.UNKNOWN)
        self.assertTrue(checks.summary(results).startswith('Ready'))
        self.assertNotIn('BRAXUNIT00000001', checks.report(results))

    def test_windows_query_failure_is_not_a_missing_driver(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 30)
        with patch('brax_snwriter.computer_check.os.name', 'nt'):
            results = checks.run_checks('adb', self.data, runner=runner)
        self.assertEqual(results[1].state, checks.UNKNOWN)


class ComputerCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = patch('brax_snwriter.ui_base.Path.home', return_value=Path(self.temp.name))
        self.home.start()
        self.root = tk.Tk(); self.root.withdraw()
        self.app = RepairApp(self.root)

    def tearDown(self):
        for timer in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', timer)
        self.root.destroy(); self.home.stop(); self.temp.cleanup()

    def results(self, adb):
        return [adb, checks.check_drivers(snapshot()), checks.check_agent(''), checks.server_not_checked()]

    def step_one_buttons(self):
        return [control for control in self.app.step_widgets[0]]

    def test_nothing_runs_until_started_and_step_one_is_open(self):
        self.assertIsNone(self.app.adb_ready)
        self.assertFalse(self.app.computer_card.running)
        for control in self.step_one_buttons():
            self.assertNotIn('disabled', control.state())

    def test_missing_adb_locks_step_one_and_shows_the_fix(self):
        a = self.app
        a.computer_card.apply(self.results(checks.Check('adb', 'ADB', checks.MISSING, 'Not found', 'adb')))
        self.assertIs(a.adb_ready, False)
        for control in self.step_one_buttons():
            self.assertIn('disabled', control.state())
        self.assertIn('ADB is missing', a.step_labels[0].get())
        self.assertTrue(a.computer_card.expanded.get())
        texts = []
        def collect(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TButton':
                    texts.append(child.cget('text'))
                collect(child)
        collect(a.computer_card.rows)
        self.assertIn('Choose adb.exe…', texts)
        self.assertIn('Open download page', texts)
        # Typing another path lifts the lock until it is checked.
        a.fields['adb'].set('C:\\somewhere\\adb.exe')
        self.assertIsNone(a.adb_ready)
        for control in self.step_one_buttons():
            self.assertNotIn('disabled', control.state())

    def test_found_adb_fills_the_field_and_opens_step_one(self):
        a = self.app
        found = str(Path(self.temp.name)/'platform-tools'/'adb.exe')
        a.computer_card.apply(self.results(checks.Check('adb', 'ADB', checks.OK, 'Version 35 at '+found, None, found)))
        self.assertIs(a.adb_ready, True)
        self.assertEqual(a.fields['adb'].get(), found)
        self.assertFalse(a.computer_card.expanded.get())
        self.assertTrue(a.computer_card.headline.get().startswith('Ready'))

    def test_reviewed_session_keeps_its_adb(self):
        a = self.app
        a.stage = 3
        before = a.fields['adb'].get()
        a.computer_card.apply(self.results(checks.Check('adb', 'ADB', checks.OK, 'Version 35', None, 'C:\\other\\adb.exe')))
        self.assertEqual(a.fields['adb'].get(), before)

    def test_start_runs_in_background_and_applies_the_result(self):
        a = self.app
        seen = {}
        def fake(adb, data, agent, tool, network=False):
            seen.update(adb=adb, network=network)
            return self.results(checks.Check('adb', 'ADB', checks.MISSING, 'Not found', 'adb'))
        a.computer_card.run_checks = fake
        a.computer_card.start()
        for _ in range(100):
            self.root.update()
            if not a.computer_card.running:
                break
            self.root.after(20)
        self.assertEqual(seen['network'], False)
        self.assertIs(a.adb_ready, False)
        self.assertIn('Needs attention', a.computer_card.headline.get())


if __name__ == '__main__':
    unittest.main()
