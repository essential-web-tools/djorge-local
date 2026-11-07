"""Modo leve: o Djorge em segundo plano gastando o mínimo, e um jeito fácil de desligá-lo e liberar memória.

Interface (as rotas /api/app/* chamam estas funções; a bandeja também):
    pausar(app)          -> recursos(app)   para conectores, integrações e secretário; descarrega os modelos
    retomar(app)         -> recursos(app)   religa o que estava ligado (o modelo carrega no próximo uso)
    liberar_memoria(app) -> {"antes_mb","depois_mb","liberado_mb","descarregados":[...]}
                            descarrega modelos ociosos, gc e (Windows) EmptyWorkingSet
    recursos(app)        -> {"pausado", "rodando":[...], "memoria_mb", "modelo":{"carregado","motor","nome",
                            "estado"}, "prioridade", "config":{...}}
    ler_config() / salvar_config(patch) -> configuração do modo leve (%LOCALAPPDATA%/Djorge/leveza.json)
    iniciar_com_windows(ativo, minimizado=True) / no_inicio_do_windows()   (HKCU\\...\\Run, sem admin)
    Vigia(app).start()   prioridade baixa quando ocioso e descarregar o modelo após N minutos sem uso
    atividade()          marca uso (chamado por Models.get): volta à prioridade normal
    encerrar(app)        Sair de verdade: para tudo e fecha o processo (a bandeja chama)
Nada aqui importa torch; as chamadas de Windows ficam protegidas (fora do Windows viram no-ops).
"""
from __future__ import annotations

import gc
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("djorge.leveza")
IS_WIN = sys.platform.startswith("win")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Djorge"
PADRAO = {
    "iniciar_com_windows": True,        # padrão ligado (minimizado na bandeja)
    "iniciar_minimizado": True,
    "descarregar_apos_min": 15,         # 0 = nunca descarregar o modelo ocioso
    "prioridade_baixa_ocioso": True,
    "ocioso_apos_s": 90,                # sem uso por este tempo -> prioridade baixa
    "otimizar_modelo": True,            # converter artefato de treino para ONNX int8 (uma vez)
    "inicio_adiar_integracoes_s": 90,   # início com o Windows: conectores/integrações/secretário depois disso
    "inicio_adiar_modelo_s": 300,       # início com o Windows: modelo só no 1º uso ou depois disso (0 = só no uso)
}
_INICIO = {"silencioso": False, "t": time.time()}
_ULTIMA = {"t": time.time()}
_PRIORIDADE = {"baixa": False}
_LOCK = threading.Lock()


# ------------------------------------------------------------------------------------- config --
def _arquivo() -> Path:
    from .model_download import data_root
    return data_root() / "leveza.json"


def ler_config() -> dict:
    c = dict(PADRAO)
    try:
        f = _arquivo()
        if f.is_file():
            c.update({k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items() if k in PADRAO})
    except Exception:
        pass
    return c


def salvar_config(patch: dict) -> dict:
    c = ler_config()
    for k, v in (patch or {}).items():
        if k not in PADRAO:
            continue
        if isinstance(PADRAO[k], bool):
            c[k] = bool(v)
        else:
            try:
                c[k] = max(0, int(float(v)))
            except (TypeError, ValueError):
                raise ValueError(f"{k}: use um número")
    f = _arquivo()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)
    if "iniciar_com_windows" in (patch or {}) or "iniciar_minimizado" in (patch or {}):
        iniciar_com_windows(c["iniciar_com_windows"], c["iniciar_minimizado"])
    return c


# --------------------------------------------------------------------------- início com o Windows --
def _comando(minimizado=True) -> str:
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        exe = sys.executable.replace("python.exe", "pythonw.exe") if IS_WIN else sys.executable
        cmd = f'"{exe}" -m djorge_local.launcher'
    return cmd + (" --minimizado" if minimizado else "")


def iniciar_com_windows(ativo: bool, minimizado: bool = True) -> bool:
    """Liga/desliga o início com o Windows (HKCU Run, mesmo valor que o instalador grava)."""
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if ativo:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, _comando(minimizado))
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        log.warning("início com o Windows: %s", e)
        return False


def no_inicio_do_windows() -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except Exception:
        return False


def aplicar_inicio(conf: dict = None):
    """Na primeira execução grava o início com o Windows conforme a config (padrão ligado). Depois só muda
    quando a pessoa muda a opção (salvar_config), para respeitar quem desligou pelo Windows ou pelo instalador."""
    conf = conf or ler_config()
    marca = _arquivo().with_name("leveza.inicio-aplicado")
    if marca.exists():
        return
    if conf.get("iniciar_com_windows") and not no_inicio_do_windows():
        iniciar_com_windows(True, conf.get("iniciar_minimizado", True))
    try:
        marca.parent.mkdir(parents=True, exist_ok=True)
        marca.write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")
    except Exception:
        pass


