import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from brax_snwriter.sparse_image import inspect_image, pieces, read_piece, MAGIC
from brax_snwriter.native_install import install


def sparse(chunks, blocks, checksum=0):
    return struct.pack('<I4H4I',MAGIC,1,0,28,12,512,blocks,len(chunks),checksum)+b''.join(chunks)


def chunk(kind, blocks, data=b''):
    return struct.pack('<2H2I',kind,0,blocks,12+len(data))+data


class SparseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'image'

    def tearDown(self):
        self.temp.cleanup()

    def test_raw_fill_and_holes_have_correct_offsets(self):
        self.path.write_bytes(sparse([chunk(0xcac1,1,b'a'*512),chunk(0xcac3,3),
                                     chunk(0xcac2,2,b'ABCD')],6))
        info = inspect_image(self.path,3072)
        output = bytearray(b'z'*3072)
        for extent,start,offset,size in pieces(info,512):
            output[offset:offset+size] = read_piece(self.path,extent,start,size)
        self.assertEqual(output,b'a'*512+b'z'*1536+b'ABCD'*256)

    def test_malformed_images_fail_before_output(self):
        good = sparse([chunk(0xcac1,1,b'x'*512)],1)
        for data in [good[:-1],good+b'junk',sparse([chunk(0xcac3,2)],1),
                     sparse([chunk(0xcac2,1,b'x')],1),sparse([chunk(0xcac1,1,b'x'*512)],2),
                     sparse([chunk(0xcac4,0,b'\0'*4)],1),
                     sparse([chunk(0xcac1,1,b'x'*512)],1,checksum=1)]:
            self.path.write_bytes(data)
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                inspect_image(self.path,4096)

    def test_expanded_capacity_is_checked(self):
        self.path.write_bytes(sparse([chunk(0xcac2,100,b'abcd')],100))
        with self.assertRaises(ValueError):
            inspect_image(self.path,4096)

    def test_sparse_headers_do_not_count_toward_partition_size(self):
        self.path.write_bytes(sparse([chunk(0xcac1,1,b'x'*512)],1))
        self.assertEqual(inspect_image(self.path,512)['size'],512)

    def test_raw_padding_only_touches_last_sector(self):
        self.path.write_bytes(b'a'*513)
        info = inspect_image(self.path,1024)
        extent = info['extents'][0]
        self.assertEqual(read_piece(self.path,extent,512,512),b'a'+bytes(511))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root/'image'
        self.image.write_bytes(b'x'*512)
        self.device = {'cid':'test'}
        self.plan = {'expected_device':self.device,'write_bytes':512,'package_sha256':'test',
                     'scatter':str(self.image),'scatter_sha256':hashlib.sha256(b'x'*512).hexdigest(),
                     'images':[{'name':'boot_a','path':str(self.image),'offset':4096,
                                'sha256':hashlib.sha256(b'x'*512).hexdigest(),
                                **inspect_image(self.image,512)}]}
        self.agent = Mock()
        self.patcher = patch('brax_snwriter.native_install.write_named_partition')
        self.named = self.patcher.start()
        self.agent.download.side_effect = [b'o'*512,b'x'*512]

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_backup_precedes_write_and_readback_is_required(self):
        def writing(*args,**kwargs):
            self.assertEqual((self.root/'backup/00000.bin').read_bytes(),b'o'*512)
            self.assertTrue((self.root/'backup-index.json').exists())
        self.named.side_effect = writing
        result = install(self.agent,self.device,self.plan,self.root,event=lambda s:None)
        self.assertTrue(result['readback_verified'])
        self.assertFalse(result['boot_verified'])

    def test_identity_mismatch_sends_nothing(self):
        with self.assertRaises(ValueError):
            install(self.agent,{'cid':'wrong'},self.plan,self.root)
        self.assertFalse(self.agent.mock_calls)

    def test_changed_input_sends_nothing(self):
        self.image.write_bytes(b'y'*512)
        with self.assertRaises(ValueError):
            install(self.agent,self.device,self.plan,self.root)
        self.assertFalse(self.agent.mock_calls)

    def test_write_failure_is_not_retried(self):
        self.named.side_effect = OSError('disconnect')
        with self.assertRaises(RuntimeError):
            install(self.agent,self.device,self.plan,self.root,event=lambda s:None)
        self.named.assert_called_once()
        self.assertFalse((self.root/'result.json').exists())
        self.assertEqual((self.root/'backup/00000.bin').read_bytes(),b'o'*512)

    def test_wrong_readback_is_failure(self):
        self.agent.download.side_effect = [b'o'*512,b'bad'*170+b'xx']
        with self.assertRaises(RuntimeError):
            install(self.agent,self.device,self.plan,self.root,event=lambda s:None)
        self.assertFalse((self.root/'result.json').exists())

