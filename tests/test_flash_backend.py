import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from brax_snwriter.flash_backend import MODES, prepare, execute
from tests import test_flash_plan


class FlashBackendTests(unittest.TestCase):
    def setUp(self):
        # Exercise the vendor adapter separately from the real fail-closed gate.
        self.compatibility = patch('brax_snwriter.flash_backend.require_verified_compatibility')
        self.compatibility.start()
        self.addCleanup(self.compatibility.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tool = self.root/'SPFlashToolV6.exe'
        self.tool.write_bytes(b'fake executable - never launched')
        agent = self.root/'package'/'download_agent'
        agent.mkdir(parents=True)
        self.manifest = agent/'flash.xml'
        self.manifest.write_text('<flash-mode><project>k6835v1_64</project><dagent>DA_BR.bin</dagent><scatter>../MT6835_Android_scatter.xml</scatter></flash-mode>')
        (agent/'DA_BR.bin').write_bytes(b'agent')
        (agent.parent/'MT6835_Android_scatter.xml').write_bytes(test_flash_plan.PlanTests().data())
        self.image = agent.parent/'boot.img'
        self.image.write_bytes(b'image')
        self.device = {'hw_code': '0x1209', 'handshake_verified': True, 'port': 'COM5', 'usb_location': '1-1'}

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, mode='download'):
        return prepare(self.tool, self.manifest, mode, self.device)

    def test_exact_mode_flags_and_format_does_not_reboot(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                plan = self.plan(mode)
                self.assertEqual(plan['command'][1:3], ['-c', mode])
                self.assertEqual('-b' in plan['command'], mode != 'format')
                self.assertEqual(plan['confirmation'], ('INSTALL ' if mode == 'download' else 'ERASE ')+'COM5')

    def test_changed_image_or_command_cannot_execute(self):
        plan = self.plan()
        self.image.write_bytes(b'changed')
        process = Mock()
        with self.assertRaisesRegex(ValueError, 'changed'):
            execute(plan, plan['confirmation'], self.root/'records', print, popen=process, ports=[])
        process.assert_not_called()
        plan = self.plan()
        plan['command'].append('--unexpected')
        with self.assertRaises(ValueError):
            execute(plan, plan['confirmation'], self.root/'records', print, popen=process, ports=[])

    def test_no_confirmation_no_process(self):
        process = Mock()
        with self.assertRaises(ValueError):
            execute(self.plan('format'), '', self.root/'records', print, popen=process, ports=[])
        process.assert_not_called()

    def test_wrong_usb_location_rejected(self):
        plan = self.plan()
        port = SimpleNamespace(vid=0x0e8d, pid=0x2000, device='COM5', location='1-2')
        with self.assertRaisesRegex(ValueError, 'differs'):
            execute(plan, plan['confirmation'], self.root/'records', print, ports=[port])

    def test_nonzero_exit_records_failure_even_with_success_text(self):
        plan = self.plan('firmware-upgrade')
        proc = Mock(pid=123, stdout=io.StringIO('All command exec done!\n'))
        proc.wait.return_value = 1
        with self.assertRaises(RuntimeError):
            execute(plan, plan['confirmation'], self.root/'records', lambda *_: None, popen=Mock(return_value=proc), ports=[])
        result = json.loads(next((self.root/'records').rglob('result.json')).read_text())
        self.assertEqual(result['state'], 'failed_or_unconfirmed')
        self.assertFalse(result['boot_verified'])

    def test_success_logs_output_but_does_not_claim_boot(self):
        plan = self.plan('format-download')
        proc = Mock(pid=123, stdout=io.StringIO('50% of [boot [1/2]] data has been sent\nAll command exec done!\n'))
        proc.wait.return_value = 0
        events = []
        result = execute(plan, plan['confirmation'], self.root/'records', events.append, popen=Mock(return_value=proc), ports=[])
        self.assertEqual(result['state'], 'tool_completed')
        self.assertFalse(result['boot_verified'])
        self.assertIn('50% of [boot [1/2]] data has been sent', events)
