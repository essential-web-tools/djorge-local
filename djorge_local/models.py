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
        k = _ask_window()
    else:
        k = getpass.getpass("Chave dos modelos: ")
    if not k:
        raise PermissionError("chave dos modelos não informada")
    return k


def _ask_window() -> str:
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk(); root.withdraw()
    try:
        ico = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(ico):
            root.iconphoto(True, tk.PhotoImage(file=ico))
    except Exception:
        pass
    k = simpledialog.askstring("Djorge Local", "Chave dos modelos:", show="•", parent=root)
    if k:
        from tkinter import messagebox
        if messagebox.askyesno("Djorge Local", "Lembrar a chave neste computador (cofre do sistema)?", parent=root):
            try:
                import keyring
                keyring.set_password(KEYRING_SERVICE, "model-key", k)
            except Exception:
                messagebox.showinfo("Djorge Local", "Cofre do sistema indisponível (pip install keyring).")
    root.destroy()
    return k or ""


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
        self.apis = getattr(self, "apis", {})
        self.apis[name] = f"http://{a['host']}:{port}"

    def loaded(self):
        return sorted(self._z)

    def preload(self, names=None):
        """Loads the models in the background so the window / tray appear immediately."""
        import threading

        def go():
            for n in names or [k for k, v in self.cfg["models"].items() if v.get("enabled", True)]:
                self.state[n] = "carregando"
                try:
                    self.get(n); self.state[n] = "pronto"
                except Exception as e:
                    self.state[n] = f"erro: {e}"
        threading.Thread(target=go, daemon=True, name="carregar-modelos").start()
