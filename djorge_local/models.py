"""Modelos: pastas com um artefato DJORGE. O instalador NÃO traz o modelo: o DOFL (Flash) é baixado quando a pessoa
pede, pelo card do modelo (model_download.py), que grava o caminho em cfg.models.flash.path. Outros modelos podem ser
adicionados escolhendo a pasta. Sem modelo instalado nada é carregado e o app funciona só com as regras."""
from __future__ import annotations

import gc
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

from .config import HOME, resolve

log = logging.getLogger("djorge.models")


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


PROFILES_CPU = {"needs": "any computer", "onnx": "int8", "providers": ["CPUExecutionProvider"]}
GPU_PROVIDERS = ("WebGpuExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider", "CUDAExecutionProvider")


def pick_profile(art: Path, cfg) -> dict:
    """How this computer runs this model. Local packages (local_profiles.json) use ONNX Runtime:
    fp16 on a GPU of any brand when available (and not turned off), int8 on the CPU otherwise.
    Plain artifacts (training output, no onnx/) fall back to PyTorch and are converted once in the background
    (otimizar.py). A package with onnx/encoder.json but no local_profiles.json is still run by ONNX (CPU int8)."""
    import json as _j
    lp = art / "local_profiles.json"
    from .otimizar import eh_pacote_onnx
    if not lp.is_file():
        try:
            files = _j.load(open(art / "onnx" / "encoder.json", encoding="utf-8")).get("files", {})
        except Exception:
            files = {}
        if not any((art / f).is_file() for f in files.values()):
            return {"engine": "pytorch", "label": "PyTorch"}
        prof = {"cpu": PROFILES_CPU}
    else:
        prof = _j.load(open(lp, encoding="utf-8")).get("profiles") or {"cpu": PROFILES_CPU}
        if not eh_pacote_onnx(art) and not (art / "onnx").is_dir():
            return {"engine": "pytorch", "label": "PyTorch"}
    try:
        import onnxruntime as ort
        avail = ort.get_available_providers()
    except Exception:                       # local packages carry no PyTorch backbone: ONNX Runtime is required
        raise RuntimeError("este modelo é um pacote Local e precisa do ONNX Runtime (reinstale o Djorge Local)")
    want_gpu = cfg.get("device", "auto") in ("auto", "cuda")
    gpu = [p for p in GPU_PROVIDERS if p in avail]
    if want_gpu and gpu and "gpu" in prof and (art / "onnx" / "encoder.fp16.onnx").is_file():
        return {"engine": "onnx", "onnx": "fp16", "providers": gpu + ["CPUExecutionProvider"],
                "label": f"GPU · {gpu[0].replace('ExecutionProvider', '')} · fp16"}
    return {"engine": "onnx", "onnx": "int8", "providers": ["CPUExecutionProvider"], "label": "CPU · int8 (ONNX)"}


