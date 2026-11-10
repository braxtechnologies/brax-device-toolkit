"""Stock WRITE-PARTITIONS file-serving protocol; no vendor executable or DLL."""
from pathlib import Path
import xml.etree.ElementTree as ET

from .da_protocol import AgentError, MAX_PACKET, command_xml


def write_named_partition(agent, scatter, partition, image):
    scatter, image = Path(scatter), Path(image)
    files = {str(scatter):scatter, str(image):image}
    if scatter == image:
        raise ValueError('Scatter and image must be distinct files')
    root = ET.fromstring(command_xml('WRITE-PARTITIONS',{'source_file':str(scatter)}))
    listing = ET.SubElement(root.find('arg'),'flash_list')
    ET.SubElement(listing,'pt',{'name':partition}).text = str(image)
    try:
        agent.event('Named partition download: '+partition)
        agent.send(ET.tostring(root,encoding='utf-8',xml_declaration=True)+b'\0')
        agent.expect_ok()
        sent_image = False
        for _ in range(32):
            name, request = agent.message()
            if name == 'CMD:END':
                result = request.findtext('arg/result') or request.findtext('result')
                agent.ack()
                agent.expect_message('START')
                if result != 'OK' or not sent_image:
                    raise AgentError('Named download failed: '+ET.tostring(request,encoding='unicode'))
                return
            if name == 'CMD:FILE-SYS-OPERATION':
                path = request.findtext('arg/file_path')
                if request.findtext('arg/key') == 'EXISTS':
                    # Report only the reviewed virtual file set, never inspect
                    # arbitrary host paths requested by the phone.
                    agent.send('OK@EXISTS' if path in files else 'OK@NOT-EXISTS')
                    continue
                if request.findtext('arg/key') != 'FILE-SIZE' or path not in files:
                    raise AgentError('Unexpected file-system request; no external paths opened')
                agent.ack(files[path].stat().st_size)
                continue
            if name != 'CMD:DOWNLOAD-FILE':
                raise AgentError('Unexpected named-download command: '+name)
            path = request.findtext('arg/source_file')
            if path not in files or request.findtext('arg/checksum') != 'CHK_NO':
                raise AgentError('Unreviewed download file or checksum mode')
            packet = int(request.findtext('arg/packet_length','0'),0)
            if not 0 < packet <= MAX_PACKET:
                raise AgentError('Invalid file packet size')
            size = files[path].stat().st_size
            if not 0 < size <= 8*1024**3:
                raise AgentError('Invalid reviewed file size')
            agent.ack(size)
            agent.expect_ok()
            with files[path].open('rb') as source:
                done = 0
                while done < size:
                    payload = source.read(min(packet,size-done))
                    if not payload:
                        raise AgentError('Reviewed file became truncated')
                    agent.ack(0)
                    agent.expect_ok()
                    agent.send(payload)
                    agent.raw_status()
                    done += len(payload)
                    agent.event(f'Transferred {files[path].name}: {done}/{size} bytes ({100*done/size:.1f}%)')
            if files[path] == image:
                sent_image = True
        raise AgentError('Too many named-download callbacks')
    except Exception:
        agent.failed = True
        raise
