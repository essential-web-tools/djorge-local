"""Connectors: where events come from. Each one is OFF until enabled in config.yaml.

  http_inbox     any program sends text: POST http://127.0.0.1:8765/api/event (token) - the universal connector
  notifications  system notifications: Windows (winsdk, asks your permission), Linux (D-Bus), macOS (read-only
                 Notification Center database; needs Full Disk Access granted by you)
  folder         new / changed files in chosen folders (text, e-mail, PDF, DOCX, images by OCR, audio)
  clipboard      text you copy (pyperclip)
  imap           new e-mails, read-only (BODY.PEEK: they stay unread); the email_move action can move them
                 when connectors.imap.allow_move is on
  command        runs a program you list and reads its output (e.g. a CLI of another app)
  stdin          lines typed / piped into the program
"""
from __future__ import annotations

import fnmatch
import imaplib
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .. import extract

EVENTS: "queue.Queue[dict]" = queue.Queue(maxsize=5000)


def emit(source: str, text: str, **kw):
    ev = {"id": uuid.uuid4().hex[:12], "source": source, "text": (text or "")[:200000],
          "time": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}
    try:
        EVENTS.put_nowait(ev)
    except queue.Full:
        pass
    return ev


class Base(threading.Thread):
    name_ = "base"

    def __init__(self, cfg, conf):
        super().__init__(daemon=True, name=f"conector-{self.name_}")
        self.cfg, self.conf, self.stop_ev = cfg, conf, threading.Event()
        self.status = "iniciando"

    def stop(self):
        self.stop_ev.set()

    def sleep(self, s):
        self.stop_ev.wait(float(s))


