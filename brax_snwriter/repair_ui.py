"""Guided serial, IMEI and factory address repair interface."""
import json
import os
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
from .repair_records import probe_identity
import subprocess
import re
from .device_connection import NO_WINDOW
from .ui_base import AppWindow, new_record_path
from .serial_record import validate_serial, change_serial, patch_serial, PRODUCT_LID
from .imei import validate_imei, write_assigned, inspect_imeis
from .session import MetaSession, open_serial
from .modem import ModemSession
from . import mac_records
from .discovery import check_connection, adb_states, debugging_guidance
from .device_connection import boot_meta, return_normal

TITLE = 'Brax Serial and IMEI Repair Tool'


class RepairApp(AppWindow):
    def __init__(self, root):
        super().__init__(root, TITLE)
        self.buttons, self.fields, self.inputs, self.input_states = [], {}, {}, {}
        self.tooltips, self.sections = {}, {}
        self.pending_serial = None
        self.stage = 0
        self.recovery_required = False
        self.reviewed = None
        self.step_widgets = [[] for _ in range(5)]
        self.field_steps = {}
        self.step_labels = []
        self.summaries = []
        self.advanced_buttons = []
        self.action_messages = {}
        self.active_action_message = None
        self.confirm = tk.BooleanVar(value=False)
        self.checked_phone = tk.StringVar(value='No phone checked yet.')
        self.confirm.trace_add('write', lambda *_: self.refresh_gates())
        root.geometry('1120x900')
        root.minsize(860, 680)
        style = ttk.Style(root)
        style.configure('TFrame', background='#F5F4F7')
        style.configure('TLabel', background='#F5F4F7', foreground='#171717', font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'))
        style.configure('TNotebook.Tab', padding=(16, 9), font=('Segoe UI', 10))
        style.map('TNotebook.Tab',
                  padding=[('selected', (22, 15)), ('!selected', (16, 9))],
                  font=[('selected', ('Segoe UI', 11, 'bold')), ('!selected', ('Segoe UI', 10))],
                  foreground=[('selected', '#171717')],
                  background=[('selected', '#E65035'), ('!selected', '#E4E2E6')],
                  expand=[('selected', (2, 4, 2, 0)), ('!selected', (0, 0, 0, 0))])
        style.configure('Card.TFrame', background='white')
        style.configure('Card.TLabel', background='white', foreground='#171717')
        style.configure('Step.TLabel', background='white', font=('Segoe UI', 13, 'bold'))
        style.configure('Primary.TButton', background='#E65035', foreground='#171717', padding=(16, 10))
        style.map('Primary.TButton', background=[('disabled', '#e5ebe7'), ('active', '#D44528')], foreground=[('disabled', '#65786b')])
        self.brand_logo = tk.PhotoImage(master=root, file=str(Path(__file__).parent / 'assets' / 'brax-logo.png'))
        ttk.Label(self.body, image=self.brand_logo).pack(anchor='w', pady=(0, 12))
        ttk.Label(self.body, text=TITLE, style='Title.TLabel').pack(anchor='w')
        ttk.Label(self.body, text='Write serial, IMEIs and optional Bluetooth / Wi-Fi addresses in one guided session.').pack(anchor='w', pady=(6, 20))
        self.tabs = ttk.Notebook(self.body)
        self.tabs.pack(fill='both', expand=True)
        self.tabs.enable_traversal()
        guided, content = self.scroll_page('Serial & IMEI', 'guided')
        _, advanced = self.scroll_page('Advanced tools', 'advanced')
        log_page = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(log_page, text='Session log')
        self.sections['log'] = log_page
        ttk.Label(log_page, text='Session log', style='Title.TLabel').pack(anchor='w')
        ttk.Label(log_page, text='Live operation results and backup paths. This log is also saved to disk.').pack(anchor='w', pady=8)
        self.log = tk.Text(log_page, wrap='word', font=('Consolas', 10), state='disabled', height=14)
        bar = ttk.Scrollbar(log_page, command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.log.pack(fill='both', expand=True)
        self.status = tk.StringVar(value='Ready · select a phone to begin')
        self.status.trace_add('write', lambda *_: self.show_result(self.status.get()))
        status_row = ttk.Frame(self.body)
        status_row.pack(fill='x', pady=(10, 4))
        ttk.Button(status_row, text='View session log',
                   command=lambda: self.tabs.select(self.sections['log'])).pack(side='right')
        self.progress = ttk.Progressbar(self.body, mode='indeterminate')
        self.session_text = tk.StringVar(value='0 of 5 steps complete')
        ttk.Label(content, textvariable=self.session_text, foreground='#B83A21').pack(anchor='w', pady=(4, 12))
        card = self.card(content, 0, 'Connect through ADB',
            '1. On the phone, open Settings → About phone and tap Build number seven times. Enter your screen-lock code if asked.\n'
            '2. Open Settings → System → Developer options and enable USB debugging.\n'
            '3. Connect a USB data cable, unlock the phone, and accept “Allow USB debugging” for this computer.\n'
            '4. Click Refresh devices, select your phone, then Check connection. The next step opens only after the check succeeds.')
        self.add_field(card, 'serial', 'Connected ADB device', step=0, choices=[])
        self.action(card, 'Refresh devices', self.inventory, 0)
        self.action(card, 'Check connection', self.connect_guided, 0, primary=True)
        card = self.card(content, 1, 'Prepare for writing',
            'Keep the USB cable connected. Enter META mode to restart the selected phone into its service connection. The app checks the phone identity and supported firmware before continuing.')
        self.action(card, 'Enter META mode', self.prepare_guided, 1, primary=True)
        card = self.card(content, 2, 'Enter identifiers & addresses',
            'Enter the identifiers assigned to this phone from its production record. The serial must be 16 uppercase letters or digits. Both IMEIs are required and must be distinct, checksum-valid 15-digit numbers.')
        self.add_field(card, 'new', 'Assigned serial number', step=2)
        self.add_field(card, 'imei1', 'IMEI · SIM 1', step=2)
        self.add_field(card, 'imei2', 'IMEI · SIM 2', step=2)
        self.add_field(card, 'bluetooth', 'Bluetooth address (optional)', step=2)
        self.add_field(card, 'wifi', 'Wi-Fi address (optional)', step=2)
        ttk.Label(card, text='Leave an address empty to keep it unchanged. Use AA:BB:CC:DD:EE:FF format.', style='Card.TLabel').pack(anchor='w')
        self.action(card, 'Validate identifiers', self.validate_guided, 2, primary=True)
        card = self.card(content, 3, 'Review & write',
            'Compare all entered values with the production record. Keep the phone connected. The app backs up and verifies each write. If the serial already matches, it is verified without rewriting it.')
        self.review_text = tk.StringVar(value='Serial —\nIMEI 1 —\nIMEI 2 —')
        ttk.Label(card, textvariable=self.review_text, style='Card.TLabel', font=('Consolas', 11)).pack(anchor='w', pady=10)
        confirm = ttk.Checkbutton(card, text='I checked all entered identifiers against this phone’s assigned production record.', variable=self.confirm)
        confirm.pack(anchor='w', pady=8)
        self.step_widgets[3].append(confirm)
        self.write_button = self.action(card, 'Write & verify identifiers', self.write_guided, 3, primary=True)
        self.action(card, 'Correct identifiers', self.edit_guided, 3)
        card = self.card(content, 4, 'Restart & verify',
            'Restart into Android, then reconnect in META to read the stored serial, both IMEIs and any selected MAC records after reboot. The app compares them with this session and returns the phone to Android. Keep USB connected through both restarts.')
        self.action(card, 'Restart & verify identifiers', self.verify_guided, 4, primary=True)
        self.next_button = self.action(content, 'Start next phone', self.reset_guided)
        self.build_advanced(advanced)
        _, firmware = self.scroll_page('Install OS', 'firmware')
        from .firmware_ui import build_firmware
        build_firmware(self, firmware)
        self.fields['serial'].trace_add('write', self.selection_changed)
        for key in ('new', 'imei1', 'imei2', 'bluetooth', 'wifi'):
            self.fields[key].trace_add('write', self.identifiers_changed)
        root.bind('<F1>', lambda event: self.tabs.select(self.sections['advanced']))
        self.refresh_gates()
        self.append('Repair tool ready. No device is selected. Device operations run only when requested.')

    def scroll_page(self, title, key):
        page = ttk.Frame(self.tabs)
        self.tabs.add(page, text=title)
        self.sections[key] = page
        if key == 'firmware':
            from .firmware_guide import STEPS
            rail = ttk.Frame(page, padding=(20, 20), width=240)
            rail.pack(side='right', fill='y')
            ttk.Label(rail, text='Your installation', font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(0, 16))
            self.firmware_rail = []
            for step_title in STEPS:
                label = ttk.Label(rail, text=step_title, wraplength=190)
                label.pack(anchor='w', pady=12)
                self.firmware_rail.append(label)
            ttk.Separator(rail).pack(fill='x', pady=16)
            ttk.Label(rail, text='Complete each check to unlock the next step. Installation requires verified compatibility.', wraplength=190).pack(anchor='w')
        if key == 'guided':
            rail = ttk.Frame(page, padding=(20, 20), width=240)
            rail.pack(side='right', fill='y')
            ttk.Label(rail, text='Your session', font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(0, 16))
            self.rail_labels = []
            for title in ('Connect through ADB', 'Prepare connection', 'Enter identifiers', 'Review & write', 'Restart & verify'):
                label = ttk.Label(rail, text=title, wraplength=200)
                label.pack(anchor='w', pady=12)
                self.rail_labels.append((label, title))
            ttk.Separator(rail).pack(fill='x', pady=16)
            ttk.Label(rail, text='Complete each step to unlock the next.', wraplength=190).pack(anchor='w')
            ttk.Label(rail, textvariable=self.checked_phone, wraplength=190).pack(anchor='w', pady=(16, 0))
        canvas = tk.Canvas(page, background='#F5F4F7', highlightthickness=0)
        scroll = ttk.Scrollbar(page, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        content = ttk.Frame(canvas, padding=(6, 14, 16, 14))
        window = canvas.create_window(0, 0, anchor='nw', window=content)
        content.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        def wheel(event):
            widget = event.widget
            while widget:
                if widget == page:
                    canvas.yview_scroll(int(-event.delta / 120), 'units')
                    return 'break'
                widget = getattr(widget, 'master', None)
        self.root.bind('<MouseWheel>', wheel, add='+')
        if key == 'guided':
            self.guided_canvas = canvas
        return page, content

    def card(self, parent, index, title, instruction):
        frame = ttk.Frame(parent, style='Card.TFrame', padding=20, relief='solid', borderwidth=1)
        frame.pack(fill='x', pady=(0, 16))
        self.step_labels.append(tk.StringVar())
        ttk.Label(frame, text=f'{index+1:02d}   {title}', style='Step.TLabel').pack(anchor='w')
        ttk.Label(frame, textvariable=self.step_labels[-1], style='Card.TLabel', foreground='#B83A21').pack(anchor='w', pady=(4, 8))
        text = ttk.Label(frame, text=instruction, style='Card.TLabel', wraplength=850, justify='left')
        text.pack(fill='x', pady=(0, 12))
        frame.bind('<Configure>', lambda event: text.configure(wraplength=max(400, event.width-48)))
        self.summaries.append(tk.StringVar())
        ttk.Label(frame, textvariable=self.summaries[-1], style='Card.TLabel', wraplength=820).pack(anchor='w')
        return frame

    def add_field(self, parent, key, label, default='', step=None, choices=None):
        form = ttk.Frame(parent, padding=(0, 4))
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        self.form = form
        widget = self.field(key, label, default, choices=choices)
        self.field_steps[key] = step
        return widget

    def action(self, parent, label, callback, step=None, primary=False, advanced=False):
        row = ttk.Frame(parent, style='Card.TFrame' if step is not None else 'TFrame')
        row.pack(fill='x', pady=5)
        row.columnconfigure(1, weight=1)
        message = tk.StringVar(value='')
        self.action_messages[label] = message
        def invoke():
            if self.busy:
                return
            self.active_action_message = message
            message.set('')
            callback()
        button = ttk.Button(row, text=label, command=invoke, style='Primary.TButton' if primary else 'TButton')
        button.grid(row=0, column=0, sticky='nw')
        status_label = ttk.Label(row, textvariable=message, justify='left', wraplength=450,
                                style='Card.TLabel' if step is not None else 'TLabel')
        status_label.grid(row=0, column=1, sticky='nw', padx=(12, 0), pady=5)
        row.bind('<Configure>', lambda event: status_label.configure(
            wraplength=max(120, event.width-button.winfo_reqwidth()-20)))
        self.buttons.append(button)
        if step is not None:
            self.step_widgets[step].append(button)
        if advanced:
            self.advanced_buttons.append(button)
        return button

    def build_advanced(self, parent):
        from .advanced_ui import build_advanced
        build_advanced(self, parent)


    def resolve_adb(self):
        value = self.fields['adb'].get().strip().strip('"')
        if value:
            expanded = os.path.expandvars(os.path.expanduser(value))
            candidate = shutil.which(expanded)
            if candidate:
                self.fields['adb'].set(str(Path(candidate).resolve()))
            elif Path(expanded).is_absolute():
                self.fields['adb'].set(str(Path(expanded)))
            elif value.lower() in ('adb', 'adb.exe'):
                common = Path('C:/Program Files/platform-tools/adb.exe')
                if common.is_file():
                    self.fields['adb'].set(str(common.resolve()))
        self.update_adb_status()


    def update_adb_status(self):
        value = self.fields['adb'].get().strip()
        path = Path(value)
        found = path.is_absolute() and path.is_file() and path.name.lower() == 'adb.exe'
        self.adb_status.set('Found · adb.exe exists at this path' if found else 'Not found · browse to a valid adb.exe')

    def choose_adb(self):
        if self.busy or (self.stage in (3, 4) and not self.recovery_required):
            return
        path = filedialog.askopenfilename(parent=self.root, title='Choose Android platform-tools adb.exe', filetypes=[('ADB executable', 'adb.exe')])
        if path:
            self.fields['adb'].set(path)
            self.resolve_adb()
            self.show_result('ADB executable: '+self.fields['adb'].get()+'\n'+self.adb_status.get())

    def show_capabilities(self):
        capabilities = json.loads((Path(__file__).resolve().parent.parent/'capabilities.json').read_text())
        self.append(capabilities)
        dialog = tk.Toplevel(self.root)
        dialog.title('Full capability list')
        dialog.geometry('850x600')
        content = tk.Text(dialog, wrap='word', padx=18, pady=18)
        scroll = ttk.Scrollbar(dialog, command=content.yview)
        content.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y');content.pack(fill='both',expand=True)
        for item in capabilities['capabilities']:
            content.insert('end', item['name']+'\n'+item['status']+'\n\n')
        content.configure(state='disabled')
        self.status.set('Full capability list opened.')

    def selection_changed(self, *_):
        self.checked_phone.set('No phone checked yet.')
        self.current_mode = None
        if self.busy:
            return
        self.stage = 0
        self.reviewed = None
        self.confirm.set(False)
        self.refresh_gates()

    def identifiers_changed(self, *_):
        if self.stage == 3 and not self.busy:
            self.stage = 2
            self.reviewed = None
            self.confirm.set(False)
            self.refresh_gates()

    def refresh_gates(self):
        if not hasattr(self, 'write_button'):
            return
        for i, (label, title) in enumerate(self.rail_labels):
            label.configure(text=('✓' if i < self.stage else str(i+1))+'  '+title,
                            foreground='#B83A21' if i <= self.stage else '#55555A',
                            font=('Segoe UI', 10, 'bold' if i == self.stage else 'normal'))
        for i, controls in enumerate(self.step_widgets):
            for control in controls:
                control.configure(state='normal' if i == self.stage and not self.busy and not self.recovery_required else 'disabled')
            self.step_labels[i].set('Complete' if i < self.stage else 'Current step' if i == self.stage else f'Locked · finish step {i}')
        for key, widget in self.inputs.items():
            step = self.field_steps[key]
            enabled = not self.busy and (step is None or (step == self.stage and not self.recovery_required) or (step == 0 and self.recovery_required))
            # Reviewed setup is immutable until the operator goes back to identifiers.
            if step is None and self.stage in (3, 4) and not self.recovery_required:
                enabled = False
            widget.configure(state=self.input_states[key] if enabled else 'disabled')
        self.adb_browse.configure(state='disabled' if self.busy or (self.stage in (3, 4) and not self.recovery_required) else 'normal')
        if not self.confirm.get():
            self.write_button.configure(state='disabled')
        self.next_button.configure(state='normal' if self.stage == 5 and not self.busy else 'disabled')
        self.session_text.set(('Operation in progress · wait for verification' if self.busy else
                               'Verification or recovery required · check the latest operation result')
                              if self.recovery_required else
                              ('Session completed · identifiers verified after reboot' if self.stage==5 else f'{min(self.stage, 5)} of 5 steps complete'))
        if hasattr(self, 'normal_mode_button'):
            self.normal_mode_button.configure(state='normal' if not self.busy and self.current_mode=='meta' else 'disabled')
            self.meta_mode_button.configure(state='normal' if not self.busy and self.current_mode=='normal' else 'disabled')
            if self.current_mode:
                self.mode_detail.set('Current mode: '+('Normal · Android running' if self.current_mode=='normal' else 'META · Factory service mode')+'\n'+self.checked_phone.get()+'\nUSB: '+(self.fields['usb'].get() or 'not checked')+(' · Port: '+self.fields['port'].get() if self.fields['port'].get() else ''))
            elif not self.busy:
                self.mode_detail.set('Current mode: not checked. Detect the connected phone first.')
        if hasattr(self, 'firmware_guide'):
            self.firmware_guide.refresh()

    def show_result(self, text):
        if self.active_action_message is not None:
            self.active_action_message.set(text)

    def run(self, label, operation, on_success=None):
        if self.busy:
            return
        self.show_result(label+'…')
        def done(result):
            if isinstance(result, dict) and 'adb' in result:
                devices = result['adb'].strip()
                ports = result.get('ports', [])
                port_text = ', '.join(p['port'] for p in ports) or 'None detected'
                self.show_result(devices+'\nMETA / serial ports: '+port_text)
            else:
                self.show_result(json.dumps(result, indent=2, default=str) if not isinstance(result, str) else result)
            self.status.set(label+' · complete. Details in Session log.')
            if on_success:
                on_success(result)
            if isinstance(result, dict) and 'adb' in result:
                states = {s: state for s, state in adb_states(result['adb']).items() if not s.startswith('emulator-')}
                messages = [s+': '+('Ready to check. Select this phone and click Check connection.'
                            if state == 'device' else debugging_guidance(state)) for s, state in states.items()]
                self.status.set('\n'.join(messages) if messages else debugging_guidance(None))
        super().run(label, operation, done)

    def drain(self):
        super().drain()
        if self.status.get().startswith('Stopped •'):
            if self.checked_phone.get() == 'Checking phone and OS…':
                self.checked_phone.set('Phone and OS not verified. Check the connection again.')
            detail = getattr(self, 'last_error', self.status.get())
            self.status.set('Stopped')
            self.show_result(detail)
        self.refresh_gates()

    def require_stage(self, stage):
        if self.busy or self.recovery_required or self.stage != stage:
            raise ValueError('Complete the preceding step before this action.')

    def complete(self, stage, summary):
        self.summaries[stage].set('✓ '+summary)
        self.stage = stage+1
        self.refresh_gates()
        self.status.set(summary)

    def connect_guided(self):
        self.require_stage(0)
        v = self.values()
        self.checked_phone.set('Checking phone and OS…')
        def done(result):
            self.fields['usb'].set(result['usb_location'])
            self.show_checked_phone(result)
            self.current_mode = 'normal'
            self.complete(0, 'USB debugging connected and authorized ✓ · Phone, OS and USB location checked: '+v['serial'])
        def operation():
            return check_connection(v['adb'], v['serial'])
        self.run('Checking selected phone', operation, done)

    def show_checked_phone(self, result):
        from .serial_record import BASEOS_FINGERPRINT
        if result.get('fingerprint') != BASEOS_FINGERPRINT:
            self.checked_phone.set('Phone and OS not verified.')
            return
        android_version = result['fingerprint'].split(':')[1].split('/')[0]
        build = result['fingerprint'].split(':')[1].split('/')[-1]
        self.checked_phone.set(f"Checked phone: BraX3\nSerial: {result['serial']}\nOS: BaseOS · Android {android_version} · build {build}")

    def prepare_guided(self):
        self.require_stage(1)
        v = self.values()
        def operation():
            location = check_connection(v['adb'], v['serial'])['usb_location']
            if location != v['usb']:
                raise ValueError('USB location changed; reconnect and start again.')
            result = boot_meta(v['adb'], v['serial'], location, timeout=90, report=self.emit)
            result['identity'] = probe_identity(result['port'], v['serial'])
            with open_serial(result['port']) as stream:
                _, records, values = inspect_imeis(ModemSession(stream, timeout=20), v['serial'])
            blank_slots = []
            for slot in (1, 2):
                if records[slot] == b'\xff' * 10:
                    blank_slots.append(slot)
                elif not values[slot]['valid']:
                    raise ValueError(f'SIM {slot} stored IMEI has an unsupported format. No write attempted; inspect the modem records.')
            result['empty_imei_slots'] = blank_slots
            return result
        def done(result):
            self.fields['port'].set(result['port'])
            self.current_mode = 'meta'
            blank = result['identity']['serial_field'] == ''
            self.fields['blank'].set('Yes' if blank else 'No')
            empty_slots = result['empty_imei_slots']
            self.fields['imei_blank'].set('Yes' if empty_slots else 'No')
            summary = ('Stored serial is empty. Your new serial will initialize it after review.'
                       if blank else 'Existing stored serial read. Your new serial will replace it after review.')
            if empty_slots:
                summary += ' Uninitialized IMEI storage detected for SIM '+', '.join(map(str, empty_slots))+'. Your new IMEIs will initialize those records after review.'
            self.complete(1, 'Phone checked on '+result['port']+'. '+summary)
        self.run('Preparing META connection', operation, done)

    def validate_guided(self):
        self.require_stage(2)
        try:
            v = self.values()
            validate_serial(v['new'])
            values = [validate_imei(v[k]) for k in ('imei1', 'imei2')]
            if values[0] == values[1]:
                raise ValueError('SIM 1 and SIM 2 must have distinct IMEIs.')
            for key in ('bluetooth', 'wifi'):
                if v[key]: mac_records.parse_address(v[key])
            self.reviewed = v.copy()
            serial_action = 'Initialize empty serial field' if v['blank']=='Yes' else 'Update existing serial field'
            self.review_text.set(f"New serial   {v['new']}\nNew IMEI 1   {v['imei1']}\nNew IMEI 2   {v['imei2']}\n{serial_action}\nInitialize empty IMEI records: {v['imei_blank']}")
            self.review_text.set(self.review_text.get()+'\nBluetooth: '+(v['bluetooth'] or 'unchanged')+'\nWi-Fi: '+(v['wifi'] or 'unchanged'))
            self.confirm.set(False)
            self.complete(2, 'All entered identifier formats validated')
        except ValueError as exc:
            self.status.set(str(exc))
            self.append({'validation_error': str(exc)})

    def edit_guided(self):
        self.require_stage(3)
        self.stage = 2
        self.reviewed = None
        self.confirm.set(False)
        self.refresh_gates()

    def write_guided(self):
        self.require_stage(3)
        if not self.confirm.get() or self.values() != self.reviewed:
            self.status.set('Review and confirm the current values before writing.')
            return
        v = self.reviewed.copy()
        path = new_record_path(self.data / 'guided-sessions')
        # Lock out a second attempt even if a device operation raises midway.
        self.recovery_required = True
        self.refresh_gates()
        def operation():
            path.mkdir(parents=True, exist_ok=False)
            (path/'session.json').write_text(json.dumps(v, indent=2), encoding='utf-8')
            self.emit({'session_records': str(path), 'state': 'preflight'})
            self.validate_meta_target(v)
            with open_serial(v['port']) as stream:
                s = MetaSession(stream)
                # Validate serial record before changing either identity subsystem.
                probe = (s.property('ro.vendor.serialno'), s.property('ro.build.fingerprint'))
                from .serial_record import BASEOS_FINGERPRINT
                if probe != (v['serial'], BASEOS_FINGERPRINT):
                    raise ValueError('Selected phone identity/profile changed.')
                mac_changes = {k:v[k] for k in ('bluetooth','wifi') if v[k]}
                mac_review = mac_records.plan(mac_records.capture(s, v['serial']), mac_changes) if mac_changes else None
                record = s.read_record(PRODUCT_LID)
                if v['new'] != v['serial']:
                    patch_serial(record, v['serial'], v['new'], initialize_blank=v['blank']=='Yes')
                elif record[116:136].split(b'\0', 1)[0] != v['new'].encode('ascii'):
                    raise ValueError('Stored serial differs; reconcile before writing.')
            with open_serial(v['port']) as stream:
                modem = write_assigned(ModemSession(stream, timeout=20), v['serial'],
                    [v['imei1'], v['imei2']], path/'imei', initialize_blank=v['imei_blank']=='Yes')
            self.emit({'imei_write': modem})
            if mac_review:
                with open_serial(v['port']) as stream:
                    mac_records.write_reviewed(MetaSession(stream), v['serial'], mac_review, path/'mac')
            if v['new'] != v['serial']:
                with open_serial(v['port']) as stream:
                    serial = change_serial(MetaSession(stream), v['serial'], v['new'], path/'serial', initialize_blank=v['blank']=='Yes')
            else:
                serial = {'status': 'already_matches', 'record_readback_verified': True}
            result = {'serial': serial, 'imei': modem, 'record': str(path), 'reboot_persistence_verified': False}
            (path/'write-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            return result
        def done(result):
            self.session_record = path
            self.pending_serial = v['new']
            self.recovery_required = False
            self.complete(3, 'All selected identifiers verified in META. Reboot verification still required.')
        self.run('Backing up, writing and checking identifiers', operation, done)

    def verify_guided(self):
        self.require_stage(4)
        v = self.reviewed.copy()
        self.recovery_required = True
        self.refresh_gates()
        def operation():
            return_normal(v['adb'], v['port'], v['serial'], expected_after=v['new'], report=self.emit)
            connection = check_connection(v['adb'], v['new'])
            if connection['usb_location'] != v['usb']:
                raise ValueError('USB location changed after reboot; reconcile the selected phone.')
            meta = boot_meta(v['adb'], v['new'], v['usb'], timeout=90, report=self.emit)
            serial = probe_identity(meta['port'], v['new'])
            if serial['serial_field'] != v['new']:
                raise ValueError('Persisted serial differs from the assigned serial.')
            with open_serial(meta['port']) as stream:
                _, _, imeis = inspect_imeis(ModemSession(stream, timeout=20), v['new'])
            if any(imeis[i].get('imei') != v[f'imei{i}'] for i in (1, 2)):
                raise ValueError('Persisted IMEI readback differs. See backups before retrying.')
            if any(v[k] for k in ('bluetooth','wifi')):
                transaction = json.loads((self.session_record/'mac'/'mac-transaction.json').read_text())
                with open_serial(meta['port']) as stream:
                    actual = mac_records.capture(MetaSession(stream), v['new'])
                if actual['records'] != transaction['after']['records']:
                    raise ValueError('MAC records did not persist. Preserve session backups before recovery.')
            normal = return_normal(v['adb'], meta['port'], v['new'], report=self.emit)
            result = {'serial': serial, 'imeis': {i: imeis[i] for i in (1, 2)}, 'normal': normal, 'reboot_persistence_verified': True, 'mac_records_verified': any(v[k] for k in ('bluetooth','wifi'))}
            (self.session_record/'reboot-verified.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            return result
        def done(result):
            self.fields['serial'].set(v['new'])
            self.show_checked_phone(result['serial'])
            self.current_mode = 'normal'
            self.fields['port'].set('')
            self.pending_serial = None
            self.recovery_required = False
            self.complete(4, 'All selected identifiers persisted after reboot. Phone returned to Android.')
        self.run('Restarting and verifying persisted identifiers', operation, done)

    def reset_guided(self):
        if self.busy:
            return
        self.stage = 0
        self.reviewed = None
        self.pending_serial = None
        self.recovery_required = False
        for key in ('serial', 'new', 'imei1', 'imei2', 'bluetooth', 'wifi', 'usb', 'port'):
            self.fields[key].set('')
        self.fields['blank'].set('No')
        self.fields['imei_blank'].set('No')
        self.confirm.set(False)
        for summary in self.summaries:
            summary.set('')
        self.append('New guided session. Previous operation results remain in the session log and on disk.')
        self.tabs.select(self.sections['guided'])
        self.guided_canvas.yview_moveto(0)
        self.refresh_gates()

    @staticmethod
    def validate_meta_target(v):
        if not v['serial']:
            raise ValueError('No phone selected. If the phone is already in META mode, use Detect META phone in Advanced tools first.')
        validate_serial(v['serial'])
        if not re.fullmatch(r'COM[1-9][0-9]*',v['port'],re.I):
            raise ValueError('No META port selected. In Device: check the BaseOS phone and USB, then Enter factory META mode. Normal Android has no META COM port.')

    def inventory(self):
        v = self.values()
        def operation():
            from serial.tools import list_ports
            devices = subprocess.check_output([v['adb'], 'devices', '-l'], text=True, timeout=10,creationflags=NO_WINDOW)
            return {'adb': devices, 'ports': [{'port': p.device, 'location': p.location,
                    'vid': p.vid, 'pid': p.pid} for p in list_ports.comports()]}
        def selected(result):
            self.set_device_choices(result)
            if 'port' in self.inputs:
                self.inputs['port'].configure(values=[p['port'] for p in result['ports']])
            if 'usb' in self.inputs:
                self.inputs['usb'].configure(values=sorted({p['location'].split(':')[0] for p in result['ports'] if p['location']}))
        self.run('Listing devices', operation, selected)
