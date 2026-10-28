"""Actions at the end of a decision path. Side effects are opt-in and, by default, wait for confirmation
in the dashboard (actions.require_confirmation). Nothing here sends anything anywhere unless configured.

  notify      desktop notification (title, message)
  log         line in the history (message)
  write_file  append text to a file inside actions.write_dir (file, content)
  webhook     POST JSON to a URL whose prefix is in actions.webhook_allow (url, body)
  command     run an executable listed in actions.command_allow, argv as a list (no shell)
  email_move  move the e-mail of this event to another folder (IMAP; needs connectors.imap.allow_move)
  stop        end this path
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .tree import render


def desktop_notify(title: str, message: str):
    title, message = title[:120], message[:400]
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name="Djorge Local", timeout=8)
        return True
    except Exception:
        pass
    sysname = platform.system()
    try:
        if sysname == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=5)
            return True
        if sysname == "Darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                           timeout=5)
            return True
    except Exception:
        pass
    print(f"[aviso] {title}: {message}")
    return False


class Actions:
    def __init__(self, cfg, history):
        self.cfg, self.hist = cfg, history
        self.pending = {}
        self.lock = threading.Lock()

    def run(self, n: dict, ctx: dict, tree: str, nid: str) -> dict:
        kind = n["action"]
        spec = {k: render(v, ctx) if isinstance(v, str) else
                ([render(x, ctx) for x in v] if isinstance(v, list) else v) for k, v in n.items()}
        if kind == "stop":
            return {"action": "stop"}
        if kind in self.cfg["actions"]["require_confirmation"] and not n.get("auto", False):
            pid = uuid.uuid4().hex[:10]
            with self.lock:
                self.pending[pid] = {"id": pid, "tree": tree, "node": nid, "spec": spec, "created": time.time()}
            return {"action": kind, "status": "aguardando confirmação", "pending_id": pid}
        return self.execute(spec)

    def confirm(self, pid: str, approve: bool) -> dict:
        with self.lock:
            p = self.pending.pop(pid, None)
        if not p:
            return {"error": "pendência não encontrada"}
        if not approve:
            self.hist.add({"type": "action", "status": "recusada", **p})
            return {"status": "recusada"}
        r = self.execute(p["spec"])
        self.hist.add({"type": "action", "status": "executada", **p, "result": r})
        return r

    def execute(self, s: dict) -> dict:
        kind, A = s["action"], self.cfg["actions"]
        try:
            if kind == "notify":
                ok = A.get("notify", True) and desktop_notify(s.get("title", "Djorge"), s.get("message", ""))
                return {"action": kind, "status": "ok" if ok else "mostrado no console"}
            if kind == "log":
                self.hist.add({"type": "log", "message": s.get("message", "")})
                return {"action": kind, "status": "ok"}
            if kind == "write_file":
                base = Path(A["write_dir"]).expanduser().resolve()
                base.mkdir(parents=True, exist_ok=True)
                p = (base / s.get("file", "saida.txt")).resolve()
                if not str(p).startswith(str(base) + os.sep):
                    return {"action": kind, "status": "negado", "reason": "fora de actions.write_dir"}
                with open(p, "a", encoding="utf-8") as f:
                    f.write(s.get("content", "") + "\n")
                return {"action": kind, "status": "ok", "file": str(p)}
            if kind == "webhook":
                url = s.get("url", "")
                if not any(url.startswith(pre) for pre in A["webhook_allow"]) or not url.startswith(("https://",
                                                                                                    "http://127.0.0.1",
                                                                                                    "http://localhost")):
                    return {"action": kind, "status": "negado", "reason": "URL fora de actions.webhook_allow"}
                import urllib.request
                body = s.get("body") or {}
                req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    return {"action": kind, "status": r.status}
            if kind == "command":
                argv = s.get("argv") or []
                if not argv or Path(argv[0]).name not in A["command_allow"]:
                    return {"action": kind, "status": "negado", "reason": "programa fora de actions.command_allow"}
                p = subprocess.run(argv, capture_output=True, text=True, timeout=int(s.get("timeout", 30)))
                return {"action": kind, "status": p.returncode, "stdout": p.stdout[-2000:], "stderr": p.stderr[-1000:]}
            if kind == "email_move":
                return self._email_move(s)
        except Exception as e:
            return {"action": kind, "status": "erro", "error": f"{type(e).__name__}: {e}"}
        return {"action": kind, "status": "desconhecida"}

    def _email_move(self, s: dict) -> dict:
        import imaplib
        c = (self.cfg.get("connectors") or {}).get("imap") or {}
        uid, folder = str(s.get("uid") or "").strip(), str(s.get("folder") or "").strip()
        if not c.get("allow_move"):
            return {"action": "email_move", "status": "negado", "reason": "ligue connectors.imap.allow_move"}
        if not uid.isdigit() or not folder or len(folder) > 100 or any(ch in folder for ch in '\r\n"'):
            return {"action": "email_move", "status": "negado", "reason": "e-mail ou pasta inválidos"}
        pw = os.environ.get(c.get("password_env", "DJORGE_IMAP_PASSWORD"))
        with imaplib.IMAP4_SSL(c["host"], int(c.get("port", 993))) as M:
            M.login(c["user"], pw)
            M.select(s.get("mailbox") or c.get("folder", "INBOX"))
            q = f'"{folder}"'
            if M.list('""', q)[1] in ([None], []):
                M.create(q)
            caps = {x.decode().upper() if isinstance(x, bytes) else str(x).upper() for x in (M.capabilities or ())}
            if "MOVE" in caps:
                typ, _ = M.uid("MOVE", uid, q)
            else:                                       # COPY + mark; expunge only this message when possible
                typ, _ = M.uid("COPY", uid, q)
                if typ == "OK":
                    M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                    if "UIDPLUS" in caps:
                        M.uid("EXPUNGE", uid)
        return {"action": "email_move", "status": "ok" if typ == "OK" else "erro", "folder": folder}
