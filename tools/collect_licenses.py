"""Copy installed dependency notices into both release distributions."""
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys

destination = Path(sys.argv[1])
destination.mkdir(parents=True, exist_ok=True)
fallback = Path(__file__).resolve().parent.parent/'licenses'
for name in ('pyserial', 'pyinstaller'):
    distribution = metadata.distribution(name)
    notices = [p for p in distribution.files or []
               if any(word in p.name.lower() for word in ('license', 'copying', 'notice'))
               and not str(p).endswith(('.py', '.pyc'))]
    if not notices:
        if name == 'pyserial' and distribution.version == '3.5':
            shutil.copyfile(fallback/'pyserial-3.5-LICENSE.txt', destination/'pyserial-3.5-LICENSE.txt')
            continue
        raise RuntimeError('Missing dependency license: '+name)
    for item in notices:
        target = destination/name/str(item)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(distribution.locate_file(item), target)
base = Path(sys.base_prefix)
python_license = base/'LICENSE.txt'
if not python_license.is_file():
    raise RuntimeError('Python LICENSE.txt is missing')
shutil.copyfile(python_license, destination/'Python-LICENSE.txt')
for component in ('tcl', 'tk'):
    folders = list((base/'tcl').glob(component+'[0-9]*'))
    notices = [p for folder in folders for p in folder.glob('*')
               if p.is_file() and p.name.lower() in ('license.terms', 'license', 'license.txt')]
    if not notices:
        if component == 'tcl':
            notices = [fallback/'tcl-license.terms']
        else:
            raise RuntimeError('Missing '+component+' license. Use the full Windows Python distribution.')
    for notice in notices:
        shutil.copyfile(notice, destination/(component+'-'+notice.name))
