"""Sentinel service for the Chrome extension: http://127.0.0.1:8760/v1/sentinel (Bearer = pairing code).

The Sentinel model ships inside the installer (modelos/sentinel) and is loaded only when the extension first
asks something; on computers with little memory it is unloaded again after a few idle minutes.
It is a security service only: it does not appear in the decision trees.
"""
from __future__ import annotations

import hmac
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = "sentinel"


class SentinelService:
    def __init__(self, app):
        self.app, self.cfg = app, app.cfg
        self.last = 0.0
        self.lock = threading.Lock()
        self.srv = None

    @property
    def conf(self):
        return self.cfg.get("sentinel") or {}

    def ensure(self):
        """Loads the Sentinel model if needed and returns the internal URL of its full API."""
        M = self.app.models
        with self.lock:
            if NAME not in M.loaded():
                M.state[NAME] = "carregando"
                try:
                    M.get(NAME)
                except Exception as e:
                    M.state[NAME] = f"erro: {e}"
                    raise
        self.last = time.time()
        return M.apis.get(NAME)

    def idle_loop(self):
        while True:
            time.sleep(30)
            mins = float(self.conf.get("unload_after_min", 0) or 0)
            if mins and NAME in self.app.models.loaded() and time.time() - self.last > mins * 60:
                self.app.models.unload(NAME)

    def start(self):
        port = int(self.conf.get("port", 8760))
        svc, token = self, self.cfg["server"]["token"]

        class H(BaseHTTPRequestHandler):
            server_version = "DjorgeSentinelLocal"

            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b)

            def _auth(self):
                h = self.headers.get("Authorization", "")
                return h.startswith("Bearer ") and hmac.compare_digest(h[7:].strip(), token)

            def do_GET(self):
                if self.path == "/health":
                    st = svc.app.models.state.get(NAME, "não carregado")
                    return self._send(200, {"ok": True, "model": st})
                self._send(404, {"error": "não encontrado"})

            def do_POST(self):
                if not self._auth():
                    return self._send(401, {"error": "código de pareamento inválido"})
                if self.path != "/v1/sentinel":
                    return self._send(404, {"error": "não encontrado"})
                n = int(self.headers.get("Content-Length") or 0)
                if n > 200_000:
                    return self._send(413, {"error": "texto grande demais"})
                body = self.rfile.read(n)
                try:
                    url = svc.ensure()
                    req = urllib.request.Request(url + "/v1/sentinel", data=body, method="POST",
                                                 headers={"Content-Type": "application/json",
                                                          "Authorization": f"Bearer {token}"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        return self._send(r.status, json.loads(r.read()))
                except urllib.error.HTTPError as e:
                    return self._send(e.code, json.loads(e.read() or b"{}"))
                except Exception as e:
                    return self._send(503, {"error": f"Sentinel indisponível: {e}"})

        for p in range(port, port + 10):
            try:
                self.srv = ThreadingHTTPServer(("127.0.0.1", p), H)
                self.port = p
                break
            except OSError:
                continue
        if self.srv is None:
            return None
        threading.Thread(target=self.srv.serve_forever, daemon=True, name="sentinel-extensao").start()
        threading.Thread(target=self.idle_loop, daemon=True, name="sentinel-ocioso").start()
        return self.port
