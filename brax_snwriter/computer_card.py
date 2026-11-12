"""The "This computer" card: shows computer_check results and how to fix them."""
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk, filedialog
import webbrowser

from . import computer_check as checks

MARKS = {checks.OK: ('✓', '#1E7B45'), checks.MISSING: ('✕', '#B83A21'), checks.WARNING: ('!', '#B83A21'),
         checks.OPTIONAL: ('–', '#6B6B70'), checks.UNKNOWN: ('?', '#6B6B70')}


class ComputerCard:
    def __init__(self, app, parent, run_checks=checks.run_checks):
        self.app, self.run_checks = app, run_checks
        self.results, self.pending, self.running = [], None, False
        self.expanded = tk.BooleanVar(value=False)
        self.headline = tk.StringVar(value='Not checked yet.')
        frame = ttk.Frame(parent, style='Card.TFrame', padding=20, relief='solid', borderwidth=1)
        frame.pack(fill='x', pady=(0, 16))
        top = ttk.Frame(frame, style='Card.TFrame')
        top.pack(fill='x')
        ttk.Label(top, text='This computer', style='Step.TLabel').pack(side='left')
        self.copy_button = ttk.Button(top, text='Copy report', command=self.copy_report, state='disabled')
        self.copy_button.pack(side='right')
        self.again_button = ttk.Button(top, text='Check again', command=lambda: self.start(network=True))
        self.again_button.pack(side='right', padx=8)
        ttk.Style(app.root).configure('Card.TCheckbutton', background='white')
        ttk.Checkbutton(top, text='Show details', variable=self.expanded, command=self.render,
                        style='Card.TCheckbutton').pack(side='right')
        self.headline_label = ttk.Label(frame, textvariable=self.headline, style='Card.TLabel', wraplength=800, justify='left')
        self.headline_label.pack(anchor='w', pady=(6, 0))
        self.rows = ttk.Frame(frame, style='Card.TFrame')
        self.frame, self.wrap = frame, 760
        frame.bind('<Configure>', self.resized)

    def watch_adb(self):
        """Called once the ADB field exists. A path typed by hand is unknown
        until the next check, so the old result stops locking step 1."""
        def changed(*_):
            if not self.running and self.app.adb_ready is False:
                self.app.adb_ready = None
                self.headline.set('ADB path changed. Check again to test it.')
                self.app.refresh_gates()
        self.app.fields['adb'].trace_add('write', changed)

    def resized(self, event):
        self.wrap = max(300, event.width-48)
        self.headline_label.configure(wraplength=self.wrap)
        for label in getattr(self, 'message_labels', []):
            label.configure(wraplength=max(200, self.wrap-40))

    def start(self, network=False):
        """Run the checks off the window thread; values are read here, on it."""
        if self.running:
            return
        app = self.app
        arguments = (app.fields['adb'].get().strip(), app.data, app.native_agent.get().strip(),
                     app.firmware_tool.get().strip() if hasattr(app, 'firmware_tool') else '')
        self.running, self.pending = True, None
        self.headline.set('Checking this computer…')
        self.again_button.configure(state='disabled')
        def worker():
            try:
                self.pending = self.run_checks(*arguments, network=network)
            except Exception as exc:  # never leave the card stuck on "Checking"
                self.pending = exc
        threading.Thread(target=worker, daemon=True).start()
        self.poll()

    def poll(self):
        if self.pending is None:
            self.app.root.after(150, self.poll)
            return
        result, self.pending, self.running = self.pending, None, False
        self.again_button.configure(state='normal')
        if isinstance(result, Exception):
            self.headline.set(f'The check itself failed: {type(result).__name__}: {result}')
            return
        self.apply(result)

    def apply(self, results):
        app = self.app
        self.results = list(results)
        adb = next((c for c in self.results if c.key == 'adb'), None)
        # Use the ADB that was found unless a session has already been reviewed with another one.
        if adb and adb.state == checks.OK and not app.busy and app.stage < 3:
            if app.fields['adb'].get().strip() != adb.detail:
                app.fields['adb'].set(adb.detail)
        app.adb_ready = None if adb is None else adb.state != checks.MISSING
        self.headline.set(checks.summary(self.results))
        self.copy_button.configure(state='normal')
        if checks.required_missing(self.results):
            self.expanded.set(True)
        self.render()
        app.append(checks.report(self.results))
        app.refresh_gates()

    def render(self):
        for child in self.rows.winfo_children():
            child.destroy()
        self.message_labels = []
        if not self.expanded.get() or not self.results:
            self.rows.pack_forget()
            return
        self.rows.pack(fill='x', pady=(10, 0))
        for check in self.results:
            mark, colour = MARKS[check.state]
            row = ttk.Frame(self.rows, style='Card.TFrame')
            row.pack(fill='x', pady=4)
            ttk.Label(row, text=mark, style='Card.TLabel', foreground=colour, width=2,
                      font=('Segoe UI', 11, 'bold')).pack(side='left', anchor='n')
            text = ttk.Frame(row, style='Card.TFrame')
            text.pack(side='left', fill='x', expand=True)
            ttk.Label(text, text=check.title, style='Card.TLabel', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
            label = ttk.Label(text, text=check.message, style='Card.TLabel', justify='left',
                              wraplength=max(200, self.wrap-40))
            label.pack(anchor='w')
            self.message_labels.append(label)
            fixes = ttk.Frame(text, style='Card.TFrame')
            for title, command in self.fixes(check):
                ttk.Button(fixes, text=title, command=command).pack(side='left', padx=(0, 8))
            if fixes.winfo_children():
                fixes.pack(anchor='w', pady=(4, 0))

    def fixes(self, check):
        if check.state == checks.OK or not check.fix:
            return []
        app = self.app
        if check.fix == 'adb':
            return [('Choose adb.exe…', app.choose_adb),
                    ('Open download page', lambda: webbrowser.open(checks.PLATFORM_TOOLS_PAGE))]
        if check.fix == 'driver':
            return [('Open Device Manager', self.open_device_manager)]
        if check.fix == 'agent':
            return [('Choose Download Agent…', self.choose_agent)]
        if check.fix == 'flash_tool':
            return [('Open Install OS', lambda: app.tabs.select(app.sections['firmware']))]
        if check.fix == 'records':
            return [('Open records folder', app.open_records)]
        return []

    def open_device_manager(self):
        try:
            os.startfile('devmgmt.msc')
        except OSError as exc:
            self.app.append(f'Could not open Device Manager: {exc}')

    def choose_agent(self):
        current = self.app.native_agent.get()
        path = filedialog.askopenfilename(parent=self.app.root, title='Choose stock DA_BR.bin',
                                          initialdir=str(Path(current).parent) if current else None,
                                          filetypes=[('Download Agent', '*.bin')])
        if path:
            self.app.native_agent.set(path)
            self.start()

    def copy_report(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(checks.report(self.results))
        self.headline.set(checks.summary(self.results)+' · Report copied.')
