"""Windows packaging entry point. Device operations start only from the UI."""
import argparse
from pathlib import Path
import tkinter as tk
from brax_snwriter.repair_ui import RepairApp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-os', action='store_true')
    parser.add_argument('--flash-log', type=Path, help='View a log; does not start flashing')
    args = parser.parse_args()
    root = tk.Tk()
    app = RepairApp(root)
    if args.install_os or args.flash_log:
        app.tabs.select(app.sections['firmware'])
    if args.flash_log:
        app.firmware_progress.follow = args.flash_log.resolve()
        app.firmware_progress.emit('Viewing recorded flash log: ' + str(args.flash_log), 0)
    root.mainloop()

if __name__ == '__main__':
    main()