class Models:
    NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

    def __init__(self, cfg):
        self.cfg, self._z, self._lock = cfg, {}, threading.Lock()
        self.state = {}                     # name -> "carregando" | "pronto" | "erro: ..."
        self.apis, self._servers = {}, {}
        self.profile = {}                   # name -> how this machine runs it (GPU fp16 / CPU int8 / PyTorch)
        self.engine = {}                    # name -> "ONNX int8" | "ONNX fp16" | "PyTorch"
        self.last_used = {}                 # name -> time.time() do último uso (descarregar ocioso)
        self._dormindo = set()              # descarregados por ociosidade: recarregam sozinhos no próximo uso
        self._api_on = {}                   # name -> True/False: API local ligada/desligada pela interface
        self.motores = {}                   # name -> Motor (fachada: lote, cache, aquecimento); sobrevive ao
                                            # descarregar ocioso e recarrega o modelo sozinho no próximo pedido

    def get(self, name="flash"):
        if name not in self.cfg["models"] or not self.cfg["models"][name].get("enabled", True):
            raise KeyError(f"modelo desligado ou desconhecido: {name}")
        self.last_used[name] = time.time()
        self._dormindo.discard(name)
        _atividade()
        with self._lock:
            if name not in self._z:
                m = self.cfg["models"][name]
                art = model_path(self.cfg, m.get("path", ""))
                if not m.get("path") or not is_model(art):
                    raise FileNotFoundError(f"modelo {name}: pasta não encontrada ({art}). Escolha a pasta na aba Modelos.")
                prof = pick_profile(art, self.cfg)
                if prof["engine"] != "onnx":
                    self._maybe_optimize(name, art)
                    from . import otimizar as O
                    if O.OTIMIZADOR.info(art).get("state") == "otimizando":
                        # não carrega o PyTorch junto com a conversão (memória em dobro): o app usa só as regras
                        # por alguns minutos e recarrega sozinho em ONNX int8 ao terminar
                        self.state[name] = "otimizando"
                        raise RuntimeError(O.MENSAGEM)
                from djorge import Djorge, DjorgeConfig
                for k in ("DJORGE_ONNX", "DJORGE_ONNX_PROVIDERS"):
                    os.environ.pop(k, None)
                if prof["engine"] == "onnx":                  # ONNX Runtime: nada de torch neste caminho
                    os.environ["DJORGE_ONNX"] = prof["onnx"]
                    os.environ["DJORGE_ONNX_PROVIDERS"] = ",".join(prof["providers"])
                    os.environ.setdefault("DJORGE_THREADS", str(int(self.cfg.get("threads") or 0) or _threads_padrao()))
                    dev = "cpu"
                else:
                    try:
                        import torch
                    except ImportError:
                        raise RuntimeError("este modelo está no formato de treino (PyTorch) e o PyTorch não vem no "
                                           "instalador: baixe o pacote leve (ONNX int8) do modelo") from None
                    if self.cfg.get("threads"):
                        torch.set_num_threads(int(self.cfg["threads"]))
                    dev = self.cfg.get("device", "auto")
                    if dev == "auto":
                        dev = "cuda" if torch.cuda.is_available() else "cpu"
                c = DjorgeConfig(artifact=str(art), device=dev)
                c.context_dir = str(HOME / "contextos" / name)
                try:
                    z = Djorge(artifact=str(art), config=c)
                except Exception:
                    if prof.get("onnx") == "fp16":            # GPU path failed on this machine -> CPU int8
                        os.environ["DJORGE_ONNX"], os.environ["DJORGE_ONNX_PROVIDERS"] = "int8", "CPUExecutionProvider"
                        prof = {"engine": "onnx", "onnx": "int8", "label": "CPU · int8 (ONNX, GPU falhou)"}
                        z = Djorge(artifact=str(art), config=c)
                    else:
                        raise
                eng = getattr(z.semantic, "engine", z.semantic)
                ox = (getattr(eng, "meta", {}) or {}).get("onnx")
                if ox and prof["engine"] == "onnx":
                    prof["label"] = prof["label"].split(" · ")[0] + f" · {ox['file'].split('.')[-2]} · {ox['provider'].replace('ExecutionProvider', '')}"
                self.profile[name] = prof["label"]
                self.engine[name] = "ONNX int8" if prof.get("onnx") == "int8" else (
                    "ONNX fp16" if prof["engine"] == "onnx" else "PyTorch")
                z.config.sentinel_endpoint = bool(m.get("service"))     # /v1/sentinel only on the Sentinel
                self._ligar_motor(name, z)
                self._z[name] = z
                self.state[name] = "pronto"
                self._start_api(name, z)
            return self._z[name]

    # ------------------------------------------------------------------------------------ motor --
    def motor(self, name="flash"):
        """O Motor do modelo (cria se preciso; carrega o modelo só no primeiro pedido que não estiver no cache)."""
        mo = self.motores.get(name)
        if mo is None:
            from .motor import Motor
            conf = dict(self.cfg.get("motor") or {})
            mo = Motor(carregar=lambda n=name: self.get(n).semantic, ao_usar=lambda n=name: self._usou(n), **conf)
            self.motores[name] = mo
        return mo

    def _usou(self, name):
        self.last_used[name] = time.time()
        _atividade()

    def _ligar_motor(self, name, z):
        if (self.cfg.get("motor") or {}).get("ativo", True) is False or z.semantic is None:
            return
        from .motor import Motor
        if isinstance(z.semantic, Motor):
            return
        mo = self.motor(name)
        mo.trocar(z.semantic)
        z.semantic = mo
        try:
            z.executor.sem = mo
        except Exception:
            pass
        mo.aquecer()

    def motor_info(self, name="flash") -> dict:
        mo = self.motores.get(name)
        return mo.metricas() if mo is not None else {}

    # ------------------------------------------------------------ conversão para ONNX (uma vez) --
    def _maybe_optimize(self, name, art):
        """Artefato de treino (sem onnx/): converte em segundo plano para ONNX int8 e recarrega ao terminar.
        Enquanto isso (ou se não der) o modelo roda em PyTorch, quando o PyTorch existe. Só pastas baixadas pelo
        app (%LOCALAPPDATA%/Djorge/modelos): uma pasta escolhida pela pessoa nunca é substituída."""
        if (self.cfg.get("leveza") or {}).get("otimizar_modelo", True) is False:
            return
        from . import otimizar as O
        if not O.precisa_otimizar(art) or not O.pasta_gerenciada(art):
            return

        def antes():
            self.unload(name)

        def depois(ok):
            self.state.pop(name, None)
            self.preload([name])              # ok -> ONNX int8; falhou -> PyTorch (se houver)
        O.OTIMIZADOR.iniciar(art, antes_de_trocar=antes, depois=depois)

    def optimizing(self, name="flash") -> dict:
        m = (self.cfg.get("models") or {}).get(name) or {}
        if not m.get("path"):
            return {}
        from . import otimizar as O
        return O.OTIMIZADOR.info(model_path(self.cfg, m["path"]))

    # ------------------------------------------------------------------- API local liga/desliga --
    def _start_api(self, name, z):
        """Full DJORGE HTTP API for this model, local only, Bearer token = server.token."""
        a = self.cfg["api"]
        want = self._api_on.get(name)
        if want is False or (want is None and not a.get("enabled")):
            return
        port = (a.get("ports") or {}).get(name)
        if not port or name in self._servers:
            return
        from djorge import server as S
        z.config.api_key = self.cfg["server"]["token"]
        _prepare_for_serving(z, float(a.get("batch_window_ms", 8)))
        srv = None
        for p in range(int(port), int(port) + 20):           # port busy -> next free one (shown in the UI)
            try:
                srv = S._Server((a.get("host", "127.0.0.1"), p), S.make_handler(z)); port = p
                break
            except OSError:
                continue
        if srv is None:
            self.state[name] = "pronto (API sem porta livre)"
            return
        threading.Thread(target=srv.serve_forever, daemon=True, name=f"api-{name}").start()
        self._servers[name] = srv
        self.apis[name] = f"http://{a.get('host', '127.0.0.1')}:{port}"

    def start_api(self, name, port=None) -> dict:
        """Liga a API local do modelo sem reiniciar (carrega o modelo se preciso)."""
        if name not in self.cfg["models"]:
            raise KeyError(f"modelo desconhecido: {name}")
        a = self.cfg.setdefault("api", {})
        ports = a.setdefault("ports", {})
        if port:
            port = int(port)
            if not 1024 <= port <= 65535:
                raise ValueError("porta: use um número entre 1024 e 65535")
            if self._servers.get(name) is not None and ports.get(name) != port:
                self.stop_api(name)
            ports[name] = port
        elif not ports.get(name):
            ports[name] = max([8749] + [int(p) for p in ports.values()]) + 1
        self._api_on[name] = True
        z = self.get(name)
        with self._lock:
            self._start_api(name, z)
        return self.api_info(name)

    def stop_api(self, name) -> dict:
        """Desliga a API local do modelo; o modelo continua carregado para o app."""
        self._api_on[name] = False
        srv = self._servers.pop(name, None)
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        self.apis.pop(name, None)
        return self.api_info(name)

    def api_info(self, name=None):
        if name is None:
            return [self.api_info(n) for n in self.cfg["models"]]
        url = self.apis.get(name)
        port = int(url.rsplit(":", 1)[1]) if url else ((self.cfg.get("api") or {}).get("ports") or {}).get(name)
        return {"modelo": name, "porta": port, "ativa": bool(url), "url": url}

    # ---------------------------------------------------------------------- leveza / ociosidade --
    def unload_idle(self, minutes: float) -> list:
        """Descarrega os modelos sem uso há `minutes` minutos (exceto os que servem a API local)."""
        if not minutes or minutes <= 0:
            return []
        lim = time.time() - minutes * 60
        out = []
        for n in list(self._z):
            if n in self._servers:
                continue
            if self.last_used.get(n, 0) < lim:
                self.unload(n)
                self._dormindo.add(n)
                self.state[n] = "descarregado (ocioso) · volta no próximo uso"
                out.append(n)
        return out

    def unload_all(self, keep_api=False, dormir=True) -> list:
        out = []
        for n in list(self._z):
            if keep_api and n in self._servers:
                continue
            self.unload(n)
            if dormir:
                self._dormindo.add(n)
            out.append(n)
        return out

    def sleep(self, names=()):
        """Marca modelos para voltarem no próximo uso (retomar depois de pausar)."""
        self._dormindo |= set(names)

    def wake_off(self):
        """Pausado: nenhum modelo volta sozinho."""
        self._dormindo.clear()

    def engine_of(self, name="flash"):
        return self.engine.get(name)

    def loaded(self):
        """Modelos prontos para uso: na memória ou "dormindo" (descarregados por ociosidade; voltam no próximo
        get(), em segundos no ONNX int8). Para saber o que está de fato na memória use in_memory()."""
        return sorted(set(self._z) | self._dormindo)

    def in_memory(self):
        return sorted(self._z)

    def preload(self, names=None):
        """Loads the enabled models in the background so the window appears immediately."""
        def go():
            for n in names or [k for k, v in self.cfg["models"].items() if v.get("enabled", True) and not v.get("service")
                               and v.get("path") and is_model(model_path(self.cfg, v["path"]))]:
                self.state[n] = "carregando"
                try:
                    self.get(n)
                except Exception as e:
                    if self.state.get(n) != "otimizando":
                        self.state[n] = f"erro: {e}"
        threading.Thread(target=go, daemon=True, name="carregar-modelos").start()

    # ---------------------------------------------------------------- management (Models tab) --
    def describe(self):
        out = []
        for name, m in self.cfg["models"].items():
            p = m.get("path", "")
            found = bool(p) and is_model(model_path(self.cfg, p))
            st = self.state.get(name) or ("pronto" if name in self._z else ("carrega quando a extensão pedir" if m.get("service") and found and m.get("enabled", True) else (
                "desligado" if not m.get("enabled", True) else ("não carregado" if found else "sem pasta"))))
            opt = self.optimizing(name) if found else {}
            if opt.get("state") == "otimizando":
                st = opt.get("mensagem") or st
            out.append({"name": name, "enabled": m.get("enabled", True), "role": m.get("role", ""), "service": bool(m.get("service")),
                        "engine": self.engine.get(name), "otimizacao": opt or None,
                        "path": "" if p.startswith("@app/") else p, "bundled": p.startswith("@app/"),
                        "found": found, "state": st, "api": self.apis.get(name), "profile": self.profile.get(name),
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
            def _close(s=srv):
                try:
                    s.shutdown(); s.server_close()
                except Exception:
                    pass
            threading.Thread(target=_close, daemon=True).start()
        self.apis.pop(name, None)
        self.engine.pop(name, None)
        if name in self.motores:
            self.motores[name].soltar()
        self._dormindo.discard(name)
        self.state[name] = "não carregado"
        del z
        gc.collect()

    def decision_models(self):
        return [k for k, v in self.cfg["models"].items() if not v.get("service")]

    def remove(self, name, save=None):
        self.unload(name)
        self.cfg["models"].pop(name, None)
        (self.cfg["api"].get("ports") or {}).pop(name, None)
        if save:
            save()


def _threads_padrao() -> int:
    """Leve: metade dos núcleos (mín. 1, máx. 4) para o app não disputar a CPU com o resto do computador."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def _atividade():
    try:
        from . import leveza
        leveza.atividade()
    except Exception:
        pass


def _prepare_for_serving(z, batch_window_ms: float = 8.0):
    """djorge.server.prepare_for_serving sem torch (o caminho ONNX não tem PyTorch): só o micro-lote."""
    if z.semantic is not None and batch_window_ms > 0 and not getattr(z, "_mb", False):
        from djorge.runtime.batching import MicroBatcher
        mb = MicroBatcher(z.semantic, window_ms=batch_window_ms)
        z.semantic = mb
        z.executor.sem = mb
        z._mb = True
    return z
