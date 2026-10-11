"""Bluetooth/Wi-Fi review, write, recovery and persistence controls."""
from tkinter import filedialog, messagebox
from pathlib import Path
from . import mac_records as mac
from .session import open_serial, MetaSession
from .ui_base import new_record_path
from .device_connection import return_normal, boot_meta
from .discovery import check_connection
from .serial_record import _save_exclusive


def build_mac_controls(app, section, text, require_meta, accepted):
    frame = section('Bluetooth & Wi-Fi addresses',
        'Write assigned factory addresses. Only the six address bytes change; calibration data is preserved. '
        'Factory-record write, reboot persistence and restoration tested on BaseOS build 58. Wi-Fi privacy addresses may differ from the factory address.')
    text(frame,'Enter new addresses alongside serial and IMEI in the main Serial & IMEI tab. These controls inspect and restore MAC backups.')
    pending = {}
    def read(v):
        with open_serial(v['port']) as stream:
            return mac.capture(MetaSession(stream),v['serial'])
    def show_review(review, v):
        before, after = mac.addresses(review['before']), mac.addresses(review['after'])
        description = '\n'.join(f'{name}: {before[name]} -> {after[name]}' for name in mac.PROFILES)
        if not messagebox.askyesno('Review factory addresses',description+'\n\nWrite these changes and verify both complete records?',parent=app.root):
            return
        fresh = require_meta()
        if any(fresh[k] != v[k] for k in ('serial','port')):
            raise ValueError('Selected phone changed. Review again.')
        def operation():
            folder = new_record_path(app.data/'mac-backups')
            with open_serial(v['port']) as stream:
                mac.write_reviewed(MetaSession(stream),v['serial'],review,folder)
            return str(folder)
        def done(folder):
            pending.update(target=review['after'],folder=folder)
            app.status.set('Addresses read back correctly. Backup: '+folder+'. Use Restart & verify MAC addresses next.')
        app.recovery_required=True
        app.run('Writing factory addresses',operation,done)
    def inspect():
        v=require_meta()
        app.run('Reading factory addresses',lambda:read(v),lambda r:app.status.set('\n'.join(k+': '+s for k,s in mac.addresses(r).items())))
    def restore():
        v=require_meta()
        folder=filedialog.askdirectory(parent=app.root,title='Choose MAC transaction backup',initialdir=str(app.data/'mac-backups'),mustexist=True)
        if folder:
            app.run('Reviewing MAC restore',lambda:mac.restore_plan(folder,read(v)),lambda r:app.root.after(0,lambda:show_review(r,v)))
    def verify():
        v=require_meta()
        if not pending: raise ValueError('No successful MAC write or restore awaits reboot verification in this session.')
        target,folder=pending['target'],pending['folder']
        if target['serial'] != v['serial']: raise ValueError('Select the phone for this transaction.')
        def operation():
            return_normal(v['adb'],v['port'],v['serial'],report=app.emit)
            connection=check_connection(v['adb'],v['serial'])
            meta=boot_meta(v['adb'],v['serial'],connection['usb_location'],report=app.emit)
            actual=read({**v,'port':meta['port']})
            if actual != target: raise ValueError('MAC records did not persist. Detect the phone again and review the saved backup.')
            return_normal(v['adb'],meta['port'],v['serial'],report=app.emit)
            _save_exclusive(Path(folder)/'reboot-verified',b'Both full records matched after reboot.\n')
            return {**connection,'mode':'normal','port':''}
        def done(result):
            pending.clear(); app.recovery_required=False; accepted(result)
            app.status.set('Both MAC records verified after reboot. Phone is back in Android.')
        app.current_mode=None
        app.run('Verifying MAC persistence',operation,done)
    # Catch validation errors before starting worker threads and display next to action.
    def guarded(fn):
        def invoke():
            try: fn()
            except (ValueError,OSError) as exc: app.status.set(str(exc))
        return invoke
    for title, fn in [('Read factory Bluetooth & Wi-Fi addresses',inspect),
                      ('Choose MAC backup & restore',restore),
                      ('Restart & verify MAC addresses',verify)]:
        app.action(frame,title,guarded(fn),advanced=True)
