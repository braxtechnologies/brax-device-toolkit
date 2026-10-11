"""Reviewed, explicit external-tool operations in the Install OS tab."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
from .flash_backend import MODES, prepare, execute


def build_flash_controls(app, parent, run, monitor):
    box = ttk.LabelFrame(parent, text='Flash / format using external SP Flash Tool', padding=12)
    box.pack(fill='x', pady=12)
    ttk.Label(box, text='Compatibility: not verified. Flash and format commands are blocked until exact phone identity and an approved release can be checked. A matching chip is insufficient.', wraplength=800).pack(anchor='w', pady=8)
    ttk.Label(box, text='Requires your installed SPFlashToolV6.exe and an extracted BraX3 SP Flash package. '
              'These operations use the vendor tool, not a standalone Python writer. '
              'Download Only has a successful live test; the other modes have automated integration tests only.', wraplength=800).pack(anchor='w')
    tool = app.firmware_tool
    manifest = tk.StringVar()
    mode = tk.StringVar(value='Download Only')
    explanation = tk.StringVar(value=MODES['download'][1])
    app.flash_fields = {'tool': tool, 'manifest': manifest, 'mode': mode}
    for title, variable, pattern in [('SP Flash Tool executable', tool, '*.exe'), ('Extracted package: download_agent/flash.xml', manifest, '*.xml')]:
        ttk.Label(box, text=title).pack(anchor='w', pady=(8, 2))
        row = ttk.Frame(box)
        row.pack(fill='x')
        ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)
        def browse(v=variable, pat=pattern):
            path = filedialog.askopenfilename(parent=app.root, filetypes=[('Select file', pat)])
            if path:
                v.set(path)
        ttk.Button(row, text='Browse...', command=browse).pack(side='right', padx=6)
    ttk.Label(box, text='Operation').pack(anchor='w', pady=(8, 2))
    ttk.Combobox(box, textvariable=mode, values=[v[0] for v in MODES.values()], state='readonly').pack(fill='x')
    keys = {value[0]: key for key, value in MODES.items()}
    mode.trace_add('write', lambda *_: explanation.set(MODES[keys[mode.get()]][1]))
    ttk.Label(box, textvariable=explanation, wraplength=800).pack(anchor='w', pady=8)
    ttk.Label(box, text='First check the powered-off connection above, then unplug the phone. Review the operation, '
              'start the listener, and reconnect that same powered-off phone. Keep other phones disconnected. '
              'The selected COM port identifies the connection, not a cryptographic device identity.', wraplength=800).pack(anchor='w', pady=6)

    def review():
        if app.busy:
            return
        snapshot = (tool.get(), manifest.get(), keys[mode.get()], dict(getattr(app, 'firmware_device', {})))
        def reviewed(plan):
            # The dialog names the immutable snapshot even if a field was edited during hashing.
            text = (f"Operation: {MODES[plan['mode']][0]}\n"
                    f"Phone connection: {plan['device']['port']} / USB {plan['device']['usb_location']}\n"
                    f"Package: {Path(plan['command'][4]).parent.parent.name}\n"
                    f"Selected images: {len(plan['partitions'])}\n\n{MODES[plan['mode']][1]}\n\n"
                    'Factory backups and independent publisher signature are not verified by this adapter. '
                    'For erase-capable modes, have a verified factory recovery procedure and backups before proceeding.\n\n'
                    f"Type {plan['confirmation']} to start this operation, or Cancel.")
            answer = simpledialog.askstring('Review phone operation', text, parent=app.root)
            if answer != plan['confirmation']:
                monitor.emit('Operation canceled; no vendor process started.')
                return
            # The UI posts success before its done event; schedule after the queue drains.
            def launch():
                app.firmware_device = {}
                def finished(result):
                    message = ('Format completed; no OS installed. Install compatible firmware before booting.'
                               if plan['mode'] == 'format' else 'Vendor operation completed. Verify first boot and the installed OS separately.')
                    app.status.set(message)
                    monitor.emit(message)
                    app.firmware_device = {}
                run('Running '+MODES[plan['mode']][0],
                    lambda: execute(plan, answer, app.data/'flashes', monitor.emit), finished)
            app.root.after(100, launch)
        run('Preparing operation review', lambda: prepare(*snapshot), reviewed)
    app.action(box, 'Review flash / format operation', review, advanced=True)
