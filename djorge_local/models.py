"""Model loading: plain artifact folder, or encrypted .djx (local or downloaded from Google Drive)."""
from __future__ import annotations

import atexit
import getpass
import os
import shutil
import threading
from pathlib import Path

from . import crypto
from .config import HOME, resolve

_TMP = []


@atexit.register
def _cleanup():
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


KEYRING_SERVICE = "djorge-local"


def passphrase(cfg) -> str:
    """Key of the .djx files: env var -> system keyring (if saved) -> window (executable) -> terminal."""
    k = os.environ.get(cfg["key_env"])
    if k:
        return k
    try:
        import keyring
        k = keyring.get_password(KEYRING_SERVICE, "model-key")
        if k:
            return k
    except Exception:
        pass
    if os.environ.get("DJORGE_GUI") == "1":
        raise NeedKey("informe a chave dos modelos na aba Modelos")
    k = getpass.getpass("Chave dos modelos: ")
    if not k:
        raise PermissionError("chave dos modelos não informada")
    return k


class NeedKey(PermissionError):
    pass


def remember_key(k: str) -> bool:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, "model-key", k)
        return True
    except Exception:
        return False


def download(cfg, names=None) -> dict:
    """Downloads the encrypted .djx files. Needs `pip install gdown`; the files stay encrypted on disk."""
    import gdown
    dest = HOME / "modelos"
    dest.mkdir(parents=True, exist_ok=True)
    got = {}
    for name, m in cfg["models"].items():
        if names and name not in names:
            continue
        if m.get("drive_file_id"):
            out = dest / f"{name}.djx"
            if not out.is_file():
                gdown.download(id=m["drive_file_id"], output=str(out), quiet=False)
            got[name] = str(out)
    if cfg.get("drive_folder_id") and len(got) < len(cfg["models"]):
        gdown.download_folder(id=cfg["drive_folder_id"], output=str(dest), quiet=False, use_cookies=False)
        for p in dest.rglob("*.djx"):
            for name in cfg["models"]:
                if p.stem.lower().startswith(name) and name not in got:
                    got[name] = str(p)
    return got


class Models:
    """Lazy loading; one lock per model (the runtime batches internally)."""

    def __init__(self, cfg):
        self.cfg, self._z, self._lock = cfg, {}, threading.Lock()
        self._key = None
        self.state = {}                     # name -> "carregando" | "pronto" | "erro: ..."

    def _artifact(self, name):
        m = self.cfg["models"][name]
        if m.get("path"):
            return str(resolve(self.cfg, m["path"]))
        sealed = m.get("sealed") or (HOME / "modelos" / f"{name}.djx")
        sealed = resolve(self.cfg, str(sealed))
        if not sealed.is_file() and (m.get("drive_file_id") or self.cfg.get("drive_folder_id")):
            sealed = Path(download(self.cfg, [name]).get(name, sealed))
        if not sealed.is_file():
            raise FileNotFoundError(f"modelo {name}: configure 'path' (pasta) ou 'sealed' (.djx) ou o Drive")
        self._key = self._key or passphrase(self.cfg)
        d = crypto.open_sealed(str(sealed), self._key)
        _TMP.append(d)
        return d

    def get(self, name="origo"):
        if name not in self.cfg["models"] or not self.cfg["models"][name].get("enabled", True):
            raise KeyError(f"modelo desativado ou desconhecido: {name}")
        with self._lock:
            if name not in self._z:
                import torch
                from djorge import Djorge, DjorgeConfig
                if self.cfg.get("threads"):
                    torch.set_num_threads(int(self.cfg["threads"]))
                dev = self.cfg["device"]
                if dev == "auto":
                    dev = "cuda" if torch.cuda.is_available() else "cpu"
                art = self._artifact(name)
                c = DjorgeConfig(artifact=art, device=dev)
                c.context_dir = str(HOME / "contextos" / name)
                z = Djorge(artifact=art, config=c)
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
        srv = S._Server((a["host"], int(port)), S.make_handler(z))
        threading.Thread(target=srv.serve_forever, daemon=True, name=f"api-{name}").start()
        self._servers = getattr(self, "_servers", {})
        self._servers[name] = srv
        self.apis = getattr(self, "apis", {})
        self.apis[name] = f"http://{a['host']}:{port}"

    def loaded(self):
        return sorted(self._z)

    # ---------------------------------------------------------------- management (Models tab) --
    NAME = __import__("re").compile(r"^[a-z][a-z0-9_-]{0,31}$")

    def source(self, name):
        m = self.cfg["models"][name]
        if m.get("path"):
            return "pasta", m["path"]
        sealed = m.get("sealed") or str(HOME / "modelos" / f"{name}.djx")
        if Path(resolve(self.cfg, sealed)).is_file():
            return "djx", sealed
        if m.get("drive_file_id") or self.cfg.get("drive_folder_id"):
            return "drive", m.get("drive_file_id") or self.cfg.get("drive_folder_id")
        return "nenhuma", ""

    def describe(self):
        out = []
        for name, m in self.cfg["models"].items():
            kind, where = self.source(name)
            st = self.state.get(name) or ("pronto" if name in self._z else ("desligado" if not m.get("enabled", True)
                                                                             else "não carregado"))
            out.append({"name": name, "enabled": m.get("enabled", True), "role": m.get("role", ""), "source": kind,
                        "where": where, "path": m.get("path", ""), "sealed": m.get("sealed", ""),
                        "drive_file_id": m.get("drive_file_id", ""), "state": st,
                        "api": getattr(self, "apis", {}).get(name),
                        "port": (self.cfg["api"].get("ports") or {}).get(name)})
        return out

    def configure(self, name, fields: dict, save=None):
        if not self.NAME.match(name or ""):
            raise ValueError("nome: letras minúsculas, números, - ou _ (até 32)")
        m = self.cfg["models"].setdefault(name, {"enabled": True, "path": "", "sealed": "", "drive_file_id": "",
                                                  "role": ""})
        for k in ("enabled", "path", "sealed", "drive_file_id", "role"):
            if k in fields:
                m[k] = bool(fields[k]) if k == "enabled" else str(fields[k] or "").strip()
        if m.get("path") and not (Path(resolve(self.cfg, m["path"])) / "metadata" / "djorge.json").is_file():
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
            srv = getattr(self, "_servers", {}).pop(name, None)
        if srv:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        getattr(self, "apis", {}).pop(name, None)
        self.state[name] = "não carregado"
        del z
        import gc
        gc.collect()

    def remove(self, name, save=None):
        self.unload(name)
        self.cfg["models"].pop(name, None)
        (self.cfg["api"].get("ports") or {}).pop(name, None)
        if save:
            save()

    def set_key(self, k: str, remember: bool = False):
        if not k or len(k) < 10:
            raise ValueError("a chave tem pelo menos 10 caracteres")
        for name in self.cfg["models"]:
            kind, where = self.source(name)
            if kind == "djx":
                crypto.info(str(resolve(self.cfg, where)), k)     # raises BadKey when wrong
        self._key = k
        return remember_key(k) if remember else False

    def preload(self, names=None):
        """Loads the models in the background so the window / tray appear immediately."""
        import threading

        def go():
            for n in names or [k for k, v in self.cfg["models"].items() if v.get("enabled", True)]:
                self.state[n] = "carregando"
                try:
                    self.get(n); self.state[n] = "pronto"
                except NeedKey:
                    self.state[n] = "precisa da chave"
                except Exception as e:
                    self.state[n] = f"erro: {e}"
        threading.Thread(target=go, daemon=True, name="carregar-modelos").start()
