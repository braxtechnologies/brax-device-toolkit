"""Optional vendor download, never an automatic executable launch."""
from pathlib import Path
import os
import re
import tkinter as tk
from tkinter import ttk, filedialog
import webbrowser
from . import firmware_catalog as catalog

DOWNLOAD_PAGE = catalog.ROOT + 'flash-tool/'
REQUIRED_FILES = ('flash.dll', 'Qt5Core.dll', 'Qt5Gui.dll', 'Qt5Widgets.dll', 'usb_settings.xml')


def windows_packages(reader=catalog.fetch):
    rows = catalog.parse_index(reader(DOWNLOAD_PAGE))
    files = {row['name']: row for row in rows if row['path_type'] == 'File'}
    packages = []
    for name, row in files.items():
        if not re.fullmatch(r'sp-flash-tool-(windows|win)-v6(?:[.-][0-9]+)*\.zip', name):
            continue
        if name+'.sha256' not in files or type(row.get('size')) is not int or not 0 < row['size'] <= 512*1024**2:
            continue
        packages.append(dict(row, url=DOWNLOAD_PAGE+name, sha256_url=DOWNLOAD_PAGE+name+'.sha256'))
    return sorted(packages, key=lambda row: row['name'])


def check_installation(value):
    path = Path(value)
    if not path.is_absolute() or path.name.lower() != 'spflashtoolv6.exe' or not path.is_file():
        raise ValueError('Choose SPFlashToolV6.exe inside the extracted Windows tool folder.')
    with path.open('rb') as source:
        if source.read(2) != b'MZ':
            raise ValueError('The selected file is not a Windows executable.')
    missing = [name for name in REQUIRED_FILES if not (path.parent/name).is_file()]
    if missing:
        raise ValueError('The tool folder is incomplete. Extract the entire ZIP, not just the EXE. Missing: '+', '.join(missing))
    return {'path': str(path), 'status': 'Required files found. The tool was not launched; device compatibility is checked separately.'}


class FlashPrerequisite:
    def __init__(self, app, parent, run):
        self.app, self.run, self.packages = app, run, {}
        saved = app.settings.get('sp_flash_tool', '')
        default = saved if isinstance(saved, str) else ''
        app.firmware_tool = tk.StringVar(value=default)
        self.selected = tk.StringVar()
        self.message = tk.StringVar(value='Check your tool folder or look for a Windows download. Nothing is installed or run automatically.')
        box = ttk.Frame(parent, padding=20, style='Card.TFrame', relief='solid', borderwidth=1)
        box.pack(fill='x', pady=(0, 16))
        ttk.Label(box, text='Before installation · SP Flash Tool for Windows', style='Step.TLabel').pack(anchor='w')
        label = ttk.Label(box, text='This app uses SP Flash Tool to write the OS. Set it up once on this computer.\n1. Check the download page and download a Windows ZIP.\n2. In File Explorer, right-click the ZIP → Extract All. Keep the entire folder together.\n3. Choose SPFlashToolV6.exe from that folder and check its files below.\nUSB drivers are separate. Downloading the tool does not enable flashing or bypass phone compatibility checks.', style='Card.TLabel', wraplength=800)
        label.pack(fill='x', pady=10)
        box.bind('<Configure>', lambda event: label.configure(wraplength=max(300, event.width-48)))
        app.action(box, 'Check for Windows tool download', self.check_catalog, advanced=True)
        self.combo = ttk.Combobox(box, textvariable=self.selected, values=[], state='disabled')
        self.combo.pack(fill='x', pady=6)
        self.download_button = app.action(box, 'Download Windows tool & verify checksum', self.download, advanced=True)
        app.action(box, 'Open downloaded tools folder', self.open_downloads, advanced=True)
        app.action(box, 'Open tool download page', lambda: webbrowser.open(DOWNLOAD_PAGE), advanced=True)
        ttk.Label(box, textvariable=app.firmware_tool, style='Card.TLabel', wraplength=800).pack(anchor='w', pady=6)
        app.action(box, 'Choose existing SP Flash Tool', self.choose, advanced=True)
        app.action(box, 'Check tool files', self.check, advanced=True)
        ttk.Label(box, textvariable=self.message, style='Card.TLabel', wraplength=800).pack(anchor='w', pady=8)
        app.firmware_tool.trace_add('write', lambda *_: self.message.set('Tool path changed. Check its files before installation.'))
        self.refresh()

    def refresh(self):
        self.download_button.configure(state='normal' if self.packages and not self.app.busy else 'disabled')
        self.combo.configure(state='readonly' if self.packages and not self.app.busy else 'disabled')

    def check_catalog(self):
        self.packages = {}; self.selected.set(''); self.refresh()
        def done(rows):
            self.packages = {row['name']: row for row in rows}
            self.combo.configure(values=list(self.packages))
            self.selected.set(next(iter(self.packages), ''))
            self.message.set('Select the Windows package to download.' if rows else
                'No Windows v6 ZIP with a SHA256 file is published here yet. The Linux ZIP does not work with this Windows app. You can choose an existing Windows SP Flash Tool folder above.')
            self.refresh()
        self.run('Checking Windows prerequisite downloads', windows_packages, done)

    def download(self):
        package = self.packages.get(self.selected.get())
        if not package or self.app.busy:
            return
        def done(result):
            self.message.set('Checksum matches. Saved: '+result['path']+'\nNext: right-click the ZIP in File Explorer → Extract All, then choose SPFlashToolV6.exe above. The tool was not run.')
        self.run('Downloading Windows SP Flash Tool', lambda: catalog.download(package,
            self.app.data/'tools', progress=lambda n, total: self.app.firmware_progress.emit(
                f'SP Flash Tool download: {100*n/total:.1f}%', 100*n/total)), done)

    def choose(self):
        path = filedialog.askopenfilename(parent=self.app.root, title='Choose extracted Windows SP Flash Tool',
            initialdir=str(self.app.data/'tools'), filetypes=[('SP Flash Tool', 'SPFlashToolV6.exe')])
        if path:
            self.app.firmware_tool.set(path)
            self.check()

    def open_downloads(self):
        folder = self.app.data/'tools'
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def check(self):
        try:
            result = check_installation(self.app.firmware_tool.get())
            self.message.set(result['status'])
        except (ValueError, OSError) as exc:
            self.message.set(str(exc))
