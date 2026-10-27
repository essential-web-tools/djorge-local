"""Core: history, the worker that walks the trees for each event, and the local server (dashboard + API)."""
from __future__ import annotations

import collections
import hmac
import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import connectors as C
from .actions import Actions
from .config import HOME, resolve
from .models import Models
from .tree import Runner, TreeError, matches, validate

UI = Path(__file__).with_name("ui.html")


class History:
    def __init__(self, cfg):
        self.cfg = cfg
        self.items = collections.deque(maxlen=int(cfg["history"]["keep"]))
        self.lock = threading.Lock()
        self.file = HOME / "historico.jsonl"
        HOME.mkdir(parents=True, exist_ok=True)

    def add(self, rec: dict):
        rec = dict(rec, t=time.strftime("%Y-%m-%dT%H:%M:%S"))
        ev = rec.get("event")
        if ev:
            ev = dict(ev)
            if not self.cfg["history"]["store_text"]:
                ev["text"] = f"[{len(ev.get('text', ''))} caracteres]"
            elif self.cfg["history"]["mask_pii"]:
                try:
                    from djorge.runtime.tools import mask_pii
                    ev["text"] = mask_pii(ev.get("text", ""))["masked"]
                except Exception:
                    pass
            rec["event"] = ev
        with self.lock:
            self.items.append(rec)
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def last(self, n=100):
        with self.lock:
            return list(self.items)[-n:][::-1]


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.hist = History(cfg)
        self.models = Models(cfg)
        self.actions = Actions(cfg, self.hist)
        self.runner = Runner(cfg, self.models, self.actions)
        self.trees, self.conns = [], {}
        self.reload_trees()

    def trees_path(self) -> Path:
        return resolve(self.cfg, self.cfg["trees_file"])

    def reload_trees(self, text: str = None):
        p = self.trees_path()
        data = yaml.safe_load(text if text is not None else (p.read_text(encoding="utf-8") if p.is_file() else ""))
        trees = validate((data or {}).get("trees") or [])
        if text is not None:
            p.write_text(text, encoding="utf-8")
        self.trees = trees
        return len(trees)

    def handle(self, ev: dict, only: str = None) -> dict:
        out = []
        for t in self.trees:
            if only and t["name"] != only:
                continue
            if not t.get("enabled", True) or (not only and not matches(t, ev)):
                continue
            try:
                out.append(self.runner.run(t, ev))
            except Exception as e:
                out.append({"tree": t["name"], "error": f"{type(e).__name__}: {e}"})
        rec = {"type": "event", "event": ev, "runs": out}
        if not ev.get("skipped"):
            self.hist.add(rec)
        return rec

    def worker(self):
        while True:
            ev = C.EVENTS.get()
            try:
                self.handle(ev)
            except Exception:
                traceback.print_exc()

    def status(self):
        return {"models": {k: {"enabled": v.get("enabled", True), "role": v.get("role", "")}
                           for k, v in self.cfg["models"].items()}, "loaded": self.models.loaded(), "model_state": self.models.state,
                "api": getattr(self.models, "apis", {}),
                "connectors": {k: c.status for k, c in self.conns.items()},
                "trees": [t["name"] for t in self.trees], "pending": len(self.actions.pending),
                "queue": C.EVENTS.qsize()}

    def start(self, workers: int = 1):
        self.conns = C.start_all(self.cfg)
        for _ in range(max(1, workers)):
            threading.Thread(target=self.worker, daemon=True).start()


