import unittest
from brax_snwriter.flash_plan import review_scatter
class PlanTests(unittest.TestCase):
 def data(self,name='boot',image='boot.img'):
  return f'<root><general><config_version><platform>MT6835</platform></config_version></general><storage_type name="EMMC"><partition_index><partition_name>{name}</partition_name><file_name>{image}</file_name><is_download>true</is_download><partition_size>0x1000</partition_size><physical_start_addr>0x0</physical_start_addr></partition_index></storage_type></root>'.encode()
 def test_userdata_erasure_visible(self):
  p=review_scatter(self.data('userdata','userdata.img'),'EMMC');self.assertTrue(p['partitions'][0]['erases_user_data']);self.assertFalse(p['flash_ready'])
 def test_identity_partition_rejected(self):
  with self.assertRaises(ValueError):review_scatter(self.data('nvram'),'EMMC')
 def test_wrong_storage_rejected(self):
  with self.assertRaises(ValueError):review_scatter(self.data(),'UFS')
 def test_path_escape_rejected(self):
  with self.assertRaises(ValueError):review_scatter(self.data(image='../boot.img'),'EMMC')

 def test_package_requires_selected_images(self):
  import tempfile,zipfile
  from pathlib import Path
  from brax_snwriter.flash_plan import review_package
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'firmware.zip'
   with zipfile.ZipFile(path,'w') as z:z.writestr('release/MT6835_Android_scatter.xml',self.data())
   with self.assertRaises(ValueError):review_package(path,'EMMC')
 def test_archive_path_traversal_rejected(self):
  import tempfile,zipfile
  from pathlib import Path
  from brax_snwriter.flash_plan import review_package
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'firmware.zip'
   with zipfile.ZipFile(path,'w') as z:z.writestr('../escape','bad')
   with self.assertRaises(ValueError):review_package(path,'EMMC')

 def test_agent_must_belong_to_scatter_directory(self):
  import tempfile,zipfile
  from pathlib import Path
  from brax_snwriter.flash_plan import review_package
  for root,agent,present in [('', 'download_agent/DA_BR.bin', True),
                             ('release/', 'release/download_agent/DA_BR.bin', True),
                             ('release/', 'other/download_agent/DA_BR.bin', False)]:
   with self.subTest(root=root,agent=agent),tempfile.TemporaryDirectory() as t:
    path=Path(t)/'firmware.zip'
    with zipfile.ZipFile(path,'w') as z:
     z.writestr(root+'MT6835_Android_scatter.xml',self.data())
     z.writestr(root+'boot.img',b'image')
     z.writestr(agent,b'agent')
    self.assertEqual(review_package(path,'EMMC')['download_agent_present'],present)