# ------------------------------------------------------------------------------------------- notifications --
class Notifications(Base):
    name_ = "notifications"

    def _ok_app(self, app):
        a = (app or "").casefold()
        allow, deny = self.conf.get("apps_allow") or [], self.conf.get("apps_deny") or []
        if deny and any(d.casefold() in a for d in deny):
            return False
        return not allow or any(x.casefold() in a for x in allow)

    def run(self):
        s = platform.system()
        try:
            if s == "Windows":
                self._windows()
            elif s == "Linux":
                self._linux()
            elif s == "Darwin":
                self._macos()
            else:
                self.status = f"não suportado em {s}"
        except Exception as e:
            self.status = f"erro: {type(e).__name__}: {e}"

    def _windows(self):
        import asyncio
        try:
            from winsdk.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
            from winsdk.windows.ui.notifications.management import (UserNotificationListener,
                                                                    UserNotificationListenerAccessStatus)
        except ImportError:
            self.status = "instale: pip install winsdk"; return
        lst = UserNotificationListener.current
        loop = asyncio.new_event_loop()
        st = loop.run_until_complete(lst.request_access_async())
        if st != UserNotificationListenerAccessStatus.ALLOWED:
            self.status = "acesso negado: Configurações > Privacidade > Notificações > permitir"; return
        self.status = "ativo (Windows)"
        seen = set()
        while not self.stop_ev.is_set():
            for n in loop.run_until_complete(lst.get_notifications_async(NotificationKinds.TOAST)):
                if n.id in seen:
                    continue
                seen.add(n.id)
                try:
                    app = n.app_info.display_info.display_name
                    b = n.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
                    parts = [t.text for t in b.get_text_elements()] if b else []
                except Exception:
                    continue
                if self._ok_app(app) and parts:
                    emit("notifications", "\n".join(parts[1:]) or parts[0], title=parts[0], app=app)
            if len(seen) > 5000:
                seen = set(list(seen)[-1000:])
            self.sleep(self.conf.get("poll_s", 2))

    def _linux(self):
        if not shutil.which("dbus-monitor"):
            self.status = "instale dbus-monitor (pacote dbus)"; return
        p = subprocess.Popen(["dbus-monitor", "--session", "interface='org.freedesktop.Notifications',member='Notify'"],
                             stdout=subprocess.PIPE, text=True)
        self.status = "ativo (Linux D-Bus)"
        strings, inside = [], False
        for line in p.stdout:
            if self.stop_ev.is_set():
                p.terminate(); break
            if "member=Notify" in line:
                if inside and len(strings) >= 4:
                    self._emit_linux(strings)
                strings, inside = [], True
                continue
            m = re.match(r'\s*string "(.*)"\s*$', line)
            if inside and m:
                strings.append(m.group(1))
                if len(strings) == 4:
                    self._emit_linux(strings); inside = False

    def _macos(self):
        """macOS has no public API for other apps' notifications. With the user's explicit Full Disk Access
        (Ajustes > Privacidade e Segurança > Acesso Total ao Disco > Djorge Local), the Notification Center
        database is read READ-ONLY. Without that permission this connector just reports it."""
        import plistlib
        import sqlite3
        cands = [Path.home() / "Library/Group Containers/group.com.apple.usernoted/db2/db"]
        try:
            d = subprocess.run(["getconf", "DARWIN_USER_DIR"], capture_output=True, text=True).stdout.strip()
            cands.append(Path(d) / "com.apple.notificationcenter/db2/db")
        except Exception:
            pass
        db = next((c for c in cands if c.exists()), None)
        if db is None:
            self.status = "banco de notificações não encontrado"; return
        last = None
        while not self.stop_ev.is_set():
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
                if last is None:
                    last = con.execute("SELECT COALESCE(MAX(rec_id), 0) FROM record").fetchone()[0]
                rows = con.execute("SELECT r.rec_id, a.identifier, r.data FROM record r JOIN app a "
                                   "ON r.app_id = a.app_id WHERE r.rec_id > ? ORDER BY r.rec_id", (last,)).fetchall()
                con.close()
                self.status = "ativo (macOS, leitura com Acesso Total ao Disco)"
            except sqlite3.Error:
                self.status = "sem permissão: dê Acesso Total ao Disco ao Djorge Local"; self.sleep(10); continue
            for rec_id, app, data in rows:
                last = max(last, rec_id)
                try:
                    req = plistlib.loads(data).get("req", {})
                except Exception:
                    continue
                title = " · ".join(x for x in (req.get("titl"), req.get("subt")) if x)
                body = req.get("body") or ""
                if (title or body) and self._ok_app(app):
                    emit("notifications", body or title, title=title, app=app)
            self.sleep(self.conf.get("poll_s", 2))

    def _emit_linux(self, s):
        app, _icon, summary, body = s[:4]
        if self._ok_app(app):
            emit("notifications", body or summary, title=summary, app=app)


# ----------------------------------------------------------------------------------------------- folder --
class Folder(Base):
    name_ = "folder"

    def run(self):
        paths = [Path(p).expanduser() for p in self.conf.get("paths") or []]
        if not paths:
            self.status = "sem pastas (connectors.folder.paths)"; return
        seen = {}
        first = True
        self.status = "ativo: " + ", ".join(map(str, paths))
        while not self.stop_ev.is_set():
            for base in paths:
                for f in base.rglob("*") if base.is_dir() else []:
                    if not f.is_file() or not any(fnmatch.fnmatch(f.name.lower(), p) for p in self.conf["patterns"]):
                        continue
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    key = (st.st_mtime, st.st_size)
                    if seen.get(f) == key:
                        continue
                    seen[f] = key
                    if first or st.st_size > self.conf.get("max_mb", 20) * 1e6:
                        continue                              # existing files at start-up are not re-read
                    try:
                        d = extract.from_file(str(f), self.cfg)
                        emit("folder", d["text"], title=d.get("title", f.name), app=str(f.parent),
                             sender=d.get("sender", ""), path=str(f))
                    except extract.Unsupported as e:
                        emit("folder", "", title=f.name, app=str(f.parent), path=str(f), skipped=str(e))
                    except Exception as e:
                        self.status = f"erro em {f.name}: {e}"
            first = False
            self.sleep(self.conf.get("poll_s", 3))


