"""Experimental BraX3 v6 flashing without SP Flash Tool or its DLLs.

Only the inspected stock agent and eMMC user area are supported. No security
bypass, formatting, bootloader write or factory-record write is implemented.
"""
import argparse
import functools
import hashlib
import json
import operator
import os
from pathlib import Path
import re
import struct
import threading

from .boot_probe import open_boot_session, wait_and_probe
from .da_protocol import Agent, AgentError, parse_xml

AGENT_SHA256 = 'f8fc84afc9692e4ce3ea9ad5e2b0be2aaf9d83dc0627a5066ef1e03e897b55d3'
IMAGE_PARTITIONS = {'boot_a', 'boot_b', 'init_boot_a', 'init_boot_b',
                    'vendor_boot_a', 'vendor_boot_b', 'dtbo_a', 'dtbo_b',
                    'vbmeta_a', 'vbmeta_b', 'vbmeta_system_a', 'vbmeta_system_b',
                    'vbmeta_vendor_a', 'vbmeta_vendor_b'}
MAX_IMAGE = 128 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def authorization_disabled(policy):
    """Require the explicit DA.SLA property; error text is never authorization."""
    root = parse_xml(policy)
    items = root.findall('item')
    return (root.tag == 'sys_prop' and len(items) == 1
            and items[0].get('key') == 'DA.SLA'
            and items[0].text == 'DISABLED')


def stock_agent(path):
    data = Path(path).read_bytes()
    if digest(data) != AGENT_SHA256:
        raise ValueError('This Download Agent has not been inspected for the native BraX3 profile')
    if b'MTK_DA_v6' not in data[:104] or struct.unpack_from('<I', data, 104)[0] != 1:
        raise ValueError('Unsupported Download Agent container')
    entry = struct.unpack_from('<10H', data, 108)
    if entry[0:2] != (0xdada, 0x1209) or entry[9] != 3:
        raise ValueError('Download Agent target does not match MT6835')
    regions = []
    for index in (1, 2):
        offset, length, address, _, signature = struct.unpack_from('<5I', data, 128+20*index)
        if offset+length > len(data) or not 0 < signature < length or length % 2:
            raise ValueError('Malformed Download Agent region')
        regions.append((address, signature, data[offset:offset+length]))
    return regions


