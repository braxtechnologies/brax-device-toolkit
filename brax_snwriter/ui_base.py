"""Repair app window state, background tasks, logs and field controls."""
import json
import os
import subprocess
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from datetime import datetime, timezone
import uuid
from .help_content import FIELD_HELP


class ToolTip:
    def __init__(self, widget, text):
        self.widget,self.text,self.pending,self.popup=widget,text,None,None
        widget.bind('<Enter>',self.schedule,add='+')
        widget.bind('<FocusIn>',self.schedule,add='+')
        for event in ('<Leave>','<FocusOut>','<ButtonPress>','<Destroy>'):
            widget.bind(event,self.hide,add='+')

    def schedule(self,event=None):
        self.hide()
        self.pending=self.widget.after(550,self.show)

    def show(self):
        self.pending=None
        if not self.widget.winfo_exists():return
        self.popup=tk.Toplevel(self.widget)
        self.popup.wm_overrideredirect(True)
        self.popup.attributes('-topmost',True)
        ttk.Label(self.popup,text=self.text,wraplength=410,padding=10,relief='solid',borderwidth=1).pack()
        self.popup.update_idletasks()
        x=min(self.widget.winfo_rootx(),self.widget.winfo_screenwidth()-self.popup.winfo_reqwidth()-12)
        y=min(self.widget.winfo_rooty()+self.widget.winfo_height()+4,self.widget.winfo_screenheight()-self.popup.winfo_reqheight()-12)
        self.popup.geometry(f'+{max(0,x)}+{max(0,y)}')
        self.pending=self.widget.after(6000,self.hide)

    def hide(self,event=None):
        if self.pending:
            self.widget.after_cancel(self.pending);self.pending=None
        if self.popup:
            self.popup.destroy();self.popup=None


def data_directory():
    # Reuse existing records so upgrading never hides a user's recovery backups.
    previous = Path.home() / 'BraxStations' / 'identity-station'
    path = previous if previous.is_dir() else Path.home() / 'BraxDeviceToolkit'
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_record_path(root):
    return Path(root) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12])


