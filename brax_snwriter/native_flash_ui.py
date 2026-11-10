"""Experimental native loader inspection in the existing firmware page."""
from datetime import datetime
import re
import tkinter as tk
from tkinter import filedialog, ttk


def build_native_controls(app, parent, run, monitor):
    from .native_flash import connect, save_exclusive
    import json

    box = ttk.LabelFrame(parent, text='Native Python connection test', padding=12)
    box.pack(fill='x', pady=12)
    ttk.Label(box, text='Loads the inspected stock Download Agent into RAM, reads storage and partitions, '
              'then requests a reboot. Does not write or erase phone storage. No SP Flash Tool executable '
              'or DLL is used. A compatible vendor Download Agent and USB driver are still required.',
              wraplength=800).pack(anchor='w')
    path = tk.StringVar()
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

        def event(message):
            match = re.fullmatch(r'(Read|Wrote) (\d+)/(\d+) bytes', message)
            percent = 100*int(match[2])/int(match[3]) if match else None
            monitor.emit(message, percent)

        def operation(agent, inventory):
            output.mkdir(parents=True, exist_ok=False)
            save_exclusive(output/'inventory.json', json.dumps(inventory, indent=2).encode())
            return inventory

        app.status.set('Listening: reconnect the powered-off phone now.')
        run('Native storage inspection',
            lambda: connect(agent_path, location, operation, event=event),
            lambda result: app.status.set(
                f"Read {len(result['partitions'])} partitions. Saved: {output}. "
                'Reboot requested; check that Android starts. No storage written.'))

    app.action(box, 'Read storage using Python', inspect, advanced=True)
