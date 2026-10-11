"""Sequential OS installation guidance; no UI setting bypasses compatibility checks."""
import os
import subprocess
import tkinter as tk
from tkinter import ttk
from . import firmware_catalog as catalog
from .discovery import adb_states
from .flash_plan import review_package
from .compatibility import require_verified_compatibility


STEPS = ('Check your phone', 'Choose and download an OS', 'Review changes',
         'Turn off and reconnect', 'Install the OS', 'Check first startup')
MODE_HELP = {
    'Download Only': 'Installs the files included in the OS package. “Download” means writing to the phone, not just saving a file on your computer. Your apps, photos and settings can be erased if the package includes a user-data image. It is not a keep-my-data option.',
    'Firmware Upgrade': 'Installs firmware and can change how the phone’s storage is divided. Treat this as erasing your personal data. Use only an upgrade approved for this exact phone. Preservation of factory records has not been validated here.',
    'Format Only': 'Erases storage without installing an OS. The phone may no longer start. Factory identity and radio calibration records may also be lost. This is a service operation, not a normal OS installation.',
    'Format + Download': 'Erases storage first, then installs firmware. Personal data and factory records may be lost. Installing an OS does not recreate missing identity or calibration records. Requires a tested factory recovery procedure.',
}


def read_android(adb):
    """Observe one authorized USB phone without rebooting or assuming model trust."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    def call(*args):
        return subprocess.check_output([adb, *args], text=True, stderr=subprocess.PIPE,
                                       timeout=15, creationflags=flags).strip()
    states = adb_states(call('devices', '-l'))
    # Require a single USB transport: neither emulators nor network ADB qualify.
    if len(states) != 1:
        raise ValueError('Connect one phone by USB. If none appears, enable USB debugging and check the cable and driver.')
    serial, state = next(iter(states.items()))
    if state == 'unauthorized':
        raise ValueError('Unlock the phone and accept “Allow USB debugging”, then check again.')
    if state != 'device' or serial.startswith('emulator-') or ':' in serial:
        raise ValueError('Start Android normally and connect the phone by USB. Use Advanced recovery if Android cannot start.')
    # Windows ADB may report get-devpath as "unknown" even over physical USB.
    # -d selects a USB transport in ADB itself and rejects network transports.
    if call('-d', 'get-serialno') != serial:
        raise ValueError('Use a USB cable; wireless ADB cannot identify the cable to reconnect.')
    props = {name: call('-s', serial, 'shell', 'getprop', name) for name in
             ('ro.product.model', 'ro.product.device', 'ro.build.fingerprint',
              'ro.build.version.release', 'ro.lineage.version', 'ro.bootmode', 'sys.boot_completed')}
    if props['sys.boot_completed'] != '1' or props['ro.bootmode'] not in ('normal', ''):
        raise ValueError('Android has not finished starting normally. Wait for the home or setup screen, then check again.')
    if not all(props[name] for name in ('ro.product.model', 'ro.product.device', 'ro.build.fingerprint')):
        raise ValueError('The phone did not report its model and OS. No compatibility decision was made.')
    return {'serial': serial, **props}


class FirmwareGuide:
    def __init__(self, app, parent, run):
        self.app, self.run = app, run
        self.stage, self.phone, self.downloaded, self.plan = 0, None, None, None
        self.controls, self.labels, self.results = [], [], []
        self.packages = {}
        self.selected = tk.StringVar()
        self.mode = tk.StringVar(value='Download Only')
        self.ack = tk.BooleanVar(value=False)
        self.summary = tk.StringVar(value='No phone checked. Start with Android running.')
        ttk.Label(parent, text='Install an OS', style='Title.TLabel').pack(anchor='w')
        ttk.Label(parent, text='Follow the numbered steps. Each check unlocks the next step. Nothing is written while checking or downloading.', wraplength=850).pack(anchor='w', pady=(6, 10))
        ttk.Label(parent, text='This build cannot yet verify exact phone-to-release compatibility. You can check the phone and prepare a package; installation stops at the review. Keep the phone on until that check passes.', foreground='#9B351F', wraplength=850).pack(anchor='w', pady=(0, 12))
        self.rail = ttk.Label(parent, textvariable=self.summary, wraplength=850)
        self.rail.pack(anchor='w', pady=(0, 12))
        cards = []
        instructions = (
            'Keep the phone on. Open Settings → About phone and tap Build number seven times. In Developer options, enable USB debugging. Connect one phone with a USB data cable, unlock it and accept “Allow USB debugging”. Then check below.',
            'Load the BraX3 download catalog, choose an SP Flash package, then download it. The checksum check detects a damaged download; it does not prove the package is approved for your exact phone.',
            'Read what the operation will change. Save your photos, files and account recovery details elsewhere first. Factory-record backups from the repair tool do not back up your apps or photos.',
            'After compatibility is approved: unplug USB, use the phone’s power menu to power it off, and wait for the screen to go dark. Start the listener here BEFORE reconnecting USB. Reconnect the same powered-off phone and keep the cable attached. META mode is not used for flashing.',
            'Review the exact phone, OS and erase operation before confirming. Leave the cable connected and wait for the tool to finish. A transfer reaching 100% does not mean the whole installation is finished.',
            'Start the phone if it only shows a charging screen. First startup can take several minutes. Complete setup, enable USB debugging again if needed, and check the installed OS. A completed flash alone does not verify startup.',
        )
        for index, (title, instruction) in enumerate(zip(STEPS, instructions)):
            card = ttk.Frame(parent, padding=20, style='Card.TFrame', relief='solid', borderwidth=1)
            card.pack(fill='x', pady=(0, 16)); cards.append(card)
            ttk.Label(card, text=f'{index+1:02d}   {title}', style='Step.TLabel').pack(anchor='w')
            state = tk.StringVar(); self.labels.append(state)
            ttk.Label(card, textvariable=state, style='Card.TLabel', foreground='#B83A21').pack(anchor='w', pady=(4, 8))
            label = ttk.Label(card, text=instruction, style='Card.TLabel', wraplength=800)
            label.pack(fill='x', pady=(0, 12))
            card.bind('<Configure>', lambda event, label=label: label.configure(wraplength=max(300, event.width-48)))
            result = tk.StringVar(); self.results.append(result)
            ttk.Label(card, textvariable=result, style='Card.TLabel', wraplength=800).pack(anchor='w', pady=6)
        self.button(cards[0], 'Check phone and current OS', 0, self.check_phone)
        self.button(cards[1], 'Load OS catalog', 1, self.load_catalog)
        self.combo = ttk.Combobox(cards[1], textvariable=self.selected, state='disabled', width=65)
        self.combo.pack(fill='x', pady=8)
        self.controls.append((1, self.combo, 'readonly'))
        self.button(cards[1], 'Download and verify package', 1, self.download)
        self.mode_text = tk.StringVar(value=MODE_HELP['Download Only'])
        ttk.Label(cards[2], text='Normal installation: Download Only', style='Step.TLabel').pack(anchor='w')
        ttk.Label(cards[2], textvariable=self.mode_text, style='Card.TLabel', wraplength=800).pack(anchor='w', pady=8)
        ttk.Label(cards[2], text='Other operations are explained in Advanced recovery below. They do not bypass device checks.', style='Card.TLabel', wraplength=800).pack(anchor='w', pady=8)
        checkbox = ttk.Checkbutton(cards[2], text='I have saved what I need and understand that my data may be erased.', variable=self.ack)
        checkbox.pack(anchor='w', pady=8); self.controls.append((2, checkbox, 'normal'))
        self.review_button = self.button(cards[2], 'Check installation compatibility', 2, self.review)
        # No live installation authorization exists yet. Keep these locked rather
        # than connecting cosmetic Next buttons to destructive operations.
        for index, title in ((3, 'Start powered-off connection listener'), (4, 'Review and install'), (5, 'Verify installed OS')):
            button = ttk.Button(cards[index], text=title, state='disabled')
            button.pack(anchor='w'); self.controls.append((index, button, 'disabled'))
        self.button(parent, 'Start again with another phone', -1, self.reset)
        self.selected.trace_add('write', lambda *_: self.invalidate_package())
        self.ack.trace_add('write', lambda *_: self.refresh())
        self.refresh()

    def button(self, parent, title, stage, action):
        def invoke():
            if self.app.busy or (stage >= 0 and self.stage != stage):
                return
            if stage >= 0:
                self.app.active_action_message = self.results[stage]
            action()
        button = ttk.Button(parent, text=title, command=invoke, style='Primary.TButton' if stage >= 0 else 'TButton')
        button.pack(anchor='w', pady=5)
        self.controls.append((stage, button, 'normal'))
        self.app.buttons.append(button)
        return button

    def refresh(self):
        if hasattr(self.app, 'flash_prerequisite'):
            self.app.flash_prerequisite.refresh()
        for index, label in enumerate(getattr(self.app, 'firmware_rail', [])):
            label.configure(text=('✓' if index < self.stage else str(index+1))+'  '+STEPS[index],
                            foreground='#B83A21' if index <= self.stage else '#55555A',
                            font=('Segoe UI', 10, 'bold' if index == self.stage else 'normal'))
        for index, label in enumerate(self.labels):
            label.set('Complete' if index < self.stage else 'Current step' if index == self.stage else f'Locked · finish step {index}')
        for stage, control, normal in self.controls:
            enabled = not self.app.busy and (stage == -1 or stage == self.stage)
            control.configure(state=normal if enabled else 'disabled')
        if not self.ack.get():
            self.review_button.configure(state='disabled')

    def reset(self):
        self.stage = 0; self.phone = self.downloaded = self.plan = None
        self.app.firmware_device = {}
        self.ack.set(False)
        for result in self.results: result.set('')
        self.summary.set('No phone checked. Start with Android running.')
        self.refresh()

    def check_phone(self):
        self.phone = None
        def done(phone):
            self.phone = phone; self.stage = 1
            text = f"Connected: {phone['ro.product.model']} · Android {phone['ro.build.version.release']}\nOS: {phone['ro.lineage.version'] or phone['ro.build.fingerprint']}\nUSB debugging authorized. Reported identity is not yet a compatibility approval."
            self.results[0].set(text); self.summary.set(text); self.refresh()
        self.run('Checking Android connection', lambda: read_android(self.app.fields['adb'].get()), done)

    def load_catalog(self):
        def done(rows):
            self.packages = {r['name']: r for r in rows if r['format'] == 'spflash' and r.get('sha256_url')}
            self.combo.configure(values=list(self.packages))
            self.selected.set(next(iter(self.packages), ''))
            self.results[1].set(f'{len(self.packages)} SP Flash packages with checksums. These are catalog entries, not compatibility approvals.')
        self.run('Loading OS catalog', catalog.catalog, done)

    def invalidate_package(self):
        self.downloaded = self.plan = None; self.ack.set(False)
        if self.stage > 1: self.stage = 1
        self.refresh()

    def download(self):
        package = self.packages.get(self.selected.get())
        if not package:
            self.results[1].set('Load the catalog and choose a package first.'); return
        def operation():
            result = catalog.download(package, self.app.data/'firmware', progress=lambda n, total:
                self.app.firmware_progress.emit(f'Download {100*n/total:.1f}%', 100*n/total))
            return result, review_package(result['path'], 'EMMC')
        def done(result):
            self.downloaded, self.plan = result; self.stage = 2
            self.results[1].set('Download checksum matches. Saved: '+self.downloaded['path'])
            erases = any(p['erases_user_data'] for p in self.plan['partitions'])
            self.results[2].set('Package review: '+('includes a user-data image; personal data will be replaced.' if erases else 'no user-data image selected; this is not a guarantee that data will survive.'))
            self.refresh()
        self.run('Downloading and inspecting OS', operation, done)

    def review(self):
        if not self.ack.get() or not self.downloaded or not self.phone:
            return
        # Check the same Android transport again before any shutdown instruction.
        def operation():
            current = read_android(self.app.fields['adb'].get())
            if current != self.phone:
                raise ValueError('The connected phone or OS changed. Start again and check the new phone.')
            from .flash_prerequisite import check_installation
            check_installation(self.app.firmware_tool.get())
            require_verified_compatibility(current, self.downloaded['path'], 'download')
            # Fail closed if that API changes until its verified plan is wired to
            # the shutdown listener, installer and independent first-boot check.
            raise ValueError('Guided installation is not available in this build. No reboot or write was sent.')
        self.run('Checking exact phone and release compatibility', operation)


def build_mode_guide(parent):
    for title, text in MODE_HELP.items():
        ttk.Label(parent, text=title, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(12, 3))
        ttk.Label(parent, text=text, wraplength=850).pack(anchor='w')
