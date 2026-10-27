"""Configuration: config.yaml (see config.example.yaml). Every connector and every action is OFF unless enabled."""
from __future__ import annotations

import copy
import os
import secrets
from pathlib import Path

import yaml

HOME = Path(os.environ.get("DJORGE_LOCAL_HOME", Path.home() / ".djorge-local"))

DEFAULTS = {
    "device": "auto",                     # auto | cpu | cuda
    "threads": 0,                         # 0 = all cores
    "timezone": "America/Sao_Paulo",
    "language": "pt",
    "models": {                           # the Flash ships inside the installer (@app = program folder)
        "flash": {"enabled": True, "path": "@app/modelos/flash", "role": "Origo Flash — rápido, leve, para qualquer computador"},
    },
    "server": {"host": "127.0.0.1", "port": 8765, "token": ""},
    # full DJORGE API per model (/v1/classify, /v1/aggregate, /v1/tag, /v1/decide, /v1/document, /v1/verify,
    # /v1/score, /v1/select, /v1/tools/*, /execute, /contexts, /capabilities ...). Bearer token = server.token
    "api": {"enabled": True, "host": "127.0.0.1", "ports": {"flash": 8750}, "batch_window_ms": 8,
            "rate_rps": 50, "rate_burst": 200, "max_body_mb": 2},
    "connectors": {
        "http_inbox": {"enabled": True},                        # any program: POST /api/event
        "notifications": {"enabled": False, "apps_allow": [], "apps_deny": [], "poll_s": 2},
        "folder": {"enabled": False, "paths": [], "patterns": ["*.txt", "*.md", "*.eml", "*.pdf", "*.docx",
                                                                "*.png", "*.jpg"], "poll_s": 3, "max_mb": 20},
        "clipboard": {"enabled": False, "poll_s": 1.5, "min_chars": 8},
        "imap": {"enabled": False, "host": "", "port": 993, "user": "", "password_env": "DJORGE_IMAP_PASSWORD",
                 "folder": "INBOX", "poll_s": 60},
        "command": {"enabled": False, "jobs": []},           # [{name, argv: [...], every_s}]
        "stdin": {"enabled": False},
    },
    "extract": {"ocr": True, "ocr_lang": "por+eng", "audio": False, "whisper_model": "small", "max_chars": 20000},
    "actions": {
        "require_confirmation": ["command", "webhook"],     # actions that leave the computer / run programs
        "webhook_allow": [],              # URL prefixes allowed for the webhook action
        "command_allow": [],              # executable names allowed for the command action
        "write_dir": str(HOME / "saidas"),
        "notify": True,
    },
    "trees_file": "arvores.yaml",
    "history": {"keep": 2000, "store_text": True, "mask_pii": True},
    "max_steps": 25,
    "open_panel_on_start": True,
    "economy": "auto",                    # auto (liga com menos de 8 GB de RAM) | on | off
}


def _merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _migrate(raw: dict) -> dict:
    """Configs from 0.7.x (encrypted .djx, Google Drive, origo + flash): keep only folders, bring the bundled Flash."""
    if not raw:
        return raw
    raw.pop("drive_folder_id", None); raw.pop("key_env", None)
    ms = raw.get("models")
    if isinstance(ms, dict):
        for k, m in list(ms.items()):
            if not isinstance(m, dict):
                ms.pop(k); continue
            m.pop("sealed", None); m.pop("drive_file_id", None)
            if k == "flash" and not m.get("path"):
                m.update(path="@app/modelos/flash", enabled=True)
            if k != "flash" and not m.get("path"):
                ms.pop(k)
        ms.setdefault("flash", dict(DEFAULTS["models"]["flash"]))
        ports = (raw.get("api") or {}).get("ports") or {}
        for k in list(ports):
            if k not in ms:
                ports.pop(k)
    return raw


def load(path: str = None) -> dict:
    p = Path(path or HOME / "config.yaml")
    raw = _migrate(yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {})
    cfg = _merge(DEFAULTS, raw)
    if raw and isinstance(raw.get("models"), dict):
        cfg["models"] = raw["models"]                 # the user's list replaces the default one
    cfg["_path"] = str(p)
    cfg["_dir"] = str(p.parent)
    if not cfg["server"]["token"]:
        tf = HOME / "token.txt"
        HOME.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(HOME, 0o700)                         # history, token and models: only this user
        except OSError:
            pass
        if not tf.is_file():
            tf.write_text(secrets.token_urlsafe(24)); os.chmod(tf, 0o600)
        cfg["server"]["token"] = tf.read_text().strip()
    os.environ.setdefault("DJORGE_TZ", cfg["timezone"])
    os.environ["DJORGE_SENTINEL"] = "0"          # this build carries no Sentinel
    a = cfg["api"]
    os.environ.setdefault("DJORGE_RATE_RPS", str(a["rate_rps"]))
    os.environ.setdefault("DJORGE_RATE_BURST", str(a["rate_burst"]))
    os.environ.setdefault("DJORGE_MAX_BODY", str(int(a["max_body_mb"] * 1e6)))
    return cfg


def resolve(cfg: dict, rel: str) -> Path:
    p = Path(rel).expanduser()
    return p if p.is_absolute() else Path(cfg["_dir"]) / p
