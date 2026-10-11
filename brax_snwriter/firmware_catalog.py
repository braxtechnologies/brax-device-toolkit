"""Read Brax's DUFS catalog. Download integrity is not publisher authentication."""
import base64
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, quote

ROOT = 'https://download.os-source.co/brax3/'
MAX_INDEX = 2 * 1024 * 1024

def allowed_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc!='download.os-source.co' or not p.path.startswith('/brax3/') or p.query or p.fragment or '/..' in p.path:
        raise ValueError('Unexpected firmware URL')
    return url

class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def open_url(url):
    return urllib.request.build_opener(SameOriginRedirect()).open(allowed_url(url),timeout=30)

def fetch(url, limit=MAX_INDEX):
    with open_url(url) as response:
        data=response.read(limit+1)
    if len(data)>limit: raise ValueError('Catalog response exceeds size limit')
    return data

def parse_index(data):
    match=re.search(rb'<template id="index-data">([A-Za-z0-9+/=]+)</template>',data)
    if not match: raise ValueError('Unrecognized download-page format')
    doc=json.loads(base64.b64decode(match[1],validate=True))
    if doc.get('kind')!='Index' or not doc.get('dir_exists'): raise ValueError('Catalog directory unavailable')
    rows=doc['paths']
    if not isinstance(rows,list) or len(rows)>1000: raise ValueError('Invalid directory listing')
    for row in rows:
        name=row['name']
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or name in ('.','..'):
            raise ValueError('Invalid catalog entry name')
    return rows

def catalog(reader=fetch):
    queue=[(ROOT,0)]; files=[]; count=0
    while queue:
        url,depth=queue.pop(0);count+=1
        if count>100: raise ValueError('Catalog traversal limit reached')
        for row in parse_index(reader(url)):
            child=url+quote(row['name'])
            if row['path_type']=='Dir' and depth<4 and row['name'] not in ('flash-tool','ota'):
                queue.append((child+'/',depth+1))
            elif row['path_type']=='File': files.append(dict(row,url=child))
    urls={r['url'] for r in files};result=[]
    for row in files:
        match=re.fullmatch(r'(.+)-brax3-(spflash|unsigned-fastboot|fastboot|ota)\.zip',row['name'])
        if not match: continue
        size=row.get('size')
        if type(size) is not int or not 0<size<=8*1024**3: continue
        result.append(dict(row,format=match[2],sha256_url=row['url']+'.sha256' if row['url']+'.sha256' in urls else None,
                           signature_available=row['url']+'.minisig' in urls))
    return sorted(result,key=lambda r:r['name'],reverse=True)

def download(package, folder, progress=lambda n,total:None):
    if not package.get('sha256_url'): raise ValueError('Published SHA256 is required')
    digest_text=fetch(package['sha256_url'],4096).decode('ascii').strip()
    match=re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+)',digest_text)
    if not match or match[2]!=package['name']: raise ValueError('Checksum filename does not match package')
    expected=match[1].lower()
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    name=package['name']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',name): raise ValueError('Invalid package name')
    target=folder/name;partial=folder/(name+'.partial')
    if target.exists() or partial.exists(): raise ValueError('Package or partial download already exists; inspect it before downloading again.')
    size=package['size'];written=0;digest=hashlib.sha256()
    if type(size) is not int or not 0<size<=8*1024**3: raise ValueError('Invalid package size')
    with open_url(package['url']) as response, partial.open('xb') as out:
        while True:
            chunk=response.read(1024*1024)
            if not chunk:break
            written+=len(chunk)
            if written>size: raise ValueError('Download exceeds published size')
            out.write(chunk);digest.update(chunk);progress(written,size)
    if written!=size or digest.hexdigest()!=expected: raise ValueError('Download size or checksum mismatch; partial file retained for inspection')
    partial.rename(target)
    return {'path':str(target),'sha256':expected,'size':written,'publisher_signature_verified':False,'flash_ready':False}
