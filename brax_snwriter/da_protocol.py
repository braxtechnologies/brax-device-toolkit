"""Bounded MediaTek v6 XML Download Agent transport over an existing stream.

Implements protocol messages, not a vendor DLL wrapper. A failed transaction
invalidates the session; flash commands are never automatically retried.
"""
import struct
import time
import xml.etree.ElementTree as ET

MAGIC = 0xFEEEEEEF
MAX_PACKET = 16 * 1024 * 1024


class AgentError(RuntimeError):
    pass


def parse_xml(data):
    data = data.rstrip(b'\0')
    if len(data) > 2 * 1024 * 1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise AgentError('Unsupported agent XML')
    return ET.fromstring(data)


def command_xml(name, args=None, *, version='1.0', advanced=None):
    root = ET.Element('da')
    ET.SubElement(root, 'version').text = version
    ET.SubElement(root, 'command').text = 'CMD:' + name
    for section, fields in [('arg', args), ('adv', advanced)]:
        if fields is not None:
            node = ET.SubElement(root, section)
            for key, value in fields.items():
                ET.SubElement(node, key).text = str(value)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True, short_empty_elements=False)


class Agent:
    def __init__(self, stream, event=lambda message: None, timeout=30):
        self.stream, self.event, self.timeout = stream, event, timeout
        self.failed = False

    def read_exact(self, count):
        deadline = time.monotonic() + self.timeout
        data = bytearray()
        while len(data) < count:
            if time.monotonic() >= deadline:
                raise AgentError('Timed out reading agent response')
            part = self.stream.read(count - len(data))
            if len(part) > count - len(data):
                raise AgentError('Transport returned more bytes than requested')
            data.extend(part)
        return bytes(data)

    def send(self, data):
        if self.failed:
            raise AgentError('Session failed; reconnect before another operation')
        if isinstance(data, str):
            data = data.encode('utf-8') + b'\0'
        if not data or len(data) > MAX_PACKET:
            raise AgentError('Invalid outbound packet length')
        for part in (struct.pack('<III', MAGIC, 1, len(data)), data):
            if self.stream.write(part) != len(part):
                raise AgentError('Partial agent packet write')

    def receive(self):
        for _ in range(4096):
            magic, kind, count = struct.unpack('<III', self.read_exact(12))
            if magic != MAGIC or kind not in (1, 2) or not 0 < count <= MAX_PACKET:
                raise AgentError('Invalid agent frame header')
            data = self.read_exact(count)
            if kind == 1:
                return data
            if len(data) < 4:
                raise AgentError('Short agent log packet')
            self.event(data[4:].rstrip(b'\0').decode('utf-8', errors='replace'))
        raise AgentError('Too many agent log packets without a response')

    def text(self):
        return self.receive().rstrip(b'\0').decode('utf-8')

    def ack(self, value=None):
        self.send('OK\0' if value is None else f'OK@{hex(value)}\0')

    def expect_ok(self):
        reply = self.text()
        if reply != 'OK':
            raise AgentError('Agent rejected operation: ' + reply[:300])

    def raw_status(self, *, final=False):
        reply = self.text()
        if reply in ('OK', 'OK@0x0'):
            return
        if final and reply.startswith('<?xml'):
            root = parse_xml(reply.encode())
            result = root.findtext('result') or root.findtext('arg/result')
            if root.findtext('command') == 'CMD:END' and result == 'OK':
                return
        raise AgentError('Raw transfer rejected: ' + reply[:300])

    def message(self):
        root = parse_xml(self.receive())
        name = root.findtext('command', '')
        if name == 'CMD:PROGRESS-REPORT':
            self.event(root.findtext('arg/message', 'Agent progress'))
            self.ack()
            for _ in range(4096):
                status = self.text()
                self.ack()
                if status == 'OK!EOT':
                    return self.message()
                self.event(status)
            raise AgentError('Agent progress did not terminate')
        if name in ('CMD:START', 'CMD:DOWNLOAD-FILE', 'CMD:UPLOAD-FILE', 'CMD:FILE-SYS-OPERATION'):
            self.ack()
        if not name:
            raise AgentError('Missing agent command')
        return name, root

    def expect_message(self, expected):
        name, root = self.message()
        if name != 'CMD:' + expected:
            raise AgentError(f'Expected {expected}, received {name}: {ET.tostring(root, encoding="unicode")[:300]}')
        return root

    def finish(self, *, expect_start=True):
        root = self.expect_message('END')
        result = root.findtext('result') or root.findtext('arg/result')
        if result != 'OK':
            raise AgentError('Agent transaction failed: ' + ET.tostring(root, encoding='unicode')[:500])
        self.ack()
        if expect_start:
            self.expect_message('START')

    def reboot(self):
        """Accept the reboot result; Android boot must be verified separately."""
        try:
            self.begin('REBOOT', {'action': 'IMMEDIATE'})
            self.finish(expect_start=False)
        finally:
            self.failed = True  # The device is leaving this protocol session.

    def begin(self, name, args=None, **kwargs):
        self.event('Command: ' + name)
        self.send(command_xml(name, args, **kwargs) + b'\0')
        self.expect_ok()

    def simple(self, name, args=None, **kwargs):
        try:
            self.begin(name, args, **kwargs)
            self.finish()
        except Exception:
            self.failed = True
            raise

    def download(self, name, args, *, maximum=2*1024*1024, raw=False, expected=None):
        """Receive data from the device. Host paths in replies are never opened."""
        try:
            self.begin(name, args)
            root = self.expect_message('UPLOAD-FILE')
            if root.findtext('arg/checksum') not in ('CHK_NO', None, ''):
                raise AgentError('Unsupported download checksum mode')
            size_reply = self.text()
            if not size_reply.startswith('OK@0x'):
                raise AgentError('Missing transfer size')
            count = int(size_reply[3:], 16)
            if not 0 < count <= maximum or (expected is not None and count != expected):
                raise AgentError('Device transfer size differs from requested bounds')
            self.ack()
            if raw:
                self.raw_status()
            else:
                self.expect_ok()
            self.ack()
            data = bytearray()
            while len(data) < count:
                part = self.receive()
                if len(part) > count-len(data):
                    raise AgentError('Device sent excess transfer data')
                data.extend(part)
                if raw:
                    self.ack()
                    self.raw_status(final=len(data) == count)
                    self.ack()
                self.event(f'Read {len(data)}/{count} bytes')
            if raw:
                # READ-FLASH completes with START after the final chunk ACK.
                self.expect_message('START')
            else:
                self.ack()
                self.finish()
            return bytes(data)
        except Exception:
            self.failed = True
            raise

    def upload(self, name, args, data, *, flash=False):
        try:
            self.begin(name, args)
            if flash:
                request = self.expect_message('FILE-SYS-OPERATION')
                if request.findtext('arg/key') != 'FILE-SIZE':
                    raise AgentError('Unsupported agent file operation')
                self.ack(len(data))
            request = self.expect_message('DOWNLOAD-FILE')
            if request.findtext('arg/checksum') != 'CHK_NO':
                raise AgentError('Unsupported upload checksum mode')
            packet = int(request.findtext('arg/packet_length', '0'), 0)
            if not 0 < packet <= MAX_PACKET:
                raise AgentError('Invalid negotiated packet size')
            self.event(f'Transfer packet size: {packet} bytes')
            self.ack(len(data))
            self.expect_ok()
            for start in range(0, len(data), packet):
                self.ack(0)
                self.expect_ok()
                self.send(data[start:start+packet])
                if flash:
                    self.raw_status()
                else:
                    self.expect_ok()
                self.event(f'Wrote {min(start+packet,len(data))}/{len(data)} bytes')
            self.finish()
        except Exception:
            self.failed = True
            raise
