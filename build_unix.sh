#!/usr/bin/env bash
# Linux: dist/Djorge Local/Djorge Local  ·  macOS: dist/Djorge Local.app
set -e; cd "$(dirname "$0")"
python3 -m venv .venv-build && . .venv-build/bin/activate
pip install -q --index-url https://download.pytorch.org/whl/cpu torch || pip install -q torch
pip install -q -r requirements.txt pystray keyring pyinstaller
python -m PyInstaller djorge-local.spec --noconfirm
echo "pronto em dist/"
