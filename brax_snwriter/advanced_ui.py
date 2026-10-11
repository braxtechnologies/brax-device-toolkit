"""Connection, mode detection and recovery controls for the repair app."""
import json
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from .discovery import adb_states, debugging_guidance, check_connection
from .session import open_serial, MetaSession
from .modem import ModemSession
from .serial_record import BASEOS_FINGERPRINT, validate_serial
from .device_connection import boot_meta, return_normal, NO_WINDOW
from .ui_base import new_record_path
from . import advanced_records as records


def remember_backup_folder(data, folder):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        return
    temporary = Path(data)/'backup-location.json.tmp'
    temporary.write_text(json.dumps({'folder':str(folder)}), encoding='utf-8')
    temporary.replace(Path(data)/'backup-location.json')


def backup_start_folder(data):
    data = Path(data)
    try:
        saved = Path(json.loads((data/'backup-location.json').read_text(encoding='utf-8'))['folder'])
        if saved.is_dir():
            return saved
    except (OSError, ValueError, KeyError, TypeError):
        pass
    snapshots = data/'snapshots'
    available = [p.parent for p in snapshots.glob('*/snapshot.json')
                 if (p.parent/'snapshot.sha256').is_file()]
    if available:
        return max(available, key=lambda p:(p/'snapshot.json').stat().st_mtime_ns)
    return snapshots if snapshots.is_dir() else data


def detect(adb):
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if (p.vid,p.pid)==(0x0e8d,0x200e)]
    output = subprocess.check_output([adb,'devices','-l'],text=True,timeout=10,creationflags=NO_WINDOW)
    phones = {s:state for s,state in adb_states(output).items() if not s.startswith('emulator-')}
    if len(ports)>1 or len(phones)>1:
        raise ValueError('More than one phone connection found. Connect only the phone you want to service.')
    if ports:
        with open_serial(ports[0].device) as stream:
            session=MetaSession(stream)
            serial=session.property('ro.vendor.serialno')
            fingerprint=session.property('ro.build.fingerprint')
            validate_serial(serial)
            if fingerprint!=BASEOS_FINGERPRINT:
                raise ValueError('This phone has an unsupported OS; no changes made.')
        # BaseOS exposes ADB and the AP META COM interface simultaneously.
        # Correlate them by the verified identity instead of counting interfaces.
        if phones:
            if set(phones)!={serial}:
                raise ValueError('ADB and META identify different phones. Disconnect the other phone and detect again.')
            if phones[serial]!='device':
                raise ValueError(debugging_guidance(phones[serial]))
            def prop(name):
                return subprocess.check_output([adb,'-s',serial,'shell','getprop',name],
                    text=True,timeout=10,creationflags=NO_WINDOW).strip()
            if (prop('ro.serialno'),prop('ro.build.fingerprint'),prop('ro.bootmode')) != (serial,fingerprint,'meta'):
                raise ValueError('ADB and META details disagree. Wait for the mode change to finish and detect again.')
        return {'mode':'meta','serial':serial,'fingerprint':fingerprint,'port':ports[0].device,
                'usb_location':(ports[0].location or '').split(':')[0]}
    if phones:
        serial,state=next(iter(phones.items()))
        if state!='device':
            raise ValueError(debugging_guidance(state))
        return {**check_connection(adb,serial),'mode':'normal','port':''}
    raise ValueError(debugging_guidance(None))


def read_phone(v):
    # Identity and complete records are checked on every operation, not trusted from labels.
    with open_serial(v['port']) as stream:
        return records.capture(MetaSession(stream),ModemSession(stream,timeout=20),v['serial'])


