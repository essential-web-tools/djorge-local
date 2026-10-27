"""Core: history, the worker that walks the trees for each event, and the local server (dashboard + API)."""
from __future__ import annotations

import collections
import hmac
import json
import os
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

    def save_config(self):
        """Writes back the editable parts (models, api ports, device, threads); everything else is preserved."""
        p = Path(self.cfg["_path"])
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
        raw = raw or {}
        raw["models"] = {k: {f: v[f] for f in ("enabled", "path", "sealed", "drive_file_id", "role") if f in v}
                         for k, v in self.cfg["models"].items()}
        raw.setdefault("api", {})["ports"] = dict(self.cfg["api"].get("ports") or {})
        raw["device"], raw["threads"] = self.cfg["device"], self.cfg["threads"]
        p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

    EDITABLE = ("timezone", "language", "device", "threads", "drive_folder_id", "open_panel_on_start", "server", "api",
                "connectors", "extract", "actions", "history", "max_steps")

    def settings(self) -> dict:
        import copy
        out = {k: copy.deepcopy(self.cfg.get(k)) for k in self.EDITABLE}
        out["server"].pop("token", None)
        out["api"].pop("ports", None)
        out.setdefault("open_panel_on_start", True)
        return out

    def save_settings(self, new: dict) -> list:
        """Validates types against the current config, saves config.yaml, restarts what can be restarted live."""
        from .config import DEFAULTS

        def merge(cur, inc, path=""):
            if not isinstance(inc, dict):
                raise ValueError(f"{path or 'raiz'}: formato inválido")
            for k, v in inc.items():
                if k not in cur and not (path.startswith("connectors") or path == "connectors"):
                    raise ValueError(f"campo desconhecido: {path}{k}")
                old = cur.get(k)
                if isinstance(old, dict):
                    merge(old, v, f"{path}{k}.")
                elif isinstance(old, bool):
                    cur[k] = bool(v)
                elif isinstance(old, (int, float)) and not isinstance(old, bool):
                    try:
                        cur[k] = type(old)(v)
                    except (TypeError, ValueError):
                        raise ValueError(f"{path}{k}: número esperado")
                elif isinstance(old, list):
                    if isinstance(v, str):
                        v = [x.strip() for x in v.splitlines() if x.strip()]
                    if not isinstance(v, list):
                        raise ValueError(f"{path}{k}: lista esperada")
                    cur[k] = v
                else:
                    cur[k] = "" if v is None else str(v)
        new = {k: v for k, v in new.items() if k in self.EDITABLE}
        before = {k: json.dumps(self.cfg.get(k), sort_keys=True, default=str) for k in ("server", "api")}
        merge(self.cfg, new)
        if self.cfg["device"] not in ("auto", "cpu", "cuda"):
            self.cfg["device"] = "auto"
        if self.cfg["server"]["host"] not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            self.cfg["server"]["host"] = "127.0.0.1"
        p = Path(self.cfg["_path"])
        raw = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}) or {}
        for k in self.EDITABLE:
            raw[k] = self.cfg.get(k)
        raw["server"] = {kk: vv for kk, vv in self.cfg["server"].items() if kk != "token" or vv}
        raw["models"] = {k: {f: v[f] for f in ("enabled", "path", "sealed", "drive_file_id", "role") if f in v}
                         for k, v in self.cfg["models"].items()}
        p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        for c in self.conns.values():                      # connectors: restart with the new settings
            c.stop()
        self.conns = C.start_all(self.cfg)
        self.hist.items = collections.deque(self.hist.items, maxlen=int(self.cfg["history"]["keep"]))
        restart = [k for k in ("server", "api")
                   if json.dumps(self.cfg.get(k), sort_keys=True, default=str) != before[k]]
        return restart

    def load_async(self, name):
        def go():
            self.models.state[name] = "carregando"
            try:
                self.models.get(name)
            except Exception as e:
                from .models import NeedKey
                self.models.state[name] = "precisa da chave" if isinstance(e, NeedKey) else f"erro: {e}"
        threading.Thread(target=go, daemon=True).start()

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
            if u.path == "/static/icon.svg":
                return self._send(200, (UI.parent / "static" / "icon.svg").read_bytes(), "image/svg+xml")
            if u.path.startswith(("/static/fonts/", "/static/img/")):   # bundled fonts / public-domain art
                sub, name = u.path.split("/")[2], u.path.rsplit("/", 1)[1]
                types = {".woff2": "font/woff2", ".webp": "image/webp", ".json": "application/json"}
                ext = os.path.splitext(name)[1]
                f = UI.parent / "static" / sub / name
                if "/" in name or ".." in name or ext not in types or not f.is_file():
                    return self._send(404 if ext != ".json" else 200, {"error": "não encontrado"} if ext != ".json" else [])
                return self._send(200, f.read_bytes(), types[ext], {"Cache-Control": "max-age=86400"})
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
            if u.path == "/api/models":
                ms = app.models.describe()
                return self._send(200, {"models": ms, "device": app.cfg["device"], "threads": app.cfg["threads"],
                                        "embedded_key": getattr(app.models, "embedded", False),
                                        "needs_key": any(m["state"] == "precisa da chave" for m in ms)})
            if u.path == "/api/config":
                return self._send(200, app.settings())
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
                if u.path.startswith("/api/models/"):
                    act, name = u.path.rsplit("/", 1)[1], b.get("name", "")
                    M = app.models
                    if act == "save":
                        if name in M.loaded():
                            M.unload(name)
                        M.configure(name, b, app.save_config)
                        if b.get("load") and M.cfg["models"][name].get("enabled", True):
                            app.load_async(name)
                    elif act == "load":
                        M.configure(name, {"enabled": True}, app.save_config); app.load_async(name)
                    elif act == "import":
                        out = M.import_folder(name, str(b.get("folder", "")), app.save_config)
                        app.load_async(name)
                        return self._send(200, {"ok": True, "file": out, "models": M.describe()})
                    elif act == "unload":
                        M.unload(name)
                    elif act == "remove":
                        M.remove(name, app.save_config)
                    elif act == "key":
                        from .crypto import BadKey
                        try:
                            saved = M.set_key(str(b.get("key", "")), bool(b.get("remember")))
                        except BadKey:
                            return self._send(400, {"error": "chave errada para os modelos encriptados"})
                        for m in M.describe():
                            if m["state"] == "precisa da chave":
                                app.load_async(m["name"])
                        return self._send(200, {"ok": True, "remembered": saved})
                    elif act == "download":
                        from .models import download
                        threading.Thread(target=lambda: download(app.cfg, [name] if name else None),
                                         daemon=True).start()
                    elif act == "settings":
                        if b.get("device") in ("auto", "cpu", "cuda"):
                            app.cfg["device"] = b["device"]
                        if str(b.get("threads", "")).isdigit():
                            app.cfg["threads"] = int(b["threads"])
                        app.save_config()
                    else:
                        return self._send(404, {"error": "ação desconhecida"})
                    return self._send(200, {"ok": True, "models": M.describe()})
                if u.path == "/api/config":
                    return self._send(200, {"ok": True, "restart": app.save_settings(b)})
                if u.path.startswith("/api/pending/"):
                    return self._send(200, app.actions.confirm(u.path.rsplit("/", 1)[1], bool(b.get("approve"))))
                if u.path == "/api/trees":
                    return self._send(200, {"trees": app.reload_trees(b["yaml"])})
            except (TreeError, yaml.YAMLError, KeyError, ValueError, PermissionError) as e:
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
