#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
[ -f "$HOME/.djorge-local/config.yaml" ] || python -m djorge_local init
exec python -m djorge_local run
