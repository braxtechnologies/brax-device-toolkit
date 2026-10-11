"""Native workflow tests; all hardware operations are mocked."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from brax_snwriter.repair_ui import RepairApp

class GuidedRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = patch('brax_snwriter.ui_base.Path.home', return_value=Path(self.temp.name))
        self.home.start()
        self.root = tk.Tk(); self.root.withdraw()
        self.app = RepairApp(self.root)

    def tearDown(self):
        for timer in self.root.tk.call('after','info'):
            self.root.tk.call('after', 'cancel', timer)
        self.root.destroy(); self.home.stop(); self.temp.cleanup()

    def reviewed(self):
        a = self.app
        for k,v in {'serial':'BRAXUNIT00000001','new':'BRAXTEST00000001','imei1':'490154203237518','imei2':'356938035643809','port':'COM3','usb':'1-2'}.items():
            a.fields[k].set(v)
        a.stage = 2; a.validate_guided()
        self.assertEqual(a.stage, 3)
        a.confirm.set(True)
        return a

    def test_adb_resolves_full_path_and_missing_path_is_reported(self):
        a = self.app
        executable = Path(self.temp.name)/'platform-tools'/'adb.exe'
        executable.parent.mkdir(); executable.touch()
        a.fields['adb'].set('adb')
        with patch('brax_snwriter.repair_ui.shutil.which', return_value=str(executable)):
            a.resolve_adb()
        self.assertEqual(a.fields['adb'].get(), str(executable.resolve()))
        self.assertTrue(a.adb_status.get().startswith('Found'))
        a.fields['adb'].set(str(executable.parent/'missing.exe'))
        self.assertTrue(a.adb_status.get().startswith('Not found'))

    def test_browse_is_locked_after_review_and_can_be_cancelled(self):
        a = self.reviewed()
        self.assertIn('disabled', a.adb_browse.state())
        with patch('brax_snwriter.repair_ui.filedialog.askopenfilename') as dialog:
            a.choose_adb(); dialog.assert_not_called()
        a.edit_guided()
        previous = a.fields['adb'].get()
        with patch('brax_snwriter.repair_ui.filedialog.askopenfilename', return_value=''):
            a.choose_adb()
        self.assertEqual(previous, a.fields['adb'].get())

    def test_four_tabs_and_later_steps_locked(self):
        a = self.app
        self.assertEqual([a.tabs.tab(t,'text') for t in a.tabs.tabs()], ['Serial & IMEI','Advanced tools','Session log','Install OS'])
        for controls in a.step_widgets[1:]:
            for control in controls:
                self.assertIn('disabled', control.state())
        with self.assertRaises(ValueError): a.write_guided()

    def test_boot_probe_does_not_require_adb_or_unlock_flash(self):
        a=self.app
        result={'mode':'Preloader','port':'COM5','hw_code':'0x1209',
                'secure_boot':False,'serial_link_auth':False,'download_agent_auth':False}
        button=next(b for b in a.buttons if b.cget('text')=='Check powered-off connection')
        with patch('brax_snwriter.boot_probe.wait_and_probe',return_value=result), \
             patch('brax_snwriter.ui_base.threading.Thread') as thread:
            button.invoke();thread.call_args.kwargs['target']();a.drain()
        self.assertIn('Boot connection verified',a.status.get())
        self.assertFalse(a.recovery_required)
        self.assertEqual(a.stage,0)
        self.assertFalse(any('Flash selected OS' in b.cget('text') for b in a.buttons))

    def test_boot_timeout_reports_reconnect_without_recovery(self):
        a=self.app
        button=next(b for b in a.buttons if b.cget('text')=='Check powered-off connection')
        with patch('brax_snwriter.boot_probe.wait_and_probe',side_effect=TimeoutError('Reconnect powered-off phone')), \
             patch('brax_snwriter.ui_base.threading.Thread') as thread:
            button.invoke();thread.call_args.kwargs['target']();a.drain()
        self.assertEqual(a.status.get(),'Reconnect powered-off phone')
        self.assertFalse(a.recovery_required)

    def test_advanced_detection_enables_only_the_other_mode(self):
        from brax_snwriter.serial_record import BASEOS_FINGERPRINT
        a=self.app
        result={'serial':'BRAXUNIT00000001','fingerprint':BASEOS_FINGERPRINT,
                'mode':'normal','port':'','usb_location':'1-2'}
        button=next(b for b in a.buttons if b.cget('text')=='Detect current mode')
        with patch('brax_snwriter.advanced_ui.detect',return_value=result), \
             patch('brax_snwriter.ui_base.threading.Thread') as thread:
            button.invoke();thread.call_args.kwargs['target']();a.drain()
        self.assertIn('disabled',a.normal_mode_button.state())
        self.assertNotIn('disabled',a.meta_mode_button.state())
        self.assertNotIn('blank',a.inputs)
        self.assertNotIn('imei_blank',a.inputs)

    def test_failed_connection_never_unlocks_meta(self):
        a = self.app
        with patch('brax_snwriter.repair_ui.check_connection', side_effect=ValueError('wrong firmware')), patch('brax_snwriter.ui_base.threading.Thread') as thread:
            a.connect_guided()
            thread.call_args.kwargs['target'](); a.drain()
        self.assertEqual(a.stage, 0)
        self.assertIn('disabled', a.step_widgets[1][0].state())

    def test_edited_identifiers_require_new_validation_and_confirmation(self):
        a = self.reviewed()
        a.fields['new'].set('BRAXTEST00000002')
        self.assertEqual(a.stage, 2)
        self.assertFalse(a.confirm.get())
        self.assertIsNone(a.reviewed)
        self.assertIn('disabled', a.write_button.state())

    def test_duplicate_imeis_cannot_advance(self):
        a = self.reviewed(); a.edit_guided()
        a.fields['imei2'].set(a.fields['imei1'].get()); a.validate_guided()
        self.assertEqual(a.stage, 2)
        self.assertIn('distinct', a.status.get())


    def test_empty_serial_detected_during_preparation(self):
        a = self.app
        a.stage = 1
        a.fields['usb'].set('1-2')
        with patch('brax_snwriter.repair_ui.check_connection', return_value={'usb_location':'1-2'}), \
             patch('brax_snwriter.repair_ui.boot_meta', return_value={'port':'COM10'}), \
             patch('brax_snwriter.repair_ui.probe_identity', return_value={'serial_field':''}), \
             patch('brax_snwriter.repair_ui.open_serial'), \
             patch('brax_snwriter.repair_ui.inspect_imeis', return_value=({}, {1:b'\xff'*10,2:b'\xff'*10}, {1:{'valid':False},2:{'valid':False}})), \
             patch('brax_snwriter.ui_base.threading.Thread') as thread:
            a.prepare_guided(); thread.call_args.kwargs['target'](); a.drain()
        self.assertEqual(a.stage, 2)
        self.assertEqual(a.fields['blank'].get(), 'Yes')
        self.assertEqual(a.fields['imei_blank'].get(), 'Yes')
        self.assertIn('empty', a.summaries[1].get())
        self.assertFalse(a.confirm.get())

    def test_full_error_shown_once_without_truncation(self):
        a = self.app
        message = 'ValueError: ' + 'Complete recovery instructions. ' * 15
        a.active_action_message = a.action_messages['Write & verify identifiers']
        a.queue.put(('error', message)); a.drain()
        self.assertEqual(a.status.get(), 'Stopped')
        self.assertEqual(a.action_messages['Write & verify identifiers'].get(), message)
        self.assertFalse(hasattr(a, 'result_preview'))

    def test_unknown_modem_format_blocks_preparation(self):
        a = self.app
        a.stage = 1
        a.fields['usb'].set('1-2')
        with patch('brax_snwriter.repair_ui.check_connection', return_value={'usb_location':'1-2'}), \
             patch('brax_snwriter.repair_ui.boot_meta', return_value={'port':'COM10'}), \
             patch('brax_snwriter.repair_ui.probe_identity', return_value={'serial_field':''}), \
             patch('brax_snwriter.repair_ui.open_serial'), \
             patch('brax_snwriter.repair_ui.inspect_imeis', return_value=({}, {1:b'\0'*10,2:b'\xff'*10}, {1:{'valid':False},2:{'valid':False}})), \
             patch('brax_snwriter.ui_base.threading.Thread') as thread:
            a.prepare_guided(); thread.call_args.kwargs['target'](); a.drain()
        self.assertEqual(a.stage, 1)
        self.assertEqual(a.fields['imei_blank'].get(), 'No')
        self.assertIn('unsupported format', a.last_error)

    def test_partial_write_failure_blocks_retry_and_preserves_journal(self):
        a = self.reviewed()
        session = MagicMock()
        from brax_snwriter.serial_record import BASEOS_FINGERPRINT
        session.property.side_effect=[a.fields['serial'].get(),BASEOS_FINGERPRINT]
        session.read_record.return_value=bytes(1024)
        with patch('brax_snwriter.repair_ui.open_serial'), patch('brax_snwriter.repair_ui.MetaSession', return_value=session), patch('brax_snwriter.repair_ui.patch_serial'), patch('brax_snwriter.repair_ui.write_assigned', return_value={'backup':'saved'}), patch('brax_snwriter.repair_ui.change_serial', side_effect=RuntimeError('readback uncertain')), patch('brax_snwriter.ui_base.threading.Thread') as thread:
            a.write_guided()
            thread.call_args.kwargs['target'](); a.drain()
        self.assertTrue(a.recovery_required)
        self.assertEqual(a.stage, 3)
        self.assertIn('disabled',a.write_button.state())
        self.assertTrue(list((a.data/'guided-sessions').glob('*/session.json')))
        with self.assertRaises(ValueError): a.write_guided()

    def test_write_success_still_requires_reboot_readback(self):
        a = self.reviewed()
        with patch.object(a, 'run') as run:
            a.write_guided()
            run.call_args.args[2]({'record':'mock'})
        self.assertEqual(a.stage,4)
        self.assertFalse(a.recovery_required)
        self.assertIn('disabled',a.next_button.state())

    def test_reboot_mismatch_never_completes(self):
        a = self.reviewed(); a.stage=4
        a.session_record=Path(self.temp.name)
        with patch('brax_snwriter.repair_ui.return_normal'), patch('brax_snwriter.repair_ui.check_connection',return_value={'usb_location':'1-2'}), patch('brax_snwriter.repair_ui.boot_meta',return_value={'port':'COM4'}), patch('brax_snwriter.repair_ui.probe_identity',return_value={'serial_field':'WRONG'}), patch('brax_snwriter.ui_base.threading.Thread') as thread:
            a.verify_guided(); thread.call_args.kwargs['target'](); a.drain()
        self.assertEqual(a.stage,4)
        self.assertTrue(a.recovery_required)
        self.assertIn('disabled',a.next_button.state())

    def test_optional_mac_fields_review_and_invalidate(self):
        a=self.reviewed();a.edit_guided()
        a.fields['bluetooth'].set('02:12:34:56:78:90')
        a.fields['wifi'].set('02:12:34:56:78:92')
        a.validate_guided()
        self.assertEqual(a.stage,3)
        self.assertIn('02:12:34:56:78:90',a.review_text.get())
        self.assertIn('02:12:34:56:78:92',a.review_text.get())
        a.fields['wifi'].set('02:12:34:56:78:94')
        self.assertFalse(a.confirm.get())

    def test_invalid_mac_blocks_guided_review(self):
        a=self.reviewed();a.edit_guided();a.fields['wifi'].set('FF:FF:FF:FF:FF:FF')
        a.validate_guided();self.assertEqual(a.stage,2)
        self.assertIn('unicast',a.status.get())

    def test_mac_preflight_failure_prevents_imei_write(self):
        a=self.reviewed();a.edit_guided();a.fields['wifi'].set('02:12:34:56:78:90');a.validate_guided();a.confirm.set(True)
        from brax_snwriter.serial_record import BASEOS_FINGERPRINT
        session=MagicMock();session.property.side_effect=[a.fields['serial'].get(),BASEOS_FINGERPRINT]
        with patch('brax_snwriter.repair_ui.open_serial'), patch('brax_snwriter.repair_ui.MetaSession',return_value=session), patch('brax_snwriter.repair_ui.mac_records.capture',side_effect=ValueError('Unsupported MAC layout')), patch('brax_snwriter.repair_ui.write_assigned') as modem, patch('brax_snwriter.ui_base.threading.Thread') as thread:
            a.write_guided();thread.call_args.kwargs['target']();a.drain()
        modem.assert_not_called();self.assertTrue(a.recovery_required)

    def test_guided_mac_write_runs_before_serial_change(self):
        a=self.reviewed();a.edit_guided();a.fields['bluetooth'].set('02:12:34:56:78:90');a.validate_guided();a.confirm.set(True)
        from brax_snwriter.serial_record import BASEOS_FINGERPRINT
        session=MagicMock();session.property.side_effect=[a.fields['serial'].get(),BASEOS_FINGERPRINT];session.read_record.return_value=bytes(1024)
        events=[]
        with patch('brax_snwriter.repair_ui.open_serial'), patch('brax_snwriter.repair_ui.MetaSession',return_value=session), patch('brax_snwriter.repair_ui.patch_serial'), patch('brax_snwriter.repair_ui.mac_records.capture'), patch('brax_snwriter.repair_ui.mac_records.plan',return_value={'review':'prepared'}), patch('brax_snwriter.repair_ui.mac_records.write_reviewed',side_effect=lambda *args:events.append('mac')), patch('brax_snwriter.repair_ui.write_assigned',side_effect=lambda *args,**kw:events.append('imei')), patch('brax_snwriter.repair_ui.change_serial',side_effect=lambda *args,**kw:events.append('serial')), patch('brax_snwriter.ui_base.threading.Thread') as thread:
            a.write_guided();thread.call_args.kwargs['target']();a.drain()
        self.assertEqual(events,['imei','mac','serial']);self.assertEqual(a.stage,4)

    def test_flash_mode_selection_never_launches_vendor(self):
        from brax_snwriter.flash_backend import MODES
        with patch('brax_snwriter.flash_controls.execute') as execute:
            for label, _ in MODES.values():
                self.app.flash_fields['mode'].set(label)
                self.root.update_idletasks()
            execute.assert_not_called()

    def test_cancel_flash_review_never_launches_vendor(self):
        a = self.app
        plan = {'mode':'format', 'device':{'port':'COM5','usb_location':'1-1'},
                'command':['tool','-c','format','-f','release/download_agent/flash.xml'],
                'partitions':[], 'confirmation':'ERASE COM5'}
        button = next(b for b in a.buttons if b.cget('text') == 'Review flash / format operation')
        with patch('brax_snwriter.flash_controls.prepare', return_value=plan), \
             patch('brax_snwriter.flash_controls.simpledialog.askstring', return_value=None), \
             patch('brax_snwriter.flash_controls.execute') as execute, \
             patch('brax_snwriter.ui_base.threading.Thread') as thread:
            button.invoke()
            thread.call_args.kwargs['target']()
            a.drain()
            execute.assert_not_called()
        self.assertFalse(a.busy)

if __name__=='__main__': unittest.main()