# --------------------------------------------------------------------------- processo e memória --
def memoria_mb() -> float:
    """Memória em uso pelo processo (working set no Windows, RSS fora)."""
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            c = PMC(); c.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            fn = getattr(k32, "K32GetProcessMemoryInfo", None) or ctypes.WinDLL("psapi").GetProcessMemoryInfo
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            fn.restype = wintypes.BOOL
            if fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return round(c.WorkingSetSize / 1e6, 1)
            return 0.0
        with open("/proc/self/statm") as f:
            return round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6, 1)
    except Exception:
        try:
            import resource
            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3, 1)
        except Exception:
            return 0.0


def _esvaziar_working_set() -> bool:
    if not IS_WIN:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetCurrentProcess()
        return bool(ctypes.windll.psapi.EmptyWorkingSet(h))
    except Exception:
        return False


def definir_prioridade(baixa: bool) -> bool:
    """BELOW_NORMAL quando ocioso, NORMAL ao usar (só Windows: fora dele não dá para voltar sem privilégios)."""
    with _LOCK:
        if _PRIORIDADE["baixa"] == bool(baixa):
            return True
        _PRIORIDADE["baixa"] = bool(baixa)
    if not IS_WIN:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        return bool(k.SetPriorityClass(k.GetCurrentProcess(), 0x4000 if baixa else 0x20))
    except Exception:
        return False


def atividade():
    _ULTIMA["t"] = time.time()
    ad = _INICIO.get("adiado")
    if ad is not None:                     # primeiro uso depois de um início silencioso: liga o resto
        _INICIO["adiado"] = None
        threading.Thread(target=ad.ja, daemon=True, name="inicio-primeiro-uso").start()
    if _PRIORIDADE["baixa"]:
        definir_prioridade(False)


def ocioso_ha_s() -> float:
    return time.time() - _ULTIMA["t"]


# ------------------------------------------------------------------------------- pausar / retomar --
def _svc(app, name):
    s = getattr(app, "_svc", None)
    if isinstance(s, dict) and name in s:
        return s[name]
    return getattr(app, name, None)


def _chamar(obj, nomes, *a):
    for n in nomes:
        f = getattr(obj, n, None)
        if callable(f):
            try:
                f(*a)
                return True
            except TypeError:
                continue
            except Exception as e:
                log.warning("%s.%s: %s", type(obj).__name__, n, e)
                return False
    return False


def pausado(app) -> bool:
    return bool(getattr(app, "_leveza_pausado", False))


def pausar(app) -> dict:
    """Desativa o Djorge sem fechar: para conectores, integrações e secretário e descarrega os modelos.
    O servidor local e a janela continuam (para retomar com um clique)."""
    if pausado(app):
        return recursos(app)
    for c in list((getattr(app, "conns", None) or {}).values()):
        try:
            c.stop()
        except Exception:
            pass
    app.conns = {}
    integ = _svc(app, "integrations")
    if integ is not None:
        try:
            if hasattr(app, "pause_integrations"):
                app.pause_integrations(True)
            else:
                _chamar(integ, ("stop_background",))
        except Exception as e:
            log.warning("pausar integrações: %s", e)
    sec = _svc(app, "secretario")
    if sec is not None:
        _chamar(sec, ("pausar", "pause", "stop", "parar"))
    sen = getattr(app, "sentinel", None)
    if sen is not None:
        _chamar(sen, ("unload", "stop"))
    models = getattr(app, "models", None)
    antes = []
    if models is not None:
        try:
            antes = list(models.loaded())
            for n in antes:
                if hasattr(models, "stop_api") and n in (getattr(models, "_servers", {}) or {}):
                    models.stop_api(n)
                models.unload(n)
                models.state[n] = "pausado"
            if hasattr(models, "wake_off"):
                models.wake_off()
        except Exception as e:
            log.warning("descarregar modelos: %s", e)
    app._leveza_modelos = antes
    app._leveza_pausado = True
    gc.collect()
    _esvaziar_working_set()
    definir_prioridade(True)
    log.info("Djorge pausado")
    return recursos(app)


