import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from brax_snwriter.repair_ui import RepairApp


class CompactUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.home=patch('brax_snwriter.ui_base.Path.home',return_value=Path(self.temp.name))
        self.home.start()
        self.root=tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        for timer in self.root.tk.call('after','info'):
            # Some timers belong to child widgets (tooltips). Cancel Tcl's
            # timer only; each widget destroys its own Python command below.
            # root.after_cancel would delete a child's command twice.
            self.root.tk.call('after', 'cancel', timer)
        self.root.destroy()
        self.home.stop()
        self.temp.cleanup()


    def test_device_discovery_does_not_choose_a_target(self):
        app=RepairApp(self.root)
        app.set_device_choices({'adb':'List of devices attached\nPHONE1 device model:BraX3\nPHONE2 unauthorized\n'})
        self.assertEqual(tuple(app.inputs['serial'].cget('values')),('PHONE1',))
        self.assertEqual(app.fields['serial'].get(),'')


    def test_busy_state_locks_inputs_and_recovers_after_callback_error(self):
        app=RepairApp(self.root)
        with patch('brax_snwriter.ui_base.threading.Thread') as thread:
            app.run('Read only',lambda: {'ok':True},lambda result: 1/0)
            self.assertTrue(app.busy)
            self.assertEqual(str(app.inputs['serial'].cget('state')),'disabled')
            thread.call_args.kwargs['target']()
            app.drain()
        self.assertFalse(app.busy)
        self.assertEqual(str(app.inputs['serial'].cget('state')),'readonly')
        self.assertIn('display update failed',app.status.get())


    def test_changing_phone_invalidates_port_and_pending_serial(self):
        app=RepairApp(self.root)
        app.fields['serial'].set('BRAXUNIT00000001')
        app.fields['port'].set('COM10');app.fields['usb'].set('1-1')
        app.pending_serial='WK6XLNYXAEL7TEST'
        app.fields['serial'].set('WK6XLNYXAEL7TEST')
        self.assertEqual(app.fields['port'].get(),'')
        self.assertEqual(app.fields['usb'].get(),'')
        self.assertIsNone(app.pending_serial)



class RecordDirectoryTests(unittest.TestCase):
    def test_fresh_install_uses_toolkit_folder(self):
        from brax_snwriter.ui_base import data_directory
        with tempfile.TemporaryDirectory() as temp, patch('brax_snwriter.ui_base.Path.home', return_value=Path(temp)):
            self.assertEqual(data_directory(), Path(temp) / 'BraxDeviceToolkit')
            self.assertTrue(data_directory().is_dir())

    def test_upgrade_keeps_existing_backups_accessible(self):
        from brax_snwriter.ui_base import data_directory
        with tempfile.TemporaryDirectory() as temp, patch('brax_snwriter.ui_base.Path.home', return_value=Path(temp)):
            previous = Path(temp) / 'BraxStations' / 'identity-station'
            previous.mkdir(parents=True)
            backup = previous / 'backup.json'
            backup.write_text('original records')
            self.assertEqual(data_directory(), previous)
            self.assertEqual(backup.read_text(), 'original records')
