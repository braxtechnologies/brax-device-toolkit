import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from brax_snwriter.flash_prerequisite import windows_packages, check_installation, REQUIRED_FILES


def listing(names):
    data = {'kind':'Index', 'dir_exists':True, 'paths':[
        {'name': name, 'path_type':'File', 'size':100} for name in names]}
    return b'<template id="index-data">'+base64.b64encode(json.dumps(data).encode())+b'</template>'


class Prerequisites(unittest.TestCase):
    def test_linux_and_unchecksummed_windows_are_not_offered(self):
        data = listing(['sp-flash-tool-linux-v6.zip', 'sp-flash-tool-linux-v6.zip.sha256',
                        'sp-flash-tool-windows-v6.zip'])
        self.assertEqual(windows_packages(lambda url:data), [])

    def test_windows_package_uses_catalog_size_and_same_origin_checksum(self):
        data = listing(['sp-flash-tool-windows-v6.zip', 'sp-flash-tool-windows-v6.zip.sha256'])
        rows = windows_packages(lambda url:data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['size'], 100)
        self.assertEqual(rows[0]['sha256_url'], rows[0]['url']+'.sha256')
        self.assertTrue(rows[0]['url'].startswith('https://download.os-source.co/brax3/flash-tool/'))

    def test_executable_alone_is_not_ready_and_nothing_is_executed(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as launch:
            path = Path(folder)/'SPFlashToolV6.exe'; path.write_bytes(b'MZfixture')
            with self.assertRaisesRegex(ValueError, 'entire ZIP'):
                check_installation(str(path))
            for name in REQUIRED_FILES: (path.parent/name).write_bytes(b'fixture')
            result = check_installation(str(path))
            self.assertIn('not launched', result['status'])
            launch.assert_not_called()

    def test_non_windows_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'SPFlashToolV6.exe'; path.write_bytes(b'\x7fELF')
            with self.assertRaisesRegex(ValueError, 'Windows executable'):
                check_installation(str(path))


if __name__ == '__main__': unittest.main()