def retomar(app) -> dict:
    if not pausado(app):
        return recursos(app)
    app._leveza_pausado = False
    definir_prioridade(False)
    atividade()
    try:
        from . import connectors as C
        app.conns = C.start_all(app.cfg)
    except Exception as e:
        log.warning("retomar conectores: %s", e)
    if _svc(app, "integrations") is not None:
        try:
            if hasattr(app, "pause_integrations"):
                app.pause_integrations(False)
            else:
                _chamar(_svc(app, "integrations"), ("start_background",))
        except Exception as e:
            log.warning("retomar integrações: %s", e)
    sec = _svc(app, "secretario")
    if sec is not None:
        _chamar(sec, ("retomar", "resume", "start", "iniciar"))
    models = getattr(app, "models", None)
    if models is not None:
        for n, st in list(getattr(models, "state", {}).items()):
            if st == "pausado":
                models.state.pop(n, None)
        if hasattr(models, "sleep"):
            models.sleep(getattr(app, "_leveza_modelos", []) or [])   # carrega no próximo uso (leve)
    log.info("Djorge retomado")
    return recursos(app)


def liberar_memoria(app, ociosos_min: float = None) -> dict:
    """Descarrega os modelos ociosos (padrão: sem uso há 1 min; 0 = todos), coleta o lixo e devolve ao Windows
    as páginas que o processo não está usando."""
    antes = memoria_mb()
    models = getattr(app, "models", None)
    fora = []
    if models is not None:
        mins = 1 if ociosos_min is None else ociosos_min
        try:
            fora = models.unload_all(keep_api=True) if mins == 0 else models.unload_idle(mins)
        except Exception as e:
            log.warning("liberar memória: %s", e)
    for mo in list((getattr(models, "motores", None) or {}).values()) if models is not None else []:
        try:
            mo.limpar()
        except Exception:
            pass
    gc.collect()
    _esvaziar_working_set()
    depois = memoria_mb()
    return {"antes_mb": antes, "depois_mb": depois, "liberado_mb": round(max(0.0, antes - depois), 1),
            "descarregados": fora}


def _motor_info(models):
    try:
        return models.motor_info("flash") or None
    except Exception:
        return None


def recursos(app) -> dict:
    """O que está rodando em segundo plano agora (para a interface e a bandeja)."""
    rod = ["Servidor local"]
    for n in sorted((getattr(app, "conns", None) or {})):
        rod.append(f"Conector: {n}")
    integ = _svc(app, "integrations")
    bg = getattr(integ, "_bg", None)
    if bg is not None and getattr(bg, "is_alive", lambda: False)() and not getattr(getattr(integ, "_stop", None), "is_set", lambda: False)():
        rod.append("Integrações (sincronização)")
    sec = _svc(app, "secretario")
    if sec is not None and not pausado(app):
        ativo = None
        for k in ("ativo", "active"):
            v = getattr(sec, k, None)
            if v is not None:
                ativo = v() if callable(v) else v
                break
        if ativo is None:
            try:
                ativo = bool((sec.config() if callable(getattr(sec, "config", None)) else {}).get("ativo"))
            except Exception:
                ativo = False
        if ativo:
            rod.append("Secretário")
    if getattr(app, "sentinel", None) is not None:
        rod.append("Sentinela")
    models = getattr(app, "models", None)
    carregados, motor, estado = [], None, None
    if models is not None:
        try:
            carregados = list(models.in_memory() if hasattr(models, "in_memory") else models.loaded())
        except Exception:
            carregados = []
        for n in carregados:
            rod.append(f"Modelo {n}")
        for n in sorted(getattr(models, "apis", {}) or {}):
            rod.append(f"API local ({n})")
        nome = "flash" if "flash" in (app.cfg.get("models") or {}) else (carregados[:1] or [None])[0]
        motor = (getattr(models, "engine", {}) or {}).get(nome)
        estado = (getattr(models, "state", {}) or {}).get(nome)
    try:
        from .otimizar import OTIMIZADOR
        if OTIMIZADOR.ocupado():
            rod.append("Otimizando o modelo")
    except Exception:
        pass
    return {"pausado": pausado(app), "rodando": rod, "memoria_mb": memoria_mb(),
            "modelo": {"carregado": bool(carregados), "motor": motor, "nome": carregados[0] if carregados else None,
                       "estado": estado},
            "prioridade": "baixa" if _PRIORIDADE["baixa"] else "normal", "ocioso_s": round(ocioso_ha_s()),
            "motor_desempenho": _motor_info(models), "inicio_silencioso": inicio_silencioso(),
            "config": ler_config()}


