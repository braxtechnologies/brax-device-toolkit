from pathlib import Path
import tempfile
import unittest

from brax_snwriter.da_protocol import Agent, AgentError
from brax_snwriter.partition_download import write_named_partition
from tests.test_native_flash import Link, frame, message


class PartitionDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.scatter = Path(self.temp.name)/'scatter.xml'
        self.image = Path(self.temp.name)/'image.bin'
        self.scatter.write_bytes(b'<scatter/>')
        self.image.write_bytes(b'x'*512)

    def tearDown(self):
        self.temp.cleanup()

    def transfer(self,path):
        return (frame(message('DOWNLOAD-FILE',{'source_file':str(path),'checksum':'CHK_NO','packet_length':'0x1000'}))
                + frame('OK') + frame('OK') + frame('OK'))

    def test_named_files_and_missing_optional_signature(self):
        end = b'<host><command>CMD:END</command><arg><result>OK</result></arg></host>'
        wire = (frame('OK') + self.transfer(self.scatter)
                + frame(message('FILE-SYS-OPERATION',{'key':'EXISTS','file_path':str(self.image)+'.sig'}))
                + self.transfer(self.image) + frame(end) + frame(message('START')))
        link = Link(wire)
        write_named_partition(Agent(link),self.scatter,'connsys_bt_a',self.image)
        self.assertIn(b'OK@NOT-EXISTS\0',link.writes)
        self.assertIn(b'x'*512,link.writes)
        self.assertTrue(any(b'<pt name="connsys_bt_a">' in p for p in link.writes))

    def test_unreviewed_file_is_not_served(self):
        wire = frame('OK')+self.transfer(Path('C:/private-file'))
        link = Link(wire)
        agent = Agent(link)
        with self.assertRaises(AgentError):
            write_named_partition(agent,self.scatter,'boot_a',self.image)
        self.assertNotIn(b'x'*512,link.writes)
        self.assertTrue(agent.failed)

    def test_denial_is_not_success_or_retry(self):
        end = b'<host><command>CMD:END</command><arg><result>ERR</result><message>Security deny</message></arg></host>'
        link = Link(frame('OK')+frame(end))
        with self.assertRaises(AgentError):
            write_named_partition(Agent(link),self.scatter,'boot_a',self.image)
        self.assertEqual(sum(b'CMD:WRITE-PARTITIONS' in p for p in link.writes),1)

    def test_download_deadlines_are_bounded_and_restored_on_failure(self):
        from unittest.mock import patch
        link = Link(b'')
        link.write_timeout = 10
        agent = Agent(link, timeout=30)
        def failing(*args):
            self.assertEqual(agent.timeout,120)
            self.assertEqual(link.write_timeout,120)
            raise OSError('transport stopped')
        with patch('brax_snwriter.partition_download._write_named_partition',side_effect=failing) as operation:
            with self.assertRaises(OSError):
                write_named_partition(agent,self.scatter,'super',self.image)
            operation.assert_called_once()
        self.assertEqual(agent.timeout,30)
        self.assertEqual(link.write_timeout,10)