# -------------------------------------------------------------------------------------------- clipboard --
class Clipboard(Base):
    name_ = "clipboard"

    def run(self):
        try:
            import pyperclip
        except ImportError:
            self.status = "instale: pip install pyperclip"; return
        self.status = "ativo"
        last = None
        while not self.stop_ev.is_set():
            try:
                t = pyperclip.paste()
            except Exception:
                t = None
            if t and t != last and len(t.strip()) >= self.conf.get("min_chars", 8):
                if last is not None:
                    emit("clipboard", t, title="área de transferência")
                last = t
            elif last is None:
                last = t or ""
            self.sleep(self.conf.get("poll_s", 1.5))


# ------------------------------------------------------------------------------------------------- imap --
class Imap(Base):
    name_ = "imap"

    def run(self):
        c = self.conf
        pw = os.environ.get(c.get("password_env", "DJORGE_IMAP_PASSWORD"))
        if not (c.get("host") and c.get("user") and pw):
            self.status = "configure host, user e a variável da senha (password_env)"; return
        seen = set()
        first = True
        while not self.stop_ev.is_set():
            try:
                with imaplib.IMAP4_SSL(c["host"], int(c.get("port", 993))) as M:
                    M.login(c["user"], pw)
                    M.select(c.get("folder", "INBOX"), readonly=True)
                    _, data = M.uid("SEARCH", None, "UNSEEN")
                    ids = data[0].split()[-50:]
                    self.status = f"ativo ({len(ids)} não lidos)"
                    for i in ids:
                        if i in seen:
                            continue
                        seen.add(i)
                        if first:
                            continue
                        _, msg = M.uid("FETCH", i, "(BODY.PEEK[])")
                        raw = next((x[1] for x in msg if isinstance(x, tuple)), b"")
                        d = extract.from_eml(raw)
                        emit("email", d["text"][: self.cfg["extract"]["max_chars"]], title=d["title"],
                             sender=d["sender"], app=c["host"], uid=i.decode(), mailbox=c.get("folder", "INBOX"))
                first = False
            except Exception as e:
                self.status = f"erro: {type(e).__name__}"
            self.sleep(c.get("poll_s", 60))


# ---------------------------------------------------------------------------------------------- command --
class Command(Base):
    name_ = "command"

    def run(self):
        jobs = self.conf.get("jobs") or []
        if not jobs:
            self.status = "sem jobs"; return
        nxt = {j["name"]: 0 for j in jobs}
        last = {}
        self.status = "ativo: " + ", ".join(j["name"] for j in jobs)
        while not self.stop_ev.is_set():
            now = time.time()
            for j in jobs:
                if now < nxt[j["name"]]:
                    continue
                nxt[j["name"]] = now + float(j.get("every_s", 60))
                try:
                    p = subprocess.run(j["argv"], capture_output=True, text=True, timeout=int(j.get("timeout", 30)))
                    out = p.stdout.strip()
                except Exception as e:
                    out = ""; self.status = f"erro em {j['name']}: {e}"
                if out and out != last.get(j["name"]):
                    last[j["name"]] = out
                    emit("command", out, title=j["name"], app=j["argv"][0])
            self.sleep(1)


class Stdin(Base):
    name_ = "stdin"

    def run(self):
        self.status = "ativo: digite um texto e Enter"
        for line in sys.stdin:
            if line.strip():
                emit("stdin", line.strip(), title="terminal")


REGISTRY = {"notifications": Notifications, "folder": Folder, "clipboard": Clipboard, "imap": Imap,
            "command": Command, "stdin": Stdin}


def start_all(cfg) -> dict:
    running = {}
    for name, cls in REGISTRY.items():
        conf = cfg["connectors"].get(name) or {}
        if conf.get("enabled"):
            c = cls(cfg, conf)
            c.start()
            running[name] = c
    return running