class AppWindow:
    def __init__(self, root, title):
        self.root = root
        self.data = data_directory()
        self.settings = {}
        settings_path = self.data / 'settings.json'
        if settings_path.exists():
            try:
                self.settings = json.loads(settings_path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                self.settings = {}
        self.queue = queue.Queue()
        self.busy = False
        self.buttons = []
        self.fields = {}
        self.inputs = {}
        self.input_states = {}
        self.sections = {}
        self.tooltips = {}
        self.log_path = new_record_path(self.data/'activity').with_suffix('.txt')
        root.title(title)
        root.geometry('900x740')
        root.minsize(840, 700)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TButton', padding=(12, 7))
        style.configure('Title.TLabel', font=('Segoe UI', 18, 'bold'))
        style.configure('TNotebook.Tab', padding=(16, 8))
        self.body = ttk.Frame(root, padding=16)
        self.body.pack(fill='both', expand=True)
        root.after(80, self.drain)


    def open_records(self):
        try:
            os.startfile(str(self.data))
        except OSError as exc:
            self.append(str(exc))

    def save_log(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='Save activity log',
                    defaultextension='.txt', filetypes=[('Text log','*.txt')])
        if path:
            try:
                Path(path).write_text(self.log.get('1.0','end-1c'), encoding='utf-8')
                self.status.set('Activity log saved')
                self.append({'activity_log_saved':path})
            except OSError as exc:
                self.append(str(exc))

    def choose_adb(self):
        path = filedialog.askopenfilename(parent=self.root, title='Choose adb.exe', filetypes=[('ADB executable','adb.exe')])
        if path:
            self.fields['adb'].set(path)


    def set_device_choices(self, result):
        serials = [line.split()[0] for line in result['adb'].splitlines()
                   if len(line.split()) > 1 and line.split()[1] == 'device'
                   and not line.startswith('emulator-') and 'product:lineage_' not in line]
        self.inputs['serial'].configure(values=serials)
        # Discovery never selects a phone or changes its identity automatically.

    def field(self, key, label, default='', choices=None):
        row = self.form.grid_size()[1]
        ttk.Label(self.form, text=label).grid(row=row, column=0, sticky='w', pady=4, padx=(0, 12))
        # Never persist a phone selection or a writable identity across runs.
        if key == 'adb' and isinstance(self.settings, dict):
            saved = self.settings.get('adb')
            if isinstance(saved, str) and saved:
                default = saved
        variable = tk.StringVar(value=default)
        state = 'readonly' if choices is not None else 'normal'
        widget = (ttk.Combobox(self.form, textvariable=variable, values=choices or [], state=state)
                  if choices is not None or key in ('serial','port','usb') else ttk.Entry(self.form, textvariable=variable))
        widget.grid(row=row, column=1, sticky='ew', pady=4)
        self.fields[key] = variable
        if key=='serial':
            def changed(*args):
                for name in ('port','usb'):
                    if name in self.fields:self.fields[name].set('')
                if hasattr(self,'pending_serial'):self.pending_serial=None
            variable.trace_add('write',changed)
        self.inputs[key] = widget
        self.input_states[key] = state
        self.tooltips[key]=ToolTip(widget,FIELD_HELP[key])
        return widget


    def values(self):
        return {key: value.get().strip() for key, value in self.fields.items()}

    def emit(self, value):
        self.queue.put(('log', value))

    def run(self, label, operation, on_success=None):
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.configure(state='disabled')
        for widget in self.inputs.values():
            widget.configure(state='disabled')
        self.progress.start(15)
        self.status.set(label + '…')
        self.append({'operation':label,'state':'started','at':datetime.now(timezone.utc).isoformat()})
        def worker():
            try:
                result = operation()
                self.queue.put(('success', (result, on_success)))
            except Exception as exc:
                detail=f'{type(exc).__name__}: {exc}'
                if isinstance(exc,subprocess.CalledProcessError) and exc.stderr:
                    detail+='\n'+(exc.stderr.decode(errors='replace') if isinstance(exc.stderr,bytes) else str(exc.stderr))
                if isinstance(exc,FileNotFoundError):
                    detail+='\nUse Choose ADB to select platform-tools/adb.exe; verify that selected files exist.'
                self.queue.put(('error',detail))
            finally:
                self.queue.put(('done', None))
        threading.Thread(target=worker, daemon=False).start()

    def drain(self):
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == 'done':
                    self.busy = False
                    for button in self.buttons:
                        button.configure(state='normal')
                    for key, widget in self.inputs.items():
                        widget.configure(state=self.input_states[key])
                    self.progress.stop()
                elif kind == 'success':
                    result, callback = value
                    self.append(result)
                    self.status.set('Finished • see result below')
                    if callback:
                        try:
                            callback(result)
                        except Exception as exc:
                            self.status.set('Operation finished; display update failed')
                            self.append(str(exc))
                elif kind == 'error':
                    self.last_error = value
                    self.status.set('Stopped • '+value.splitlines()[0][:145])
                    self.append(value)
                else:
                    self.append(value)
        except queue.Empty:
            pass
        self.root.after(80, self.drain)

    def append(self, value):
        text = value if isinstance(value, str) else json.dumps(value, indent=2, default=str)
        try:
            self.log_path.parent.mkdir(parents=True,exist_ok=True)
            with self.log_path.open('a',encoding='utf-8') as f:f.write(text+'\n\n')
        except OSError:
            pass
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def close(self):
        if self.busy:
            self.status.set('An operation is active. Wait for its recorded outcome before closing.')
        else:
            if 'adb' in self.fields:
                try:
                    temporary = self.data / 'settings.json.tmp'
                    settings = {'adb': self.fields['adb'].get()}
                    if hasattr(self, 'firmware_tool'):
                        settings['sp_flash_tool'] = self.firmware_tool.get()
                    temporary.write_text(json.dumps(settings), encoding='utf-8')
                    temporary.replace(self.data / 'settings.json')
                except OSError as exc:
                    self.append(f'Could not save ADB setting: {exc}')
            self.root.destroy()
