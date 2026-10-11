"""Firmware catalog, diagnostics and explicit external vendor-tool adapter."""
import tkinter as tk
import threading
from tkinter import ttk, filedialog
from . import firmware_catalog as fw
from .firmware_progress import FirmwareProgress

def build_advanced_firmware(app,parent,monitor):
    ttk.Label(parent,text='Advanced recovery & diagnostics',style='Title.TLabel').pack(anchor='w')
    def run(label, operation, done=None):
        if app.busy:return
        monitor.follow = None
        monitor.emit(label+' — started', 0)
        def tracked():
            try:
                result = operation()
                if isinstance(result, dict) and 'boot_probe_error' in result:
                    monitor.emit('Error: '+result['boot_probe_error'])
                else:
                    monitor.emit(label+' - completed; see operation result', 100)
                return result
            except Exception as exc:
                monitor.emit('Error: '+str(exc))
                raise
        app.run(label, tracked, done)
    ttk.Label(parent,text='Choose a BraX3 package from download.os-source.co. Standalone flashing is under development. The iodéOS test used external SP Flash Tool; this app does not yet replace that dependency.',wraplength=800).pack(anchor='w',pady=12)
    ttk.Label(parent,text='Connect a powered-off phone',style='Step.TLabel').pack(anchor='w',pady=(8,4))
    ttk.Label(parent,text='Unplug the phone and power it fully off. Start the connection check below, then connect USB. The tool listens for the brief MediaTek preloader/BROM port. USB debugging and META mode are not required.',wraplength=800).pack(anchor='w',pady=6)
    boot_status=tk.StringVar(value='Boot connection has not been tested.')
    ttk.Label(parent,textvariable=boot_status,wraplength=800).pack(anchor='w',pady=6)
    stop=threading.Event()
    def check_boot():
        from .boot_probe import wait_and_probe
        if app.busy:return
        stop.clear()
        boot_status.set('Listening for 60 seconds. Connect the powered-off phone now. This check reads chip information; it does not flash firmware.')
        def done(result):
            if 'boot_probe_error' in result:
                app.firmware_device = {}
                boot_status.set(result['boot_probe_error'])
                app.status.set(result['boot_probe_error'])
                return
            app.firmware_device = dict(result)
            boot_status.set(f"{result['mode']} handshake verified on {result['port']}. Chip code: {result['hw_code']}.\nPreloader reports secure boot: {result['secure_boot']} · Link authentication: {result['serial_link_auth']} · Download Agent authentication: {result['download_agent_auth']}.\nThese reported flags are not an eFuse audit. Agent loading and flashing are not validated by this check.")
            app.status.set('Boot connection verified; no firmware was written.')
        def operation():
            try:
                return wait_and_probe(cancel=stop, event=lambda event:monitor.emit(str(event)))
            except Exception as exc:
                monitor.emit('Error: '+str(exc))
                return {'boot_probe_error':str(exc)}
        run('Checking powered-off connection',operation,done)
    app.action(parent,'Check powered-off connection',check_boot,advanced=True)
    # Remains usable while the worker is listening; does not touch phone storage.
    ttk.Button(parent,text='Stop connection check',command=stop.set).pack(anchor='w',pady=4)
    selected=tk.StringVar();os_choice=tk.StringVar();packages={}
    ttk.Label(parent,text='Operating system').pack(anchor='w')
    os_combo=ttk.Combobox(parent,textvariable=os_choice,state='readonly',width=40);os_combo.pack(fill='x',pady=8)
    ttk.Label(parent,text='Release and installation format').pack(anchor='w')
    combo=ttk.Combobox(parent,textvariable=selected,state='readonly',width=80);combo.pack(fill='x',pady=10)
    details=tk.StringVar(value='Refresh the catalog to list available packages.')
    ttk.Label(parent,textvariable=details,wraplength=800).pack(anchor='w',pady=10)
    def changed(*_):
        p=packages.get(selected.get())
        if p:details.set('Format: '+p['format']+' · '+str(round(p['size']/1024**3,2))+' GiB\nSHA256: '+('published' if p['sha256_url'] else 'missing')+' · Publisher signature: '+('available, not yet verified' if p['signature_available'] else 'missing')+'\nNot yet validated for automatic flashing. OTA and fastboot packages require different installers.')
    selected.trace_add('write',changed)
    def family(p):
        return 'BaseOS' if p['name'].startswith('baseos-') else 'iodéOS' if p['name'].startswith('iode-') else 'Ubuntu Touch' if p['name'].startswith('ubuntutouchos-') else 'Other'
    def select_os(*_):
        names=[name for name,p in packages.items() if family(p)==os_choice.get()]
        names.sort(key=lambda name:packages[name]['format']!='spflash')
        combo.configure(values=names);selected.set(names[0] if names else '')
    os_choice.trace_add('write',select_os)
    def refresh():
        def done(rows):
            packages.clear();packages.update({r['name']:r for r in rows})
            families=sorted({family(p) for p in rows});os_combo.configure(values=families);os_choice.set(families[0] if families else '')
            app.status.set(f'{len(rows)} packages found. Availability does not establish flashing compatibility.')
        run('Loading OS catalog',fw.catalog,done)
    def download():
        p=packages.get(selected.get())
        if not p:app.status.set('Select a package first.');return
        run('Downloading and checking SHA256',lambda:fw.download(p,app.data/'firmware',progress=lambda n,total:monitor.emit(f'Download: {n*100/total:.1f}% ({n/1024**2:.1f} / {total/1024**2:.1f} MiB)',n*100/total)),lambda r:app.status.set('Saved: '+r['path']+'. SHA256 matches; signature and flashing support still pending.'))
    app.action(parent,'Refresh OS catalog',refresh,advanced=True)
    app.action(parent,'Download & check SHA256',download,advanced=True)
    def inspect_package():
        from .flash_plan import review_package
        path=filedialog.askopenfilename(parent=app.root,title='Inspect a downloaded SP Flash package',initialdir=str(app.data/'firmware'),filetypes=[('Firmware ZIP','*.zip')])
        if not path:return
        def done(plan):
            details.set('EMMC package review (device storage not detected): '+str(len(plan['partitions']))+' partitions.\n'+('Includes user-data erasure.' if any(p['erases_user_data'] for p in plan['partitions']) else 'No userdata image selected.')+'\n'+', '.join(p['partition'] for p in plan['partitions'])+'\nSignature and device compatibility still pending; flashing disabled.')
        run('Inspecting firmware package',lambda:review_package(path,'EMMC'),done)
    app.action(parent,'Inspect downloaded SP Flash package',inspect_package,advanced=True)
    from .flash_controls import build_flash_controls
    build_flash_controls(app, parent, run, monitor)
    ttk.Label(parent,text='Standalone Python flashing engine: not yet available. The operations above require external SP Flash Tool.',wraplength=800).pack(anchor='w',pady=16)


