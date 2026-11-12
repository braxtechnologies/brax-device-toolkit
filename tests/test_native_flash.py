import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock

from brax_snwriter.da_protocol import Agent, AgentError, MAGIC, command_xml
from brax_snwriter.native_flash import authorization_disabled, inventory, stock_agent, write_partition


def frame(data, kind=1):
    if isinstance(data, str):
        data = data.encode()+b'\0'
    return struct.pack('<III', MAGIC, kind, len(data))+data


def message(name, fields=None):
    return command_xml(name, fields)


class Link:
    def __init__(self, data=b'', fragment=7):
        self.input = io.BytesIO(data)
        self.writes = []
        self.fragment = fragment

    def read(self, size):
        return self.input.read(min(size, self.fragment))

    def write(self, data):
        self.writes.append(data)
        return len(data)


class AgentTransportTests(unittest.TestCase):
    def test_flash_upload_zero_status_and_completion(self):
        end = b'<host><command>CMD:END</command><arg><result>OK</result></arg></host>'
        wire = (frame('OK') + frame(message('FILE-SYS-OPERATION', {'key':'FILE-SIZE'}))
                + frame(message('DOWNLOAD-FILE', {'checksum':'CHK_NO','packet_length':'0x200'}))
                + frame('OK') + frame('OK') + frame('OK@0x0')
                + frame('OK') + frame('OK@0x0') + frame(end) + frame(message('START')))
        link = Link(wire)
        Agent(link).upload('WRITE-FLASH', {}, b'x'*1024, flash=True)
        self.assertEqual(sum(p == b'x'*512 for p in link.writes), 2)
        last_data = max(i for i, packet in enumerate(link.writes) if packet == b'x'*512)
        # Only END and START are acknowledged after the last data status.
        # An extra ACK desynchronizes the next command on the stock BraX3 agent.
        self.assertEqual(link.writes[last_data+1:].count(b'OK\0'), 2)

    def test_raw_read_status_and_final_end(self):
        end = b'<?xml version="1.0"?><host><command>CMD:END</command><arg><result>OK</result></arg></host>'
        wire = (frame('OK') + frame(message('UPLOAD-FILE', {'checksum':'CHK_NO'}))
                + frame('OK@0x200') + frame('OK@0x0') + frame(b'x'*512)
                + frame(end) + frame(message('START')))
        self.assertEqual(Agent(Link(wire)).download('READ-FLASH', {}, raw=True, expected=512), b'x'*512)

    def test_raw_read_nonzero_status_stops(self):
        agent = Agent(Link(frame('OK@0x1')))
        with self.assertRaises(AgentError):
            agent.raw_status()

    def test_reboot_does_not_wait_for_new_session(self):
        end = b'<host><command>CMD:END</command><result>OK</result></host>'
        agent = Agent(Link(frame('OK')+frame(end)))
        agent.reboot()
        self.assertTrue(agent.failed)

    def test_authorization_property_is_explicit(self):
        self.assertTrue(authorization_disabled(b'<sys_prop version="1.0"><item key="DA.SLA">DISABLED</item></sys_prop>'))
        for value in (b'<error>DISABLED</error>',
                      b'<sys_prop><item key="DA.SLA">ENABLED</item></sys_prop>',
                      b'<sys_prop><item key="OTHER">DISABLED</item></sys_prop>',
                      b'<sys_prop><item key="DA.SLA">DISABLED</item><item key="DA.SLA">ENABLED</item></sys_prop>'):
            self.assertFalse(authorization_disabled(value))

    def test_fragmented_frames_and_log_payload(self):
        logs = []
        agent = Agent(Link(frame(struct.pack('<I', 2)+b'hello\0', 2)+frame('OK')), logs.append)
        self.assertEqual(agent.text(), 'OK')
        self.assertEqual(logs, ['hello'])

    def test_invalid_frame_stops_before_payload(self):
        for header in [(0, 1, 3), (MAGIC, 9, 3), (MAGIC, 1, 0xffffffff)]:
            with self.subTest(header=header), self.assertRaises(AgentError):
                Agent(Link(struct.pack('<III', *header))).receive()

    def test_command_completion_requires_end_and_start(self):
        end = b'<host><command>CMD:END</command><result>OK</result></host>'
        link = Link(frame('OK')+frame(end)+frame(message('START')))
        Agent(link).simple('NOTIFY-INIT-HW')
        self.assertTrue(any(b'CMD:NOTIFY-INIT-HW' in p for p in link.writes))

    def test_rejection_poisons_session_and_cannot_retry(self):
        link = Link(frame('ERR!AUTHENTICATION'))
        agent = Agent(link)
        with self.assertRaises(AgentError):
            agent.simple('NOTIFY-INIT-HW')
        written = len(link.writes)
        with self.assertRaises(AgentError):
            agent.simple('NOTIFY-INIT-HW')
        self.assertEqual(len(link.writes), written)

    def test_download_rejects_oversized_device_length(self):
        agent = Agent(Link(frame('OK')+frame(message('UPLOAD-FILE', {'checksum':'CHK_NO'}))+frame('OK@0x2000')))
        with self.assertRaisesRegex(AgentError, 'bounds'):
            agent.download('GET-HW-INFO', {}, maximum=4096)
        self.assertTrue(agent.failed)

    def test_partial_upload_does_not_retry(self):
        link = Link()
        link.write = Mock(return_value=1)
        agent = Agent(link)
        with self.assertRaisesRegex(AgentError, 'Partial'):
            agent.upload('WRITE-FLASH', {}, b'x'*512, flash=True)
        self.assertEqual(link.write.call_count, 1)
        self.assertTrue(agent.failed)

    def test_xml_fields_are_escaped(self):
        self.assertIn(b'&lt;unsafe&gt;', command_xml('SET-HOST-INFO', {'info':'<unsafe>'}))


