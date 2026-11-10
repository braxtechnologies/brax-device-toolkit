"""Validate Android sparse v1 images and expose bounded data extents.

DONT_CARE ranges are skipped, not erased. CRC-bearing images are rejected until
expanded CRC verification is supported. No caller should start writes until
the complete image has passed inspection.
"""
from pathlib import Path
import struct

MAGIC = 0xed26ff3a


def inspect_image(path, capacity):
    path = Path(path)
    file_size = path.stat().st_size
    if file_size <= 0 or capacity <= 0:
        raise ValueError('Image or partition is empty')
    with path.open('rb') as source:
        header = source.read(28)
        if len(header) < 4 or struct.unpack('<I', header[:4])[0] != MAGIC:
            size = (file_size + 511) // 512 * 512
            if size > capacity:
                raise ValueError('Aligned raw image exceeds partition capacity')
            return {'size':size, 'sparse':False, 'extents':[
                {'offset':0, 'size':size, 'file_offset':0, 'file_size':file_size, 'kind':'raw'}]}
        if len(header) != 28:
            raise ValueError('Truncated sparse header')
        _, major, minor, fh, ch, block, blocks, count, checksum = struct.unpack('<I4H4I', header)
        if major != 1 or minor != 0 or not 28 <= fh <= 4096 or not 12 <= ch <= 4096:
            raise ValueError('Unsupported sparse header version or size')
        if block == 0 or block % 512 or block > 1024*1024 or not 0 < blocks*block <= capacity:
            raise ValueError('Sparse expanded size exceeds partition or is unaligned')
        if not 0 < count <= 1000000 or checksum:
            raise ValueError('Unsupported sparse chunk count or image checksum')
        source.seek(fh)
        position = 0
        extents = []
        for _ in range(count):
            data = source.read(ch)
            if len(data) != ch:
                raise ValueError('Truncated sparse chunk header')
            kind, reserved, length, total = struct.unpack('<2H2I', data[:12])
            size = length*block
            if reserved or total < ch or source.tell()+total-ch > file_size:
                raise ValueError('Invalid sparse chunk bounds')
            if size <= 0 or position+size > blocks*block:
                raise ValueError('Invalid sparse output bounds')
            if kind == 0xcac1 and total-ch == size:
                extents.append({'offset':position,'size':size,'kind':'raw',
                                'file_offset':source.tell(),'file_size':size})
                source.seek(size, 1)
            elif kind == 0xcac2 and total-ch == 4:
                extents.append({'offset':position,'size':size,'kind':'fill',
                                'pattern':source.read(4).hex()})
            elif kind == 0xcac3 and total == ch:
                pass
            else:
                raise ValueError('Malformed or unsupported sparse chunk (including CRC chunks)')
            position += size
        if position != blocks*block or source.tell() != file_size:
            raise ValueError('Sparse image length or block count mismatch')
    if not extents:
        raise ValueError('Sparse image contains no data to write')
    return {'size':position,'sparse':True,'extents':extents}


def pieces(image, chunk_size=8*1024*1024):
    if chunk_size <= 0 or chunk_size % 512:
        raise ValueError('Chunk size must be sector aligned')
    for extent in image['extents']:
        for start in range(0, extent['size'], chunk_size):
            size = min(chunk_size, extent['size']-start)
            yield extent, start, extent['offset']+start, size


def read_piece(path, extent, start, size):
    if start < 0 or size <= 0 or start+size > extent['size']:
        raise ValueError('Piece exceeds extent')
    if extent['kind'] == 'fill':
        return bytes.fromhex(extent['pattern'])*(size//4)
    count = min(size, max(0, extent['file_size']-start))
    with Path(path).open('rb') as source:
        source.seek(extent['file_offset']+start)
        data = source.read(count)
    if len(data) != count:
        raise ValueError('Image changed or was truncated after inspection')
    return data + bytes(size-count)
