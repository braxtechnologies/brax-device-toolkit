"""Experimental, hash-pinned BaseOS 1.0 installation through the native DA.

This implements an explicitly selected lab installation, not the consumer
catalog compatibility policy. Preloader boot regions and factory records are
preserved. The stock agent may erase entire selected partitions. Image-range
backups are not complete phone backups or a guarantee of full restoration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile
import xml.etree.ElementTree as ET

from .flash_plan import review_package
from .native_flash import connect, read_range, save_exclusive, stock_agent
from .sparse_image import inspect_image, pieces, read_piece
from .partition_download import write_named_partition

PACKAGE_SHA256 = '5d5ac5987f3d96e4f0472c71e478899e531055b0a4183095370a4bba31965e4a'
PARTITIONS = {'vbmeta_a', 'vbmeta_system_a', 'vbmeta_vendor_a', 'md1img_a',
              'spmfw_a', 'mcf_ota_a', 'pi_img_a', 'dpm_a', 'scp_a', 'vcp_a',
              'sspm_a', 'mcupm_a', 'gz_a', 'lk_a', 'boot_a', 'vendor_boot_a',
              'init_boot_a', 'dtbo_a', 'tee_a', 'connsys_bt_a', 'logo_a',
              'super', 'userdata'}


def file_hash(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def prepare(package, expected, directory):
    """Inspect every image before connecting; only this reviewed archive is accepted."""
    package, directory = Path(package), Path(directory)
    if file_hash(package) != PACKAGE_SHA256:
        raise ValueError('Package is not the reviewed BaseOS 1.0 SP Flash archive')
    scatter = review_package(package, 'EMMC')
    if expected.get('storage') != 'EMMC' or not expected.get('cid'):
        raise ValueError('A recorded target storage identity is required')
    targets = [p for p in scatter['partitions'] if p['region'] == 'EMMC_USER']
    if {p['partition'] for p in targets} != PARTITIONS or len(targets) != len(PARTITIONS):
        raise ValueError('Package partition selection differs from reviewed profile')
    directory.mkdir(parents=True, exist_ok=False)
    images = directory/'images'
    images.mkdir()
    plan = []
    with zipfile.ZipFile(package) as archive:
        scatter_name = next(n for n in archive.namelist() if n.endswith('_Android_scatter.xml'))
        base = str(Path(scatter_name).parent).replace('\\','/')
        scatter_xml = archive.read(scatter_name)
        for target in targets:
            name = target['partition']
            live = expected['partitions'].get(name)
            if not live or live['offset'] != target['offset']:
                raise ValueError('Partition offset mismatch: '+name)
            # The shipping scatter uses a placeholder userdata capacity.
            if name != 'userdata' and live['size'] != target['size']:
                raise ValueError('Partition capacity mismatch: '+name)
            path = images/target['image']
            with archive.open(base+'/'+target['image']) as source, path.open('xb') as dest:
                shutil.copyfileobj(source,dest,1024*1024)
            info = inspect_image(path, live['size'])
            plan.append({'name':name, 'path':str(path), 'sha256':file_hash(path),
                         'offset':live['offset'], **info})
        agent = directory/'DA_BR.bin'
        save_exclusive(agent,archive.read(base+'/download_agent/DA_BR.bin'))
    stock_agent(agent)
    root = ET.fromstring(scatter_xml)
    for group in root.findall('storage_type'):
        for node in group.findall('partition_index'):
            name = node.findtext('partition_name')
            selected = group.get('name') == 'EMMC' and name in PARTITIONS
            node.find('is_download').text = 'true' if selected else 'false'
    scatter_path = directory/'MT6835_Android_scatter.xml'
    save_exclusive(scatter_path,ET.tostring(root,encoding='utf-8',xml_declaration=True))
    plan.sort(key=lambda p: (2 if p['name'].startswith('vbmeta') else
                            1 if p['name'] in ('boot_a','init_boot_a','vendor_boot_a') else 0,
                            p['name']))
    total = sum(e['size'] for p in plan for e in p['extents'])
    if shutil.disk_usage(directory).free < total+1024**3:
        raise ValueError('Insufficient space for all overwritten-range backups')
    review = {'package_sha256':PACKAGE_SHA256,'expected_device':expected,'images':plan,
              'scatter':str(scatter_path),'scatter_sha256':file_hash(scatter_path),
              'write_bytes':total,'resets_user_data':True,'preserves_preloader':True,
              'requires_recovery_factory_reset':True,
              'backup_scope':'image-data ranges only; not a full phone backup',
              'sparse_holes':'not compared; agent may erase selected partitions'}
    save_exclusive(directory/'plan.json',json.dumps(review,indent=2).encode())
    return review, agent


def install(agent, device, plan, directory, event=print):
    directory = Path(directory)
    if device != plan['expected_device']:
        raise ValueError('Connected storage identity or layout differs from reviewed phone')
    if any(image['name'] not in PARTITIONS for image in plan['images']):
        raise ValueError('Plan contains an unsupported image; regenerate it from the official package. Metadata must be reset through recovery.')
    for image in plan['images']:
        if image['path'] and file_hash(image['path']) != image['sha256']:
            raise ValueError('Prepared image changed: '+image['name'])
    if file_hash(plan['scatter']) != plan['scatter_sha256']:
        raise ValueError('Prepared scatter changed')
    backup = directory/'backup'
    backup.mkdir(exist_ok=False)
    records = []
    total = plan['write_bytes']
    done = 0
    # Save image-data ranges before writes. The agent may erase entire selected
    # partitions, so these records alone cannot guarantee complete restoration.
    for image in plan['images']:
        for extent,start,offset,size in pieces(image):
            absolute = image['offset']+offset
            before = read_range(agent,absolute,size)
            name = f'{len(records):05d}.bin'
            save_exclusive(backup/name,before)
            records.append({'image':image['name'],'offset':absolute,'size':size,'backup':name,
                            'before_sha256':hashlib.sha256(before).hexdigest()})
            done += size
            event(f'Backup {done}/{total} bytes')
    save_exclusive(directory/'backup-index.json',json.dumps(records,indent=2).encode())
    # Recheck files after backup: no changed input can initiate a write.
    for image in plan['images']:
        if image['path'] and file_hash(image['path']) != image['sha256']:
            raise ValueError('Prepared image changed during backup')
    done = 0
    index = 0
    save_exclusive(directory/'write-started.json',b'{"outcome":"pending readback"}')
    try:
        with (directory/'verified-ranges.jsonl').open('x',encoding='utf-8') as log:
            for image in plan['images']:
                write_named_partition(agent,plan['scatter'],image['name'],image['path'])
                for extent,start,offset,size in pieces(image):
                    payload = read_piece(image['path'],extent,start,size)
                    absolute = image['offset']+offset
                    actual = read_range(agent,absolute,size)
                    compared = min(size,max(0,extent['file_size']-start)) if extent['kind']=='raw' else size
                    if actual[:compared] != payload[:compared]:
                        raise ValueError('Readback mismatch: '+image['name'])
                    log.write(json.dumps({**records[index], 'written_sha256':hashlib.sha256(payload).hexdigest()})+'\n')
                    log.flush()
                    os.fsync(log.fileno())
                    index += 1
                    done += size
                    event(f'Installed and verified {image["name"]}: {done}/{total} bytes ({100*done/total:.1f}%)')
        result = {'written_bytes':done,'verified_ranges':index,'readback_verified':True,
                  'boot_verified':False,'package_sha256':plan['package_sha256'],
                  'requires_recovery_factory_reset':True,'recovery_reset_verified':False}
        save_exclusive(directory/'result.json',json.dumps(result,indent=2).encode())
        return result
    except Exception as exc:
        save_exclusive(directory/'error.txt',str(exc).encode())
        raise RuntimeError('Installation stopped; retain backups and inspect recovery before any retry: '+str(directory)) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--expected-device',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--usb-location',required=True)
    parser.add_argument('--install-and-reset-userdata',action='store_true')
    args = parser.parse_args()
    expected = json.loads(args.expected_device.read_text(encoding='utf-8-sig'))
    plan,agent_path = prepare(args.package,expected,args.output)
    print(f'Reviewed {len(plan["images"])} images; {plan["write_bytes"]} bytes; userdata reset; preloader preserved.',flush=True)
    if not args.install_and_reset_userdata:
        return
    result = connect(agent_path,args.usb_location,
                     lambda agent,device:install(agent,device,plan,args.output,lambda s:print(s,flush=True)),
                     event=lambda s:print(s,flush=True),timeout=600)
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    main()