def encerrar(app=None, codigo: int = 0):
    """Sair: para conectores, integrações, APIs e modelos e encerra o processo de verdade."""
    try:
        if app is not None:
            for c in list((getattr(app, "conns", None) or {}).values()):
                try:
                    c.stop()
                except Exception:
                    pass
            integ = _svc(app, "integrations")
            if integ is not None:
                _chamar(integ, ("stop_background",))
            sec = _svc(app, "secretario")
            if sec is not None:
                _chamar(sec, ("parar", "stop"))
            models = getattr(app, "models", None)
            if models is not None:
                for n in list(getattr(models, "_servers", {}) or {}):
                    try:
                        models.stop_api(n)
                    except Exception:
                        pass
        logging.shutdown()
    finally:
        os._exit(codigo)


# ------------------------------------------------------------------------------ início silencioso --
def marcar_inicio(minimizado: bool):
    """Início com o Windows (--minimizado): sem janela, sem popups/toasts de boas-vindas, prioridade baixa.
    Outros módulos (toasts, notificações) consultam inicio_silencioso() / DJORGE_INICIO_SILENCIOSO."""
    _INICIO.update(silencioso=bool(minimizado), t=time.time())
    if minimizado:
        os.environ["DJORGE_INICIO_SILENCIOSO"] = "1"
        definir_prioridade(True)


def inicio_silencioso() -> bool:
    """True desde um início minimizado até a pessoa abrir a janela (ou usar o app)."""
    return bool(_INICIO["silencioso"])


def fim_do_silencio():
    _INICIO["silencioso"] = False
    os.environ.pop("DJORGE_INICIO_SILENCIOSO", None)


class InicioAdiado:
    """Início preguiçoso: `iniciar()` (conectores, integrações, secretário) roda depois de `adiar_s` ou assim
    que `ja()` for chamado (janela aberta / primeiro uso); `carregar()` (modelo) depois de `modelo_s` (0 =
    nunca sozinho: só no primeiro pedido, que Models.get já faz) ou em `ja()`. Cada um roda uma vez."""

    def __init__(self, iniciar, carregar=None, adiar_s: float = 90, modelo_s: float = 300):
        self._f = {"iniciar": iniciar, "carregar": carregar}
        self._feito = set()
        self._lock = threading.Lock()
        self._timers = []
        _INICIO["adiado"] = self
        for nome, s in (("iniciar", adiar_s), ("carregar", modelo_s)):
            if self._f[nome] is None:
                continue
            if s is not None and float(s) <= 0 and nome == "iniciar":
                self._rodar(nome)
            elif s and float(s) > 0:
                t = threading.Timer(float(s), self._rodar, args=(nome,))
                t.daemon = True
                t.start()
                self._timers.append(t)

    def _rodar(self, nome):
        with self._lock:
            if nome in self._feito or self._f.get(nome) is None:
                return False
            self._feito.add(nome)
        try:
            self._f[nome]()
        except Exception:
            log.exception("início adiado (%s)", nome)
        return True

    def ja(self, modelo: bool = False):
        if _INICIO.get("adiado") is self:
            _INICIO["adiado"] = None
        self._rodar("iniciar")
        if modelo:
            self._rodar("carregar")
        fim_do_silencio()
        definir_prioridade(False)

    def feitos(self):
        return sorted(self._feito)

    def cancelar(self):
        for t in self._timers:
            t.cancel()


# ------------------------------------------------------------------------------------------ vigia --
class Vigia:
    """A cada `passo` s: prioridade baixa quando ocioso; descarrega o modelo após N minutos sem uso."""

    def __init__(self, app, passo: float = 30.0):
        self.app, self.passo = app, passo
        self._stop = threading.Event()
        self._t = None

    def tick(self) -> dict:
        conf = ler_config()
        out = {"descarregados": [], "prioridade_baixa": False}
        if conf.get("prioridade_baixa_ocioso", True) and ocioso_ha_s() >= float(conf.get("ocioso_apos_s", 90)):
            definir_prioridade(True)
            out["prioridade_baixa"] = True
        mins = float(conf.get("descarregar_apos_min") or 0)
        models = getattr(self.app, "models", None)
        if mins > 0 and models is not None and hasattr(models, "unload_idle"):
            try:
                out["descarregados"] = models.unload_idle(mins)
            except Exception as e:
                log.warning("descarregar ocioso: %s", e)
            if out["descarregados"]:
                gc.collect()
                _esvaziar_working_set()
                log.info("modelo(s) ocioso(s) descarregado(s): %s", out["descarregados"])
        return out

    def _loop(self):
        while not self._stop.wait(self.passo):
            try:
                self.tick()
            except Exception:
                log.exception("vigia do modo leve")

    def start(self):
        if self._t is None or not self._t.is_alive():
            self._stop.clear()
            self._t = threading.Thread(target=self._loop, daemon=True, name="modo-leve")
            self._t.start()
        return self

    def stop(self):
        self._stop.set()