def build_advanced(app, parent):
    app.current_mode = None
    app.mode_detail = tk.StringVar(value='Current mode: not checked. Detect the connected phone first.')
    for key in ('usb','port','blank','imei_blank'):
        app.fields[key] = tk.StringVar(value='No' if key in ('blank','imei_blank') else '')
    def text(frame, value):
        label=ttk.Label(frame,text=value,wraplength=790,justify='left')
        label.pack(fill='x',pady=(0,10))
        frame.bind('<Configure>',lambda e:label.configure(wraplength=max(200,e.width-40)),add='+')
    def section(title, description):
        frame=ttk.Frame(parent,padding=20,relief='solid',borderwidth=1)
        frame.pack(fill='x',pady=(0,18))
        ttk.Label(frame,text=title,font=('Segoe UI',16,'bold')).pack(anchor='w',pady=(0,8))
        text(frame,description)
        return frame
    ttk.Label(parent,text='Advanced tools',style='Title.TLabel').pack(anchor='w',pady=(0,8))
    text(parent,'Detect or change phone mode, inspect records, and recover from a saved backup. Actions report their result beside the button.')
    connection=section('Phone connection & mode','Keep one phone connected by USB. Detect its current mode first, then choose where it should boot. Mode changes do not write identifiers or undo previous writes.')
    app.add_field(connection,'adb','ADB executable','adb')
    app.adb_status=tk.StringVar()
    app.adb_browse=ttk.Button(app.form,text='Browse…',command=app.choose_adb)
    app.adb_browse.grid(row=0,column=2,padx=8);app.buttons.append(app.adb_browse)
    ttk.Label(connection,textvariable=app.adb_status).pack(anchor='w',pady=(0,10))
    app.fields['adb'].trace_add('write',lambda *_:app.update_adb_status())
    app.resolve_adb()
    ttk.Label(connection,textvariable=app.mode_detail,wraplength=780,font=('Segoe UI',11,'bold')).pack(fill='x',pady=10)
    def accepted(result):
        app.fields['serial'].set(result['serial'])
        app.fields['usb'].set(result['usb_location']);app.fields['port'].set(result['port'])
        app.current_mode=result['mode']
        app.show_checked_phone(result)
        app.mode_detail.set('Current mode: '+('Normal · Android running' if result['mode']=='normal' else 'META · Factory service mode')+'\n'+app.checked_phone.get()+'\nUSB: '+result['usb_location']+(' · Port: '+result['port'] if result['port'] else ''))
        app.status.set('Phone identity, OS and connection verified.')
        app.refresh_gates()
    def detect_mode():
        app.current_mode=None
        app.mode_detail.set('Checking connected phone…')
        app.run('Detecting current mode',lambda:detect(app.values()['adb']),accepted)
    text(connection,'Detect current mode reads the phone identity and connection. It does not reboot or write anything.')
    app.action(connection,'Detect current mode',detect_mode,advanced=True)
    def change_mode(target):
        v=app.values()
        if app.current_mode is None:
            app.status.set('Detect current mode first.');return
        if app.current_mode==target:
            app.status.set('Already in '+target+' mode.');return
        expected=app.pending_serial or v['serial']
        def operation():
            if target=='meta':
                checked=check_connection(v['adb'],v['serial'])
                if checked['usb_location']!=v['usb']:raise ValueError('USB location changed. Detect the phone again.')
                result=boot_meta(v['adb'],v['serial'],v['usb'],report=app.emit)
                return {**result,'mode':'meta','usb_location':result['usb_location'].split(':')[0]}
            return_normal(v['adb'],v['port'],v['serial'],expected_after=expected,report=app.emit)
            return {**check_connection(v['adb'],expected),'mode':'normal','port':''}
        def done(result):
            accepted(result)
            if target=='normal':app.pending_serial=None
        app.current_mode=None
        app.run('Changing phone mode',operation,done)
    text(connection,'Go to normal mode restarts an identified META phone into Android and verifies the boot. Go to META mode requires authorized USB debugging and verifies the service port after restarting.')
    app.normal_mode_button=app.action(connection,'Go to normal mode',lambda:change_mode('normal'),advanced=True)
    app.meta_mode_button=app.action(connection,'Go to META mode',lambda:change_mode('meta'),advanced=True)
    inspection=section('Connection & inspection','Read the phone before making changes. Empty records are detected automatically; the guided write review explains which new values will be stored. No empty-storage switches are needed.')
    def require_meta():
        if app.current_mode!='meta':raise ValueError('Detect the phone above, then choose Go to META mode.')
        v=app.values();app.validate_meta_target(v);return v
    def inspect():
        def operation():return read_phone(require_meta())
        app.run('Reading current identifiers',operation,lambda r:app.status.set('\n'.join(k+': '+v for k,v in records.describe(r).items())))
    app.action(inspection,'Read current serial & IMEIs',inspect,advanced=True)
    text(inspection,'Backups save existing factory records on this computer. They are not backups of Android, photos or apps. Guided writes already back up the records they change.')
    def backup():
        def operation():
            snapshot=read_phone(require_meta())
            return {'folder':records.save_snapshot(snapshot,new_record_path(app.data/'snapshots'))}
        def done(result):
            remember_backup_folder(app.data,result['folder'])
            app.status.set('Backup saved: '+result['folder']+'. No phone records changed.')
        app.run('Backing up serial and IMEI records',operation,done)
    app.action(inspection,'Back up current serial & IMEIs',backup,advanced=True)
    text(inspection,'Restore uses a backup from this phone. Choose what to restore, review the current and saved values, then confirm. If the backup contains empty records, restoring it returns those records to empty. Readback is followed by a separate restart/persistence check.')
    def choose_restore():
        if app.current_mode!='meta':app.status.set('Detect the phone and enter META mode before reviewing a backup.');return
        directory=filedialog.askdirectory(parent=app.root,title='Choose snapshot or guided-session backup folder',initialdir=str(backup_start_folder(app.data)),mustexist=True)
        if not directory:return
        remember_backup_folder(app.data,directory)
        dialog=tk.Toplevel(app.root);dialog.title('Review backup restore');dialog.transient(app.root);dialog.grab_set()
        frame=ttk.Frame(dialog,padding=20);frame.pack(fill='both',expand=True)
        text(frame,'Select a record group, then read the phone and compare it with the backup. Nothing is written until you confirm.')
        scope=tk.StringVar(value='serial');ttk.Combobox(frame,textvariable=scope,values=['serial','imei'],state='readonly').pack(fill='x')
        details=tk.StringVar(value='Not reviewed yet.');ttk.Label(frame,textvariable=details,wraplength=620).pack(fill='x',pady=16)
        consent=tk.BooleanVar(value=False);plan={}
        def invalidate(*_):plan.clear();consent.set(False);restore_button.configure(state='disabled');details.set('Review this selection before restoring.')
        scope.trace_add('write',invalidate)
        def review():
            chosen=scope.get();v=require_meta()
            def operation():
                current=read_phone(v)
                target=records.load_target(directory,chosen,current)
                return {**target,'current':current,'scope':chosen,'connection':v}
            def done(result):
                if not dialog.winfo_exists() or scope.get()!=result['scope']:return
                plan.clear();plan.update(result);consent.set(False)
                before,after=records.describe(result['current']),records.describe(result['target'])
                keys=['serial'] if chosen=='serial' else ['SIM 1','SIM 2']
                details.set('\n'.join(k+': '+before[k]+' → '+after[k] for k in keys)+'\n\nOnly the selected record group will be restored.')
            app.run('Checking backup and phone',operation,done)
        ttk.Button(frame,text='Review selected backup',command=review).pack(anchor='w')
        ttk.Checkbutton(frame,text='I checked these changes and want to restore the selected records.',variable=consent).pack(anchor='w',pady=12)
        def execute():
            if not consent.get() or not plan or app.busy:return
            reviewed=dict(plan);v=reviewed['connection']
            if v!=app.values():details.set('Connection changed. Review again.');return
            dialog.destroy();app.recovery_required=True
            def operation():
                with open_serial(v['port']) as stream:
                    return records.restore_reviewed(MetaSession(stream),ModemSession(stream,timeout=20),v['serial'],reviewed,reviewed['scope'],new_record_path(app.data/'restore-sessions'))
            def done(result):
                app.pending_restore=reviewed
                if reviewed['scope']=='serial':
                    value=records.serial_text(bytes.fromhex(reviewed['target']['product']))
                    if not value.startswith('('):app.pending_serial=value
                    elif reviewed['kind']=='transaction':app.pending_serial=json.loads((Path(reviewed['directory'])/'intent.json').read_text())['old_serial']
                app.status.set('Restore readback passed. Use Restart & verify restored records to check persistence.')
            app.run('Restoring reviewed records',operation,done)
        restore_button=ttk.Button(frame,text='Confirm restore',command=execute,state='disabled');restore_button.pack(anchor='w')
        consent.trace_add('write',lambda *_:restore_button.configure(state='normal' if consent.get() and plan else 'disabled'))
        ttk.Button(frame,text='Cancel',command=dialog.destroy).pack(anchor='e')
    app.action(inspection,'Choose backup & review',choose_restore,advanced=True)
    def verify_restore():
        plan=getattr(app,'pending_restore',None)
        if not plan:app.status.set('No completed restore is awaiting verification.');return
        v=app.values();expected=app.pending_serial or v['serial']
        def operation():
            return_normal(v['adb'],v['port'],v['serial'],expected_after=expected,report=app.emit)
            connection=check_connection(v['adb'],expected)
            meta=boot_meta(v['adb'],expected,connection['usb_location'],report=app.emit)
            actual=read_phone({**v,'serial':expected,'port':meta['port']})
            key='product' if plan['scope']=='serial' else 'imeis'
            if actual[key]!=plan['target'][key]:raise ValueError('Restored records did not persist. Keep the backups and inspect the phone.')
            return_normal(v['adb'],meta['port'],expected,report=app.emit)
            return {**connection,'mode':'normal','port':''}
        def done(result):
            app.recovery_required=False;app.pending_serial=None;app.pending_restore=None
            accepted(result);app.status.set('Restored records verified after reboot. Phone is back in Android.')
        app.run('Verifying restored records after reboot',operation,done)
    app.action(inspection,'Restart & verify restored records',verify_restore,advanced=True)
    from .mac_ui import build_mac_controls
    build_mac_controls(app, section, text, require_meta, accepted)
    support=section('Records & support','Check capabilities, find records, or start again. Backups preserve previous records.')
    for title,desc,callback in [
        ('Show full capability list','See supported operations, firmware profiles and features that are not implemented.',app.show_capabilities),
        ('Open records','Open the local folder of backups and operation results. Opening it does not restore anything.',app.open_records),
        ('Save activity log','Save this session’s messages as a text file. Device backups must be copied separately.',app.save_log)]:
        text(support,desc);app.action(support,title,callback,advanced=True)
    def reset():
        if messagebox.askyesno('Start a fresh session?','Have you checked the previous outcome and resolved any uncertain write?\n\nThis clears selected phone and entered identifiers. It keeps logs/backups and does not change the phone.',parent=app.root):
            app.reset_guided();app.current_mode=None;app.mode_detail.set('Current mode: not checked.');app.tabs.select(app.sections['advanced'])
    text(support,'Clear the selected phone, entered identifiers and progress after checking the previous outcome. Logs and backups are kept; this does not undo a write or repair a phone.')
    app.action(support,'Begin a fresh session after recovery',reset,advanced=True)