def build_firmware(app, parent):
    from .firmware_guide import FirmwareGuide, build_mode_guide
    progress_box = ttk.Frame(parent)
    app.firmware_progress = monitor = FirmwareProgress(app.root, progress_box, app.data/'firmware-logs')
    def run(label, operation, done=None):
        if app.busy:
            return
        monitor.follow = None
        monitor.emit(label+' — started', 0)
        def tracked():
            try:
                result = operation()
                monitor.emit(label+' — complete', 100)
                return result
            except Exception as exc:
                monitor.emit('Error: '+str(exc))
                raise
        app.run(label, tracked, done)
        app.firmware_guide.refresh()
    app.firmware_guide = FirmwareGuide(app, parent, run)
    from .flash_prerequisite import FlashPrerequisite
    prerequisites = ttk.Frame(parent)
    prerequisites.pack(fill='x', before=app.firmware_guide.rail)
    app.flash_prerequisite = FlashPrerequisite(app, prerequisites, run)
    advanced = ttk.Frame(parent, padding=16)
    show_advanced = tk.BooleanVar(value=False)
    def toggle():
        if app.busy:
            show_advanced.set(not show_advanced.get()); return
        if show_advanced.get():
            advanced.pack(fill='x', before=progress_box)
        else:
            advanced.pack_forget()
    ttk.Checkbutton(parent, text='Advanced recovery: phone cannot start, connection diagnostics, or other erase options',
                    variable=show_advanced, command=toggle).pack(anchor='w', pady=12)
    ttk.Label(advanced, text='Use this path if Android cannot start or you need service diagnostics. It does not bypass compatibility approval. Reading a boot connection does not authorize a flash.', wraplength=850).pack(anchor='w')
    build_mode_guide(advanced)
    build_advanced_firmware(app, advanced, monitor)
    progress_box.pack(fill='x', pady=12)