def make_handler(app: App):
    token = app.cfg["server"]["token"]

    class H(BaseHTTPRequestHandler):
        server_version = "DjorgeLocal"

        def log_message(self, *a):
            pass

        def _auth(self, q):
            got = self.headers.get("X-Token") or (q.get("token") or [""])[0]
            ck = self.headers.get("Cookie", "")
            if "djtoken=" in ck:
                got = got or ck.split("djtoken=", 1)[1].split(";", 1)[0]
            return hmac.compare_digest(got or "", token)

        def _send(self, code, obj, ctype="application/json", extra=None):
            body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, ensure_ascii=False,
                                                                              default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ValueError("corpo grande demais")
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path.startswith("/static/fonts/"):          # bundled OFL fonts (no token: nothing private)
                name = u.path.rsplit("/", 1)[1]
                f = UI.parent / "static" / "fonts" / name
                if "/" in name or ".." in name or not name.endswith(".woff2") or not f.is_file():
                    return self._send(404, {"error": "não encontrado"})
                return self._send(200, f.read_bytes(), "font/woff2", {"Cache-Control": "max-age=31536000"})
            if u.path == "/":
                if not self._auth(q):
                    return self._send(401, b"<p>Abra com ?token=SEU_TOKEN (veja ~/.djorge-local/token.txt)</p>",
                                      "text/html")
                return self._send(200, UI.read_bytes(), "text/html",
                                  {"Set-Cookie": f"djtoken={token}; HttpOnly; SameSite=Strict; Path=/"})
            if not self._auth(q):
                return self._send(401, {"error": "token"})
            if u.path == "/api/status":
                return self._send(200, app.status())
            if u.path == "/api/token":
                return self._send(200, {"token": token})
            if u.path == "/api/history":
                return self._send(200, app.hist.last(int((q.get("n") or ["100"])[0])))
            if u.path == "/api/pending":
                return self._send(200, list(app.actions.pending.values()))
            if u.path == "/api/trees":
                p = app.trees_path()
                return self._send(200, {"yaml": p.read_text(encoding="utf-8") if p.is_file() else ""})
            self._send(404, {"error": "não encontrado"})

        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if not self._auth(q):
                return self._send(401, {"error": "token"})
            if self.headers.get("Origin") and not self.headers["Origin"].startswith(
                    (f"http://127.0.0.1:{app.cfg['server']['port']}", f"http://localhost:{app.cfg['server']['port']}")):
                return self._send(403, {"error": "origem"})
            try:
                b = self._body()
                if u.path == "/api/event":
                    ev = C.emit(b.get("source", "http"), str(b.get("text", "")), title=b.get("title", ""),
                                app=b.get("app", ""), sender=b.get("sender", "")) if not b.get("sync") else None
                    if b.get("sync"):
                        ev = {"id": "sync", "source": b.get("source", "http"), "text": str(b.get("text", "")),
                              "title": b.get("title", ""), "app": b.get("app", ""), "sender": b.get("sender", ""),
                              "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
                        return self._send(200, app.handle(ev, only=b.get("tree")))
                    return self._send(202, {"queued": ev["id"]})
                if u.path == "/api/decide":
                    z = app.models.get(b.get("model", "origo"))
                    r = z.execute(b["query"], sources={"text": b.get("text", "")}, lang=app.cfg["language"],
                                  now=b.get("now"))
                    return self._send(200, r)
                if u.path.startswith("/api/pending/"):
                    return self._send(200, app.actions.confirm(u.path.rsplit("/", 1)[1], bool(b.get("approve"))))
                if u.path == "/api/trees":
                    return self._send(200, {"trees": app.reload_trees(b["yaml"])})
            except (TreeError, yaml.YAMLError, KeyError, ValueError) as e:
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            self._send(404, {"error": "não encontrado"})

    return H


def serve(app: App, block: bool = True):
    s = app.cfg["server"]
    if s["host"] not in ("127.0.0.1", "localhost", "::1"):
        print("⚠ servidor aberto na rede: proteja a máquina e o token")
    httpd = ThreadingHTTPServer((s["host"], int(s["port"])), make_handler(app))
    print(f"Painel: http://{s['host']}:{s['port']}/?token={s['token']}")
    if not block:
        threading.Thread(target=httpd.serve_forever, daemon=True, name="painel").start()
        return httpd
    httpd.serve_forever()
