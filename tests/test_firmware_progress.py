import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from brax_snwriter.firmware_progress import FirmwareProgress, parse_progress


class ProgressTests(unittest.TestCase):
    def test_partition_progress_is_not_overall_progress(self):
        self.assertEqual(parse_progress('100% of [md1img_a [6/25]] data has been sent'),
                         (100, 'Transferring md1img_a [6/25]'))
        self.assertIsNone(parse_progress('All command exec done!'))

    def test_log_follow_keeps_incomplete_lines_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            try:
                panel = FirmwareProgress(root, root, Path(directory)/'logs')
                source = Path(directory)/'flash.log'
                source.write_text('58% of [super [24/25]] data has been sent\nError: discon', encoding='utf-8')
                panel.follow = source
                panel.drain()
                self.assertEqual(panel.bar['value'], 58)
                self.assertEqual(panel.pending, 'Error: discon')
                with source.open('a', encoding='utf-8') as out:
                    out.write('nected\n')
                panel.drain()
                self.assertEqual(panel.label.get(), 'Error: disconnected')
                self.assertIn('Error: disconnected', panel.path.read_text(encoding='utf-8'))
            finally:
                for timer in root.tk.call('after', 'info'):
                    root.tk.call('after', 'cancel', timer)
                root.destroy()