class NativeWriteTests(unittest.TestCase):
    def setUp(self):
        self.device = {'cid':'test', 'partitions':{'boot_b':{'offset':4096, 'size':1024}}}
        self.data = b'a'*1024
        self.agent = Mock()
        self.agent.download.return_value = self.data
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)/'transaction'

    def tearDown(self):
        self.temp.cleanup()

    def test_same_byte_write_backs_up_before_transfer_and_checks_readback(self):
        def check(*args, **kwargs):
            self.assertEqual((self.output/'before.img').read_bytes(), self.data)
            self.assertTrue((self.output/'write-started.json').exists())
        self.agent.upload.side_effect = check
        result = write_partition(self.agent, self.device, 'boot_b', None, self.output, rewrite_existing=True)
        self.assertTrue(result['readback_verified'])
        self.assertFalse(result['reboot_verified'])
        self.agent.upload.assert_called_once()
        self.assertEqual(self.agent.download.call_count, 3)

    def test_factory_and_oversized_images_refused_before_transport(self):
        for name, data in [('nvram', b'x'*512), ('boot_b', b'x'*2048), ('boot_b', b'x'*513),
                           ('boot_b', b'\x3a\xff\x26\xed'+b'x'*508)]:
            with self.subTest(name=name, size=len(data)), self.assertRaises(ValueError):
                write_partition(self.agent, self.device, name, data, self.output)
        self.assertFalse(self.agent.mock_calls)

    def test_changed_partition_stops_before_write(self):
        self.agent.download.side_effect = [self.data, b'b'*1024]
        with self.assertRaisesRegex(ValueError, 'changed'):
            write_partition(self.agent, self.device, 'boot_b', b'x'*512, self.output)
        self.agent.upload.assert_not_called()

    def test_write_failure_records_unknown_outcome_and_keeps_backup(self):
        self.agent.upload.side_effect = AgentError('Disconnected')
        with self.assertRaisesRegex(AgentError, 'recovery review'):
            write_partition(self.agent, self.device, 'boot_b', b'x'*512, self.output)
        self.assertEqual((self.output/'before.img').read_bytes(), self.data)
        self.assertTrue((self.output/'error.txt').exists())
        self.assertFalse((self.output/'result.json').exists())
        self.agent.upload.assert_called_once()

    def test_short_image_preserves_partition_tail(self):
        self.agent.download.side_effect = [self.data,self.data,b'x'*512+b'a'*512]
        result = write_partition(self.agent, self.device, 'boot_b', b'x'*512, self.output)
        self.assertTrue(result['readback_verified'])
        self.assertEqual(json.loads((self.output/'intent.json').read_text())['image_bytes'],512)

    def test_unknown_agent_is_rejected_before_connection(self):
        source = Path(self.temp.name)/'DA.bin'
        source.write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'not been inspected'):
            stock_agent(source)

    def test_overlapping_partition_table_rejected(self):
        self.agent.download.side_effect = [
            b'<da_hw_info><storage>EMMC</storage><emmc><user_size>0x100000</user_size></emmc></da_hw_info>',
            b'<partition_table><pt><name>boot_a</name><start>0x1000</start><size>0x1000</size></pt>'
            b'<pt><name>boot_b</name><start>0x1200</start><size>0x1000</size></pt></partition_table>']
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            inventory(self.agent)
