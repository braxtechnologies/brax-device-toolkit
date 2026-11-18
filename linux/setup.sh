#!/bin/sh
# Prepare Brax Device Toolkit on Debian, Ubuntu or Linux Mint.
# Creates .venv, installs pyserial, optionally installs udev rules and a menu entry.
set -eu
here=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
python=${PYTHON:-python3}

missing=""
command -v "$python" >/dev/null 2>&1 || missing="$missing python3"
"$python" -c 'import venv, ensurepip' 2>/dev/null || missing="$missing python3-venv"
"$python" -c 'import tkinter' 2>/dev/null || missing="$missing python3-tk"
if [ -n "$missing" ]; then
    echo "Missing:$missing"
    echo "Install with: sudo apt install$missing"
    exit 1
fi
"$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
    echo "Python 3.11 or newer is required (found $("$python" -V))."; exit 1; }

if [ ! -x "$here/.venv/bin/python" ]; then
    "$python" -m venv "$here/.venv"
fi
"$here/.venv/bin/python" -m pip install --quiet --upgrade pip
"$here/.venv/bin/python" -m pip install --quiet -r "$here/requirements.txt"
chmod +x "$here/linux/brax-device-toolkit"
echo "Python environment ready: $here/.venv"

command -v adb >/dev/null 2>&1 || echo "Note: adb is not on PATH. Install it (sudo apt install adb) or choose it in the app."

rules=/etc/udev/rules.d/70-brax3.rules
if [ ! -f "$rules" ] || ! cmp -s "$here/linux/70-brax3.rules" "$rules"; then
    printf 'Install udev rules for BraX3 USB/serial access to %s (needs sudo)? [y/N] ' "$rules"
    read -r answer
    if [ "$answer" = y ] || [ "$answer" = Y ]; then
        sudo install -m 0644 "$here/linux/70-brax3.rules" "$rules"
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        echo "udev rules installed. Replug the phone."
    fi
fi
if ! id -nG | tr ' ' '\n' | grep -qx dialout; then
    echo "Note: you are not in the 'dialout' group. The udev rules grant access to the"
    echo "logged-in user; for SSH or other sessions run: sudo usermod -aG dialout \$USER (then log out and in)."
fi

desktop="${XDG_DATA_HOME:-$HOME/.local/share}/applications/brax-device-toolkit.desktop"
printf 'Add "Brax Device Toolkit" to the application menu? [y/N] '
read -r answer
if [ "$answer" = y ] || [ "$answer" = Y ]; then
    mkdir -p "$(dirname "$desktop")"
    cat > "$desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Brax Device Toolkit
Comment=BraX3 connection checks, identifier repair and record backups
Exec=$here/linux/brax-device-toolkit
Icon=$here/brax_snwriter/assets/brax-logo.png
Terminal=false
Categories=Utility;
DESKTOP
    echo "Menu entry created: $desktop"
fi
echo "Start with: $here/linux/brax-device-toolkit"
