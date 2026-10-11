import base64,json,unittest
from brax_snwriter import firmware_catalog as f

def page(rows):
 return b'<template id="index-data">'+base64.b64encode(json.dumps({'kind':'Index','dir_exists':True,'paths':rows}).encode())+b'</template>'
class CatalogTests(unittest.TestCase):
 def test_reject_foreign_and_cleartext_urls(self):
  for url in ['http://download.os-source.co/brax3/','https://evil.test/brax3/a.zip','https://download.os-source.co/other/']:
   with self.assertRaises(ValueError):f.allowed_url(url)
 def test_reject_traversal_entry(self):
  with self.assertRaises(ValueError):f.parse_index(page([{'name':'../evil','path_type':'File'}]))
 def test_catalog_classifies_packages_without_flash_authorization(self):
  rows=[{'name':name,'path_type':'File','size':100} for name in ['baseos-1-brax3-spflash.zip','baseos-1-brax3-spflash.zip.sha256','baseos-1-brax3-spflash.zip.minisig','iode-7-brax3-ota.zip','ap-db.zip']]
  data=f.catalog(lambda _:page(rows));self.assertEqual(len(data),2)
  p=next(p for p in data if p['format']=='spflash');self.assertTrue(p['sha256_url']);self.assertTrue(p['signature_available'])
 def test_invalid_html_fails_closed(self):
  with self.assertRaises(ValueError):f.parse_index(b'<h1>Unavailable</h1>')

class DownloadTests(unittest.TestCase):
 def test_mismatch_never_publishes_package(self):
  import io,tempfile
  from pathlib import Path
  from unittest.mock import patch
  package={'name':'os-brax3-spflash.zip','url':f.ROOT+'os-brax3-spflash.zip','sha256_url':f.ROOT+'os-brax3-spflash.zip.sha256','size':3}
  with tempfile.TemporaryDirectory() as tmp, patch.object(f,'fetch',return_value=('0'*64+'  '+package['name']).encode()), patch.object(f,'open_url',return_value=io.BytesIO(b'bad')):
   with self.assertRaises(ValueError):f.download(package,tmp)
   self.assertFalse((Path(tmp)/package['name']).exists())
 def test_matching_hash_is_not_signature_or_flash_approval(self):
  import io,tempfile,hashlib
  from unittest.mock import patch
  package={'name':'os-brax3-spflash.zip','url':f.ROOT+'os-brax3-spflash.zip','sha256_url':f.ROOT+'os-brax3-spflash.zip.sha256','size':3}
  with tempfile.TemporaryDirectory() as tmp, patch.object(f,'fetch',return_value=(hashlib.sha256(b'abc').hexdigest()+'  '+package['name']).encode()), patch.object(f,'open_url',return_value=io.BytesIO(b'abc')):
   result=f.download(package,tmp);self.assertFalse(result['publisher_signature_verified']);self.assertFalse(result['flash_ready'])