def start_agent(link, identity, regions, event):
    if identity['mode'] != 'Preloader' or identity['hw_code'] != '0x1209':
        raise ValueError('Native agent entry requires the supported MT6835 preloader')
    if any(identity[key] for key in ('secure_boot', 'serial_link_auth', 'download_agent_auth', 'root_certificate_required')):
        raise ValueError('This phone requires an authenticated loader path; no bypass will be attempted')
    agent = Agent(link, event)
    link.timeout, link.write_timeout = .5, 10
    def echo(data):
        if link.write(data) != len(data) or agent.read_exact(len(data)) != data:
            raise AgentError('Preloader command echo mismatch')
    address, signature, stage1 = regions[0]
    event('Loading unmodified stock Download Agent stage 1 into RAM')
    echo(b'\xd7')
    for value in (address, len(stage1), signature):
        echo(struct.pack('>I', value))
    status = struct.unpack('>H', agent.read_exact(2))[0]
    if status != 0:
        raise AgentError(f'Preloader refused Download Agent: {status:#x}')
    for start in range(0, len(stage1), 1024):
        chunk = stage1[start:start+1024]
        if link.write(chunk) != len(chunk):
            raise AgentError('Partial agent upload; reconnect the phone')
    checksum, status = struct.unpack('>HH', agent.read_exact(4))
    expected = functools.reduce(operator.xor, struct.unpack('<'+'H'*(len(stage1)//2), stage1), 0)
    if status != 0 or checksum != expected:
        raise AgentError(f'Agent upload verification failed: status={status:#x}, checksum={checksum:#x}')
    echo(b'\xd5')
    echo(struct.pack('>I', address))
    if agent.read_exact(2) != b'\0\0':
        raise AgentError('Preloader refused agent start')
    agent.expect_message('START')
    agent.simple('SET-RUNTIME-PARAMETER', dict(checksum_level='NONE', battery_exist='AUTO-DETECT',
                 da_log_level='INFO', log_channel='UART', system_os='WINDOWS'), version='1.1',
                 advanced={'initialize_dram': 'YES'})
    capabilities = 'CMD:DOWNLOAD-FILE^1@CMD:FILE-SYS-OPERATION^1@CMD:PROGRESS-REPORT^1@CMD:UPLOAD-FILE^1@'
    agent.simple('HOST-SUPPORTED-COMMANDS', {'host_capability': capabilities})
    agent.simple('NOTIFY-INIT-HW')
    agent.simple('SET-HOST-INFO', {'info': 'Brax Device Toolkit'})
    address, signature, stage2 = regions[1]
    # The container signature trailer is not part of BOOT-TO's executable body.
    body = stage2[:-signature]
    agent.upload('BOOT-TO', {'at_address': hex(address), 'jmp_address': hex(address),
                 'source_file': f'MEM://0x8000000:{hex(len(body))}'}, body)
    agent.simple('HOST-SUPPORTED-COMMANDS', {'host_capability': capabilities})
    agent.simple('NOTIFY-INIT-HW')
    policy = agent.download('GET-SYS-PROPERTY', {'key':'DA.SLA', 'target_file':'MEM://0x8000000:0x1000'}, maximum=4096)
    # Accept only the explicit disabled property, never a substring in an error.
    if not authorization_disabled(policy):
        raise AgentError('Download Agent requires authorization or returned an unknown SLA policy: '+repr(policy[:160]))
    return agent


def inventory(agent):
    hardware = parse_xml(agent.download('GET-HW-INFO', {'target_file':'MEM://0x8000000:0x200000'}))
    if hardware.findtext('storage') != 'EMMC':
        raise ValueError('Only eMMC is supported by this native profile')
    size = int(hardware.findtext('emmc/user_size', '0'), 0)
    if not 0 < size < 2**48:
        raise ValueError('Invalid eMMC capacity')
    table = parse_xml(agent.download('READ-PARTITION-TABLE', {'target_file':'MEM://0x8000000:0x200000'}))
    partitions = {}
    for entry in table.findall('pt'):
        name = entry.findtext('name', '')
        start, length = int(entry.findtext('start', '-1'), 0), int(entry.findtext('size', '0'), 0)
        if not re.fullmatch(r'[a-zA-Z0-9_]+', name) or name in partitions or start < 0 or length <= 0 or start+length > size:
            raise ValueError('Invalid or ambiguous partition table')
        if start % 512 or length % 512:
            raise ValueError('Unaligned eMMC partition')
        partitions[name] = {'offset':start, 'size':length}
    if not partitions:
        raise ValueError('Empty partition table')
    ordered = sorted(partitions.values(), key=lambda p:p['offset'])
    if any(a['offset']+a['size'] > b['offset'] for a,b in zip(ordered,ordered[1:])):
        raise ValueError('Overlapping partition table')
    return {'storage':'EMMC', 'bytes':size, 'partitions':partitions,
            'cid':hardware.findtext('emmc/id') or hardware.findtext('emmc/emmc_cid')}


def read_range(agent, offset, size):
    if offset < 0 or offset % 512 or not 0 < size <= MAX_IMAGE or size % 512:
        raise ValueError('Invalid bounded read range')
    return agent.download('READ-FLASH', {'partition':'EMMC-USER', 'offset':hex(offset),
                          'length':hex(size), 'target_file':'ROM_0'}, maximum=size, expected=size, raw=True)


def save_exclusive(path, data):
    with Path(path).open('xb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def write_partition(agent, device, name, image, directory, *, rewrite_existing=False):
    """Back up, write once and independently read back a bounded raw partition.

The caller must explicitly select this experimental operation. This is not the
production OS compatibility policy and does not enable the automatic OS guide.
"""
    if name not in IMAGE_PARTITIONS or name not in device['partitions']:
        raise ValueError('Only explicitly selected boot image partitions can be written')
    partition = device['partitions'][name]
    size, offset = partition['size'], partition['offset']
    if not 0 < size <= MAX_IMAGE:
        raise ValueError('Partition exceeds the native backup limit')
    if image is not None and (not image or len(image)>size or len(image)%512 or image[:4]==b'\x3a\xff\x26\xed'):
        raise ValueError('Select a sector-aligned raw image within the partition; sparse images are not supported')
    if image is None and not rewrite_existing:
        raise ValueError('No image or explicit same-byte test selected')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    before = read_range(agent, offset, size)
    save_exclusive(directory/'before.img', before)
    payload = before if rewrite_existing else image
    intent = {'partition':name, **partition, 'storage_cid':device['cid'],
              'backup_sha256':digest(before), 'image_sha256':digest(payload),
              'image_bytes':len(payload), 'same_byte_test':rewrite_existing}
    save_exclusive(directory/'intent.json', json.dumps(intent,indent=2).encode())
    if read_range(agent, offset, size) != before:
        raise ValueError('Partition changed since backup; no write started')
    save_exclusive(directory/'write-started.json', b'{"outcome":"unknown until readback"}')
    try:
        agent.upload('WRITE-FLASH', {'partition':'EMMC-USER', 'offset':hex(offset),
                     'source_file':f'MEM://0x8000000:{hex(len(payload))}'}, payload, flash=True)
        after = read_range(agent, offset, size)
        expected = payload + before[len(payload):]
        if after != expected:
            raise AgentError('Flash readback differs from the intended full partition')
        result = {**intent, 'readback_sha256':digest(after), 'readback_verified':True,
                  'reboot_verified':False}
        save_exclusive(directory/'result.json', json.dumps(result,indent=2).encode())
        return result
    except Exception as exc:
        save_exclusive(directory/'error.txt', str(exc).encode())
        raise AgentError('Write outcome requires recovery review; do not retry automatically. Backup: '+str(directory)) from exc


def connect(agent_path, usb_location, operation, event=print, timeout=120, cancel=None):
    regions = stock_agent(agent_path)
    if not re.fullmatch(r'\d+(?:-\d+)+', usb_location):
        raise ValueError('Specify the exact physical USB location for the test phone')
    def probe(port, stop):
        if (port.location or '').split(':')[0] != usb_location or port.pid != 0x2000:
            raise ValueError('A different boot device appeared; not opening it')
        def ready(link, identity):
            agent = start_agent(link, identity, regions, event)
            device = inventory(agent)
            event('Storage and partition table read successfully')
            result = operation(agent, device)
            agent.reboot()
            return result
        return open_boot_session(port, stop, on_ready=ready)
    # No retry after an attempted agent load or any flash command.
    class AttemptFailed(RuntimeError):
        pass
    def once(port, stop):
        try:
            return probe(port, stop)
        except Exception as exc:
            raise AttemptFailed(str(exc)) from exc
    return wait_and_probe(timeout=timeout, cancel=cancel or threading.Event(), probe=once,
                          event=lambda row:event(json.dumps(row)))


def main():
    parser = argparse.ArgumentParser(description='Experimental native BraX3 Download Agent flow')
    parser.add_argument('--agent', required=True)
    parser.add_argument('--usb-location', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=120)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--rewrite-existing', choices=sorted(IMAGE_PARTITIONS))
    action.add_argument('--partition', choices=sorted(IMAGE_PARTITIONS))
    parser.add_argument('--image', type=Path)
    args = parser.parse_args()
    if bool(args.partition) != bool(args.image):
        parser.error('--partition and --image must be supplied together')
    if args.image and not 0 < args.image.stat().st_size <= MAX_IMAGE:
        parser.error('Image exceeds the native raw-image size limit')
    image = args.image.read_bytes() if args.image else None
    def operation(agent, device):
        if args.rewrite_existing or args.partition:
            return write_partition(agent, device, args.rewrite_existing or args.partition,
                                   image, args.output, rewrite_existing=bool(args.rewrite_existing))
        args.output.mkdir(parents=True,exist_ok=False)
        save_exclusive(args.output/'inventory.json', json.dumps(device,indent=2).encode())
        return device
    result = connect(args.agent, args.usb_location, operation,
                     event=lambda message:print(message,flush=True), timeout=args.timeout)
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
