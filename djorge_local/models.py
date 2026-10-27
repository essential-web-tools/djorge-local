"""Models: folders with a DJORGE artifact. The Flash comes INSIDE the installer (folder "modelos/flash" next to
the executable); other models are added by choosing their folder in the Models tab. Nothing is downloaded."""
from __future__ import annotations

import gc
import re
import sys
import threading
from pathlib import Path

from .config import HOME, resolve


def app_dir() -> Path:
    """Folder of the installed program (next to the executable), or the source folder in development."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve().parent
        return exe
    return Path(__file__).resolve().parent.parent


def model_path(cfg, p: str) -> Path:
    """'@app/modelos/flash' -> inside the installed program; anything else -> as given (absolute or relative)."""
    if p.startswith("@app/"):
        return app_dir() / p[5:]
    return resolve(cfg, p)


def is_model(p: Path) -> bool:
    return (p / "metadata" / "djorge.json").is_file()


class Models:
    NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

    def __init__(self, cfg):
        self.cfg, self._z, self._lock = cfg, {}, threading.Lock()
        self.state = {}                     # name -> "carregando" | "pronto" | "erro: ..."
        self.apis, self._servers = {}, {}

    def get(self, name="flash"):
        if name not in self.cfg["models"] or not self.cfg["models"][name].get("enabled", True):
            raise KeyError(f"modelo desligado ou desconhecido: {name}")
        with self._lock:
            if name not in self._z:
                m = self.cfg["models"][name]
                art = model_path(self.cfg, m.get("path", ""))
                if not m.get("path") or not is_model(art):
                    raise FileNotFoundError(f"modelo {name}: pasta não encontrada ({art}). Escolha a pasta na aba Modelos.")
                import torch
                from djorge import Djorge, DjorgeConfig
                if self.cfg.get("threads"):
                    torch.set_num_threads(int(self.cfg["threads"]))
                dev = self.cfg["device"]
                if dev == "auto":
                    dev = "cuda" if torch.cuda.is_available() else "cpu"
                c = DjorgeConfig(artifact=str(art), device=dev)
                c.context_dir = str(HOME / "contextos" / name)
                z = Djorge(artifact=str(art), config=c)
                self._z[name] = z
                self.state[name] = "pronto"
                self._start_api(name, z)
            return self._z[name]

    def _start_api(self, name, z):
        """Full DJORGE HTTP API for this model, local only, Bearer token = server.token."""
        a = self.cfg["api"]
        port = (a.get("ports") or {}).get(name)
        if not a.get("enabled") or not port:
            return
        from djorge import server as S
        z.config.api_key = self.cfg["server"]["token"]
        S.prepare_for_serving(z, float(a.get("batch_window_ms", 8)), int(self.cfg.get("threads") or 0), warmup=False)
        srv = None
        for p in range(int(port), int(port) + 20):           # port busy -> next free one (shown in the UI)
            try:
                srv = S._Server((a["host"], p), S.make_handler(z)); port = p
                break
            except OSError:
                continue
        if srv is None:
            self.state[name] = "pronto (API sem porta livre)"
            return
        threading.Thread(target=srv.serve_forever, daemon=True, name=f"api-{name}").start()
        self._servers[name] = srv
        self.apis[name] = f"http://{a['host']}:{port}"

    def loaded(self):
        return sorted(self._z)

    def preload(self, names=None):
        """Loads the enabled models in the background so the window appears immediately."""
        def go():
            for n in names or [k for k, v in self.cfg["models"].items() if v.get("enabled", True)]:
                self.state[n] = "carregando"
                try:
                    self.get(n)
                except Exception as e:
                    self.state[n] = f"erro: {e}"
        threading.Thread(target=go, daemon=True, name="carregar-modelos").start()

    # ---------------------------------------------------------------- management (Models tab) --
    def describe(self):
        out = []
        for name, m in self.cfg["models"].items():
            p = m.get("path", "")
            found = bool(p) and is_model(model_path(self.cfg, p))
            st = self.state.get(name) or ("pronto" if name in self._z else (
                "desligado" if not m.get("enabled", True) else ("não carregado" if found else "sem pasta")))
            out.append({"name": name, "enabled": m.get("enabled", True), "role": m.get("role", ""),
                        "path": "" if p.startswith("@app/") else p, "bundled": p.startswith("@app/"),
                        "found": found, "state": st, "api": self.apis.get(name),
                        "port": (self.cfg["api"].get("ports") or {}).get(name)})
        return out

    def configure(self, name, fields: dict, save=None):
        if not self.NAME.match(name or ""):
            raise ValueError("nome: letras minúsculas, números, - ou _ (até 32)")
        m = self.cfg["models"].setdefault(name, {"enabled": True, "path": "", "role": ""})
        for k in ("enabled", "path", "role"):
            if k in fields:
                v = fields[k]
                if k == "path" and not str(v or "").strip() and m.get("path", "").startswith("@app/"):
                    continue                                   # empty field keeps the bundled model
                m[k] = bool(v) if k == "enabled" else str(v or "").strip()
        if m.get("path") and not m["path"].startswith("@app/") and not is_model(model_path(self.cfg, m["path"])):
            raise ValueError("essa pasta não é um modelo DJORGE (falta metadata/djorge.json)")
        ports = self.cfg["api"].setdefault("ports", {})
        if name not in ports:
            ports[name] = max([8749] + [int(p) for p in ports.values()]) + 1
        if save:
            save()
        return m

    def unload(self, name):
        with self._lock:
            z = self._z.pop(name, None)
            srv = self._servers.pop(name, None)
        if srv:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        self.apis.pop(name, None)
        self.state[name] = "não carregado"
        del z
        gc.collect()

    def remove(self, name, save=None):
        self.unload(name)
        self.cfg["models"].pop(name, None)
        (self.cfg["api"].get("ports") or {}).pop(name, None)
        if save:
            save()
