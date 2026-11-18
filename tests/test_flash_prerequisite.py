import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from brax_snwriter.flash_prerequisite import (windows_packages, linux_packages, check_installation,
    REQUIRED_FILES, LINUX_REQUIRED_FILES)


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
                check_installation(str(path), windows=True)
            for name in REQUIRED_FILES: (path.parent/name).write_bytes(b'fixture')
            result = check_installation(str(path), windows=True)
            self.assertIn('not launched', result['status'])
            launch.assert_not_called()

    def test_non_windows_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'SPFlashToolV6.exe'; path.write_bytes(b'\x7fELF')
            with self.assertRaisesRegex(ValueError, 'Windows executable'):
                check_installation(str(path), windows=True)

    def test_linux_package_is_offered_only_for_linux(self):
        data = listing(['sp-flash-tool-linux-v6.2228.zip', 'sp-flash-tool-linux-v6.2228.zip.sha256',
                        'sp-flash-tool-windows-v6.zip', 'sp-flash-tool-windows-v6.zip.sha256'])
        self.assertEqual([row['name'] for row in linux_packages(lambda url:data)], ['sp-flash-tool-linux-v6.2228.zip'])

    @unittest.skipIf(os.name == 'nt', 'execute permission is a POSIX check')
    def test_linux_tool_requires_elf_libraries_and_execute_bit(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as launch:
            path = Path(folder)/'SPFlashToolV6'; path.write_bytes(b'\x7fELFfixture')
            with self.assertRaisesRegex(ValueError, 'entire ZIP'):
                check_installation(str(path), windows=False)
            for name in LINUX_REQUIRED_FILES:
                (path.parent/name).parent.mkdir(exist_ok=True); (path.parent/name).write_bytes(b'fixture')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'chmod'):
                check_installation(str(path), windows=False)
            path.chmod(0o755)
            # The vendor launcher script resolves to the binary beside it.
            result = check_installation(str(path)+'.sh', windows=False)
            self.assertEqual(result['path'], str(path))
            launch.assert_not_called()

    def test_windows_tool_is_rejected_on_linux(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'SPFlashToolV6.exe'; path.write_bytes(b'MZfixture')
            with self.assertRaisesRegex(ValueError, 'Linux tool folder'):
                check_installation(str(path), windows=False)


if __name__ == '__main__': unittest.main()
