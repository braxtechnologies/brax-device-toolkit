from dataclasses import replace
import unittest
from brax_snwriter.compatibility import DeviceProfile, ReleaseProfile, CompatibilityError, compare, require_verified_compatibility


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.phone = DeviceProfile('brax3', 'rev-a', '0x1209', 'EMMC', 256, 'layout-a', 3, 8)
        self.release = ReleaseProfile('brax3', ('rev-a',), '0x1209', 'EMMC', (256,), ('layout-a',), 3, 8, ('download',))

    def test_matching_verified_profiles(self):
        self.assertTrue(compare(self.phone, self.release, 'download')['compatible'])

    def test_same_chip_different_model_is_rejected(self):
        with self.assertRaisesRegex(CompatibilityError, 'different phone model'):
            compare(replace(self.phone, model='brax4'), self.release, 'download')

    def test_revision_storage_layout_and_versions_are_enforced(self):
        for change in ({'board_revision':'rev-b'}, {'storage':'UFS'}, {'storage_bytes':128},
                       {'layout_id':'layout-b'}, {'bootloader_version':2}, {'rollback_index':9},
                       {'rollback_index':None}, {'board_revision':''}):
            with self.subTest(change=change), self.assertRaises(CompatibilityError):
                compare(replace(self.phone, **change), self.release, 'download')

    def test_format_is_not_an_escape_from_compatibility(self):
        for mode in ('format', 'format-download', 'firmware-upgrade'):
            with self.subTest(mode=mode), self.assertRaises(CompatibilityError):
                compare(self.phone, self.release, mode)

    def test_chip_or_self_claimed_verification_cannot_approve_live_operation(self):
        for mode in ('download','format','format-download','firmware-upgrade'):
            with self.subTest(mode=mode), self.assertRaisesRegex(CompatibilityError, 'Flashing blocked'):
                require_verified_compatibility({'hw_code':'0x1209','model':'brax3','verified':True}, 'flash.xml', mode)

    def test_vendor_prepare_uses_the_real_gate(self):
        import tempfile
        from pathlib import Path
        from brax_snwriter.flash_backend import prepare
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory)/'SPFlashToolV6.exe'
            tool.touch()
            device = {'hw_code':'0x1209','port':'COM5','usb_location':'1-1','handshake_verified':True}
            for mode in ('download','format','format-download','firmware-upgrade'):
                with self.subTest(mode=mode), self.assertRaisesRegex(CompatibilityError, 'Flashing blocked'):
                    prepare(tool, Path(directory)/'flash.xml', mode, device)
