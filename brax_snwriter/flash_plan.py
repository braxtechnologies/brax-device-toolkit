"""Read-only MT6835 scatter review. This module never connects to a phone."""
import re
import xml.etree.ElementTree as ET
PROTECTED={'nvram','nvdata','persist','proinfo','protect1','protect2','protect_f','protect_s','seccfg','sec1','otp','efuse'}
def review_scatter(data, storage):
    if len(data)>1024*1024 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper(): raise ValueError('Unsupported scatter XML')
    root=ET.fromstring(data)
    if root.findtext('./general/config_version/platform')!='MT6835': raise ValueError('Scatter is not for MT6835')
    groups=[x for x in root.findall('storage_type') if x.get('name')==storage]
    if len(groups)!=1: raise ValueError('Storage type must match exactly one scatter section')
    result=[];seen=set()
    for node in groups[0].findall('partition_index'):
        name=node.findtext('partition_name');image=node.findtext('file_name');flag=node.findtext('is_download')
        if not name or name in seen or flag not in ('true','false'): raise ValueError('Ambiguous partition entry')
        seen.add(name)
        if flag=='false':continue
        if name in PROTECTED: raise ValueError('Factory identity/security partition selected: '+name)
        if not image or not re.fullmatch(r'[A-Za-z0-9_.-]+',image) or image in ('.','..','NONE'): raise ValueError('Invalid image path')
        result.append({'partition':name,'image':image,'region':node.findtext('region'),
                       'size':int(node.findtext('partition_size'),0),'offset':int(node.findtext('physical_start_addr'),0),
                       'erases_user_data':name in ('userdata','metadata'),'bootloader':name.startswith(('preloader','lk'))})
    if not result:raise ValueError('No images selected')
    return {'platform':'MT6835','storage':storage,'partitions':result,'flash_ready':False,
            'reason':'Device matching, signature verification and flashing transport still require validation'}


def review_package(path, storage):
    import zipfile
    from pathlib import PurePosixPath
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if len(entries)>2000: raise ValueError('Archive has too many entries')
        names=[e.filename for e in entries]
        if len(names)!=len(set(names)): raise ValueError('Duplicate archive entries')
        for name in names:
            parts=PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe archive path')
        scatters=[e for e in entries if e.filename.endswith('_Android_scatter.xml')]
        if len(scatters)!=1 or scatters[0].file_size>1024*1024: raise ValueError('Expected one bounded scatter file')
        scatter=scatters[0]; plan=review_scatter(archive.read(scatter),storage)
        base=PurePosixPath(scatter.filename).parent
        for part in plan['partitions']:
            name=str(base/part['image'])
            if name not in names: raise ValueError('Selected image missing: '+name)
        agent_name=str(base/'download_agent'/'DA_BR.bin')
        agent_entries=[e for e in entries if e.filename==agent_name]
        plan['download_agent_present']=bool(agent_entries and agent_entries[0].file_size>0)
        plan['publisher_signature_verified']=False
        return plan
