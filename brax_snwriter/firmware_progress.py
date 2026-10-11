"""Thread-safe firmware progress and diagnostic log presentation."""
import queue
import re
import tkinter as tk
from tkinter import ttk, filedialog
from tkinter.scrolledtext import ScrolledText
from datetime import datetime


def parse_progress(line):
    """Vendor percentages describe one transfer, never overall completion."""
    match = re.search(r'(\d+)% of \[(.+?)\] data has been sent', line)
    if match:
        return min(100, int(match[1])), 'Transferring ' + match[2]
    match = re.search(r'(\d+)% of DA data has been sent', line)
    if match:
        return min(100, int(match[1])), 'Loading Download Agent'
    return None


class FirmwareProgress:
    def __init__(self, root, parent, data):
        self.root = root
        self.events = queue.Queue()
        self.follow = None
        self.offset = 0
        self.pending = ''
        data.mkdir(parents=True, exist_ok=True)
        self.path = data / ('firmware-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')
        box = ttk.LabelFrame(parent, text='Progress & debug log', padding=10)
        box.pack(fill='x', pady=10)
        self.label = tk.StringVar(value='Ready — no operation running')
        ttk.Label(box, textvariable=self.label, wraplength=800).pack(anchor='w')
        self.bar = ttk.Progressbar(box, maximum=100, mode='determinate')
        self.bar.pack(fill='x', pady=6)
        ttk.Label(box, text='Percentage applies to the named transfer. 100% of an image does not mean the whole flash or first boot is complete.', wraplength=800).pack(anchor='w')
        self.log = ScrolledText(box, height=8, wrap='word', state='disabled')
        self.log.pack(fill='x', pady=6)
        buttons = ttk.Frame(box)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Open / follow flashing log', command=self.choose_log).pack(side='left')
        ttk.Button(buttons, text='Save debug log', command=self.save).pack(side='left', padx=8)
        self.timer = root.after(100, self.drain)

    def emit(self, text, percent=None):
        self.events.put((str(text), percent))

    def choose_log(self):
        name = filedialog.askopenfilename(parent=self.root, title='View a flashing log (does not start flashing)', filetypes=[('Logs', '*.log'), ('All files', '*')])
        if name:
            from pathlib import Path
            self.follow = Path(name)
            self.offset = 0
            self.pending = ''
            self.emit('Viewing external log: ' + name, 0)

    def append(self, text, percent=None):
        with self.path.open('a', encoding='utf-8') as out:
            out.write(text + '\n')
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        if int(self.log.index('end-1c').split('.')[0]) > 1500:
            self.log.delete('1.0', '250.0')
        self.log.see('end')
        self.log.configure(state='disabled')
        parsed = parse_progress(text)
        if parsed:
            percent, stage = parsed
            self.label.set(f'{stage}: {percent}%')
        elif percent is not None:
            self.label.set(text)
        elif 'All command exec done!' in text:
            self.label.set('Tool reports completion — first boot must be checked separately')
        elif any(word in text.lower() for word in ('failed', 'error:', 'timeout')):
            self.label.set(text[:220])
        if percent is not None:
            self.bar.configure(value=max(0, min(100, percent)))

    def drain(self):
        try:
            if self.follow:
                with self.follow.open('r', encoding='utf-8', errors='replace') as source:
                    if self.follow.stat().st_size < self.offset:
                        self.offset = 0
                        self.pending = ''
                    source.seek(self.offset)
                    chunk = source.read(65536)
                    self.offset = source.tell()
                lines = (self.pending + chunk).split('\n')
                self.pending = lines.pop()
                for line in lines:
                    self.events.put((line.rstrip('\r'), None))
            for _ in range(300):
                try:
                    self.append(*self.events.get_nowait())
                except queue.Empty:
                    break
        except OSError as exc:
            self.follow = None
            self.label.set('Debug log unavailable: ' + str(exc))
        self.timer = self.root.after(100, self.drain)

    def save(self):
        name = filedialog.asksaveasfilename(parent=self.root, title='Save firmware debug log', defaultextension='.log', filetypes=[('Log', '*.log')])
        if name:
            from pathlib import Path
            Path(name).write_text(self.path.read_text(encoding='utf-8') if self.path.exists() else '', encoding='utf-8')
