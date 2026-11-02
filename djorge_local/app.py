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
from .command import ModeloAusente
from .tree import Runner, TreeError, matches, validate

UI = Path(__file__).with_name("ui.html")
STATIC = UI.parent / "static"


def _home() -> Path:
    """HOME atual (os testes recarregam djorge_local.config com outra pasta)."""
    from . import config as _c
    return _c.HOME


class Unavailable(Exception):
    """Módulo opcional (integrações, voz, árvores visuais, download do modelo) ausente ou com falha -> 503."""


class History:
    def __init__(self, cfg):
        self.cfg = cfg
        self.items = collections.deque(maxlen=int(cfg["history"]["keep"]))
        self.lock = threading.Lock()
        self.file = _home() / "historico.jsonl"
        _home().mkdir(parents=True, exist_ok=True)

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
        from .actions import desktop_notify
        from .security import SecurityCheck
        self.security = SecurityCheck(cfg, self.models, desktop_notify)
        from .rules import RuleBuilder
        self.rules = RuleBuilder(cfg, self.models)
        self.trees, self.conns = [], {}
        self.reload_trees()
        from .cards import Cards
        from .command import Command
        from .search import Search
        from .uiprefs import UIPrefs
        self.cards = Cards(_home() / "cards.db")
        self.ui = UIPrefs(_home() / "ui.json")
        self.search = Search(self)
        self.command = Command(self)
        self._svc, self._svc_err = {}, {}
        from .actions import desktop_notify as _dn
        self._load_service("integrations", lambda: __import__("djorge_local.integrations", fromlist=["x"])
                           .Integrations(cfg, self.cards, _dn))
        self._load_service("voice", lambda: __import__("djorge_local.voice", fromlist=["x"]).Voice(cfg))
        self._load_service("treegraph", lambda: __import__("djorge_local.treegraph", fromlist=["x"]))
        self._load_service("model", lambda: __import__("djorge_local.model_download", fromlist=["x"])
                           .ModelDownloader(cfg, self.models, save=self.save_config))

    def _load_service(self, name, make):
        try:
            self._svc[name] = make()
        except Exception as e:                       # o app continua; a rota responde 503
            self._svc_err[name] = f"{type(e).__name__}: {e}"

    @property
    def integrations(self):
        return self._svc.get("integrations")

    def pause_integrations(self, paused: bool = True) -> dict:
        """Para a bandeja: pausa/retoma a sincronização em segundo plano das integrações."""
        it = self.service("integrations")
        if hasattr(it, "pause"):
            return it.pause(paused) or {"ok": True}
        (it.stop_background if paused else it.start_background)()
        return {"ok": True, "paused": paused}

    def service(self, name):
        if name not in self._svc:
            raise Unavailable(f"{name} indisponível: {self._svc_err.get(name, 'módulo ausente')}")
        return self._svc[name]

    def has_model(self) -> bool:
        return self.command.engine() is not None

    def save_tree(self, tree: dict) -> dict:
        """Grava (substitui pelo nome ou acrescenta) uma árvore em arvores.yaml, guardando o .bak."""
        validate([tree])
        p = self.trees_path()
        data = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else None) or {}
        if p.is_file():
            p.with_suffix(p.suffix + ".bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        trees = data.setdefault("trees", [])
        for i, t in enumerate(trees):
            if t.get("name") == tree["name"]:
                trees[i] = tree; break
        else:
            trees.append(tree)
        self.reload_trees(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        return {"saved": tree["name"], "trees": len(self.trees)}

    def save_config(self):
        """Writes back the editable parts (models, api ports, device, threads); everything else is preserved."""
        p = Path(self.cfg["_path"])
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else {}
        raw = raw or {}
        raw["models"] = {k: {f: v[f] for f in ("enabled", "path", "role") if f in v}
                         for k, v in self.cfg["models"].items()}
        raw.setdefault("api", {})["ports"] = dict(self.cfg["api"].get("ports") or {})
        raw["device"], raw["threads"] = self.cfg["device"], self.cfg["threads"]
        p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

    EDITABLE = ("timezone", "language", "device", "threads", "open_panel_on_start", "seguranca", "sentinel", "server", "api",
                "connectors", "extract", "actions", "history", "max_steps", "economy")

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
        raw["models"] = {k: {f: v[f] for f in ("enabled", "path", "role") if f in v}
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
                self.models.state[name] = f"erro: {e}"
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

    def add_rule(self, tree: dict, answers: dict = None) -> dict:
        """Appends a rule made by RuleBuilder (after the person saw the summary) to arvores.yaml."""
        import copy
        from .rules import summarize
        t = copy.deepcopy(tree)
        node = t["nodes"][t["start"]]
        a = answers or {}
        if a.get("folder") and node.get("action") == "email_move":
            node["folder"] = str(a["folder"]).strip()[:100]
        if node.get("action") == "email_move" and not node.get("folder"):
            raise ValueError("falta a pasta de destino")
        p = self.trees_path()
        data = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else None) or {}
        if p.is_file():                                   # the old file (with its comments) stays as .bak
            p.with_suffix(p.suffix + ".bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        trees = data.setdefault("trees", [])
        names = {x.get("name") for x in trees}
        base, i = t["name"], 2
        while t["name"] in names:
            t["name"] = f"{base}-{i}"; i += 1
        trees.append(t)
        self.reload_trees(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        return {"added": t["name"], "summary": summarize(t), "trees": len(self.trees)}

    def handle(self, ev: dict, only: str = None) -> dict:
        out = []
        if not ev.get("skipped"):
            self.security.check(ev)                 # every event: seguro | spam | golpe | phishing | malware
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
                "api": getattr(self.models, "apis", {}), "sentinel_port": getattr(self, "sentinel_port", None),
                "connectors": {k: c.status for k, c in self.conns.items()},
                "trees": [t["name"] for t in self.trees], "pending": len(self.actions.pending),
                "queue": C.EVENTS.qsize(), "security": self.security.stats}

    def start(self, workers: int = 1):
        self.conns = C.start_all(self.cfg)
        if (self.cfg.get("sentinel") or {}).get("enabled", True) and "sentinel" in self.cfg["models"] \
                and self.cfg["models"]["sentinel"].get("enabled", True):
            from .sentinel_service import SentinelService
            self.sentinel = SentinelService(self)
            self.sentinel_port = self.sentinel.start()
        for _ in range(max(1, workers)):
            threading.Thread(target=self.worker, daemon=True).start()
        if self.integrations is not None:
            try:
                self.integrations.start_background()
            except Exception as e:
                self._svc_err["integrations_background"] = f"{type(e).__name__}: {e}"


def _q1(q, k, default=None):
    v = q.get(k)
    return v[0] if v else default


def v2_get(app: App, path: str, q: dict):
    """Rotas GET da v2. Devolve (código, objeto) ou None."""
    if path == "/api/cards":
        return 200, {"cards": app.cards.list(q=_q1(q, "q"), kind=_q1(q, "kind"), column=_q1(q, "column"),
                                             tag=_q1(q, "tag"), frm=_q1(q, "from"), to=_q1(q, "to"),
                                             source=_q1(q, "source"), priority=_q1(q, "priority"),
                                             done=_q1(q, "done"), person=_q1(q, "person"))}
    if path.startswith("/api/cards/"):
        c = app.cards.get(path.rsplit("/", 1)[1])
        return (200, c) if c else (404, {"error": "card não encontrado"})
    if path == "/api/agenda":
        return 200, {"cards": app.cards.agenda(_q1(q, "from"), _q1(q, "to"))}
    if path == "/api/search":
        return 200, app.search.search(_q1(q, "q", ""), _q1(q, "scope", "all"), engine=app.command.engine())
    if path == "/api/ui":
        from .uiprefs import ACCENTS, CHOICES, SIDEBAR
        return 200, {"prefs": app.ui.get(), "choices": dict(CHOICES, accent_colors=ACCENTS, sidebar=list(SIDEBAR))}
    if path == "/api/model/info":
        return 200, app.service("model").info()
    if path == "/api/integrations":
        return 200, {"integrations": app.service("integrations").list()}
    if path == "/api/voice/status":
        return 200, app.service("voice").status()
    if path == "/api/trees/graph":
        tg = app.service("treegraph")
        return 200, {"graphs": [dict(tg.tree_to_graph(t), name=t["name"]) for t in app.trees]}
    return None


def v2_post(app: App, path: str, b: dict):
    """Rotas POST da v2. Devolve (código, objeto) ou None."""
    if path == "/api/cards":
        return 200, app.cards.create(b)
    if path == "/api/cards/move":
        return 200, app.cards.move(b["id"], b.get("column"), b.get("order"), b.get("start"))
    if path == "/api/cards/reorder":
        return 200, {"cards": app.cards.reorder(b["column"], list(b["ids"]))}
    if path.startswith("/api/cards/"):
        parts = path.split("/")
        if len(parts) == 5 and parts[4] == "delete":
            return 200, {"ok": app.cards.delete(parts[3])}
        if len(parts) == 4:
            return 200, app.cards.update(parts[3], b)
        return None
    if path == "/api/command":
        return 200, app.command.run(b.get("text", ""), b.get("context"))
    if path == "/api/command/confirm":
        return 200, app.command.confirm(str(b.get("id", "")), bool(b.get("approve")), b.get("edits"))
    if path == "/api/triage":
        return 200, app.command.triage(b.get("text", ""), b.get("remetente"), b.get("assunto"))
    if path == "/api/ui":
        return 200, {"prefs": app.ui.set(b.get("patch", b) if isinstance(b.get("patch"), dict) else b)}
    if path.startswith("/api/model/"):
        act = path.rsplit("/", 1)[1]
        md = app.service("model")
        if act == "download":
            return 200, md.start()
        if act == "cancel":
            return 200, md.cancel()
        if act == "remove":
            return 200, md.remove()
        return None
    if path.startswith("/api/integrations/"):
        parts = path.split("/")
        if len(parts) != 5:
            return None
        integ, pid, act = app.service("integrations"), parts[3], parts[4]
        if act == "configure":
            return 200, integ.configure(pid, b.get("values", b)) or {"ok": True}
        if act == "connect":
            return 200, integ.connect(pid)
        if act == "disconnect":
            return 200, integ.disconnect(pid, forget=bool(b.get("forget"))) or {"ok": True}
        if act == "sync":
            return 200, integ.sync(pid)
        if act == "send":
            kw = {k: b[k] for k in ("template", "lang", "params") if b.get(k)}
            return 200, integ.send(pid, b["to"], b["body"], subject=b.get("subject"), **kw)
        return None
    if path.startswith("/api/voice/"):
        act = path.rsplit("/", 1)[1]
        v = app.service("voice")
        if act == "speak":
            return 200, v.speak(str(b.get("text", "")), voice=b.get("voice"), rate=b.get("rate")) or {"ok": True}
        if act == "stop":
            return 200, v.stop() or {"ok": True}
        if act == "transcribe":
            import base64
            from .voice import VoiceError
            try:
                audio = base64.b64decode(str(b.get("audio_b64", "")), validate=False)
                return 200, v.transcribe(audio, lang=b.get("lang", "pt"))
            except VoiceError as e:
                return 400, {"error": str(e)}
        if act == "setup":
            return 200, v.ensure_stt() or {"ok": True}
        return None
    if path == "/api/trees/graph":
        tg = app.service("treegraph")
        tree = tg.graph_to_tree(b["graph"])
        name = b.get("name") or tree.get("name") or (b["graph"].get("name") if isinstance(b["graph"], dict) else None)
        if not name:
            raise ValueError("a árvore precisa de um nome")
        tree["name"] = str(name)
        return 200, app.save_tree(tree)
    return None


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
            if u.path == "/static/tree-editor.js" or u.path.startswith("/static/app/"):
                name = u.path.rsplit("/", 1)[1]
                ext = os.path.splitext(name)[1]
                types = {".js": "application/javascript", ".css": "text/css"}
                if u.path == "/static/app/recorder.js":
                    try:
                        from .voice import RECORDER_JS
                        return self._send(200, RECORDER_JS.encode(), types[".js"])
                    except Exception as e:
                        return self._send(503, {"error": f"voz indisponível: {type(e).__name__}: {e}"})
                f = STATIC / name if u.path == "/static/tree-editor.js" else STATIC / "app" / name
                if ".." in name or ext not in types or not f.is_file():
                    return self._send(404, {"error": "não encontrado"})
                return self._send(200, f.read_bytes(), types[ext])
            if u.path == "/oauth/callback":              # sem token: o state é validado pelas integrações
                try:
                    html = app.service("integrations").oauth_callback(u.query)
                except Unavailable as e:
                    return self._send(503, {"error": str(e)})
                return self._send(200, html.encode() if isinstance(html, str) else html, "text/html")
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
                                        "app_dir": str(__import__("djorge_local.models", fromlist=["x"]).app_dir()),
                                        "sentinel_port": getattr(app, "sentinel_port", None)})
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
            try:
                r = v2_get(app, u.path, q)
            except Unavailable as e:
                return self._send(503, {"error": str(e)})
            except (KeyError, ValueError) as e:
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            if r is not None:
                return self._send(*r)
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
                    try:
                        z = app.models.get(b.get("model") or ([x for x in app.models.loaded() if x in app.models.decision_models()] or ["flash"])[0])
                    except (FileNotFoundError, KeyError, ImportError):
                        return self._send(409, {"error": "modelo_ausente"})
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
                    elif act == "unload":
                        M.unload(name)
                    elif act == "remove":
                        M.remove(name, app.save_config)
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
                if u.path == "/api/rules/preview":            # text -> proposed tree (nothing is saved)
                    try:
                        return self._send(200, app.rules.from_text(b.get("text", "")))
                    except (FileNotFoundError, ImportError):
                        return self._send(409, {"error": "modelo_ausente"})
                if u.path == "/api/rules/add":                # the person approved the summary
                    return self._send(200, app.add_rule(b["tree"], b.get("answers")))
                r = v2_post(app, u.path, b)
                if r is not None:
                    return self._send(*r)
            except Unavailable as e:
                return self._send(503, {"error": str(e)})
            except ModeloAusente as e:
                return self._send(409, {"error": "modelo_ausente", "tool": str(e)})
            except (TreeError, yaml.YAMLError, KeyError, ValueError, PermissionError, FileNotFoundError) as e:
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
