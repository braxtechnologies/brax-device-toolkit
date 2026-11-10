"""Experimental native loader inspection in the existing firmware page."""
from datetime import datetime
import re
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk


def build_native_controls(app, parent, run, monitor):
    from .native_flash import connect, save_exclusive
    import json

    box = ttk.LabelFrame(parent, text='Native Python inspection & BaseOS test installation', padding=12)
    box.pack(fill='x', pady=12)
    ttk.Label(box, text='The Read storage button loads the stock Download Agent into RAM, reads storage and partitions, '
              'then requests a reboot without writing or erasing storage. No SP Flash Tool executable '
              'or DLL is used. A compatible vendor Download Agent and USB driver are still required.',
              wraplength=800).pack(anchor='w')
    path = tk.StringVar()
    inspected = {}

    def event(message):
        match = re.fullmatch(r'(Read|Wrote|Backup) (\d+)/(\d+) bytes', message)
        percent = 100*int(match[2])/int(match[3]) if match else None
        progress = re.search(r'\(([0-9.]+)%\)$',message)
        if progress:
            percent = float(progress[1])
        monitor.emit(message, percent)
    row = ttk.Frame(box)
    row.pack(fill='x', pady=8)
    ttk.Entry(row, textvariable=path).pack(side='left', fill='x', expand=True)

    def browse():
        choice = filedialog.askopenfilename(parent=app.root, title='Choose stock DA_BR.bin',
                                            filetypes=[('Download Agent', '*.bin')])
        if choice:
            path.set(choice)

    ttk.Button(row, text='Choose Download Agent', command=browse).pack(side='right')
    ttk.Label(box, text='First use Check powered-off connection above to record the USB location. '
              'Then unplug and fully power off the same phone. Start this test before reconnecting USB.',
              wraplength=800).pack(anchor='w', pady=6)

    def inspect():
        if app.busy:
            return
        device = dict(getattr(app, 'firmware_device', {}))
        location = (device.get('usb_location') or '').split(':')[0]
        if not device.get('handshake_verified') or not re.fullmatch(r'\d+(?:-\d+)+', location):
            app.status.set('Check the powered-off connection first to identify the USB location.')
            return
        agent_path = path.get()
        output = app.data/'native-flash'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')

        def operation(agent, inventory):
            output.mkdir(parents=True, exist_ok=False)
            save_exclusive(output/'inventory.json', json.dumps(inventory, indent=2).encode())
            return inventory

        app.status.set('Listening: reconnect the powered-off phone now.')
        inspected.clear()

        def finished(result):
            inspected.update(device=result, location=location)
            app.status.set(f"Read {len(result['partitions'])} partitions. Saved: {output}. "
                           'Reboot requested; check that Android starts. No storage written.')

        run('Native storage inspection',
            lambda: connect(agent_path, location, operation, event=event),
            finished)

    app.action(box, 'Read storage using Python', inspect, advanced=True)
    ttk.Label(box,text='Experimental installation: supports only the reviewed August 26 BaseOS 1.0 '
              'SP Flash ZIP. First inspect storage above, let Android restart, and authorize USB debugging. '
              'Installation resets apps and user data, preserves preloader and factory identities, and '
              'saves image-range backups for diagnosis. These are not complete phone backups; '
              'the agent may erase selected partitions.',wraplength=800).pack(anchor='w',pady=8)

    def review_install():
        if app.busy:
            return
        if not inspected:
            app.status.set('Read storage using Python first, then return to Android.')
            return
        package = filedialog.askopenfilename(parent=app.root,title='Choose BaseOS 1.0 SP Flash ZIP',
                                            filetypes=[('Firmware ZIP','*.zip')])
        if not package:
            return
        from .native_install import prepare, install
        from .firmware_guide import read_android
        import subprocess
        import os
        expected = json.loads(json.dumps(inspected['device']))
        location = inspected['location']
        adb = app.fields['adb'].get()
        output = app.data/'native-flash'/('install-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))

        def prepare_operation():
            phone = read_android(adb)
            if phone['ro.product.device'] != 'brax3' or phone['ro.product.model'] != 'BraX3':
                raise ValueError('This installation profile is only for BraX3')
            slot = subprocess.check_output([adb,'-s',phone['serial'],'shell','getprop','ro.boot.slot_suffix'],
                text=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0).strip()
            if slot != '_a':
                raise ValueError('This installation profile requires active slot A; slot switching is not implemented')
            plan, agent_path = prepare(package,expected,output)
            return plan,agent_path,phone

        def reviewed(result):
            plan, agent_path, phone = result
            answer = simpledialog.askstring('Review BaseOS installation',
                f"Phone: {phone['serial']}\nBaseOS 1.0 (August 26)\n"
                f"{plan['write_bytes']/1024**3:.2f} GiB will be backed up, written and verified.\n"
                'Apps and user data will be reset. Preloader and factory identities are preserved.\n\n'
                'Unplug and power off the phone. Type INSTALL to start listening, then reconnect USB. '
                'Leave it connected until verification finishes.',parent=app.root)
            if answer != 'INSTALL':
                monitor.emit('Installation canceled; no phone storage written.')
                return
            inspected.clear()
            app.root.after(100,lambda:run('Native BaseOS installation',
                lambda:connect(agent_path,location,
                    lambda agent,device:install(agent,device,plan,output,event),event=event,timeout=300),
                lambda result:app.status.set('All written ranges verified. Reboot requested; check Android setup. Records: '+str(output))))

        run('Reviewing native BaseOS package',prepare_operation,reviewed)

    app.action(box,'Review native BaseOS installation',review_install,advanced=True)
