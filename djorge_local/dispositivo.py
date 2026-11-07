"""Status do dispositivo, leve, para o modelo/comando/secretário levarem em conta.

    status(forcar=False) -> dict      (cache de alguns segundos; cada leitura é protegida e barata)
    resumo() -> {"texto": "...", "avisos": [...], "evitar": {...}, "status": {...}}
    pode_baixar_modelo() -> (bool, motivo)

Campos de status(): bateria {percentual, carregando, na_tomada}, energia {economia}, rede {online, tipo},
cpu {nucleos, uso_pct}, memoria {total_mb, livre_mb}, disco {livre_gb}, horario {local, fuso, utc_offset_min},
foco {nao_perturbe} (Windows: estado de "notificações silenciadas"), tela {bloqueada}.
Windows via ctypes (sem dependências); psutil é usado quando existir; fora do Windows o que der (Linux /sys).
Valores desconhecidos ficam None — quem usa nunca deve tratar None como "ruim".
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time

IS_WIN = sys.platform.startswith("win")
TTL_S = 10.0
_CACHE = {"t": 0.0, "v": None}
_LOCK = threading.Lock()


def _psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


# ------------------------------------------------------------------------------------------ partes --
def bateria():
    out = {"percentual": None, "carregando": None, "na_tomada": None, "tem_bateria": None}
    if IS_WIN:
        try:
            import ctypes

            class SPS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
            s = SPS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
                sem = bool(s.BatteryFlag & 128) or s.BatteryFlag == 255
                out["tem_bateria"] = not sem
                out["na_tomada"] = {0: False, 1: True}.get(s.ACLineStatus)
                if not sem and s.BatteryLifePercent <= 100:
                    out["percentual"] = int(s.BatteryLifePercent)
                out["carregando"] = bool(s.BatteryFlag & 8) if not sem else None
                out["_economia"] = bool(s.SystemStatusFlag & 1)          # economia de bateria ligada
            return out
        except Exception:
            pass
    ps = _psutil()
    if ps is not None:
        try:
            b = ps.sensors_battery()
            if b is None:
                out["tem_bateria"] = False
            else:
                out.update(tem_bateria=True, percentual=int(b.percent), na_tomada=bool(b.power_plugged),
                           carregando=bool(b.power_plugged) and b.percent < 100)
            return out
        except Exception:
            pass
    try:                                                   # Linux sem psutil
        base = "/sys/class/power_supply"
        for d in os.listdir(base):
            p = os.path.join(base, d)
            if open(os.path.join(p, "type")).read().strip() == "Battery":
                out["tem_bateria"] = True
                out["percentual"] = int(open(os.path.join(p, "capacity")).read().strip())
                st = open(os.path.join(p, "status")).read().strip()
                out["carregando"] = st == "Charging"
                out["na_tomada"] = st in ("Charging", "Full", "Not charging")
        if out["tem_bateria"] is None:
            out["tem_bateria"] = False
    except Exception:
        pass
    return out


def rede():
    out = {"online": None, "tipo": None}
    if IS_WIN:
        try:
            import ctypes
            fl = ctypes.c_ulong()
            out["online"] = bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(fl), 0))
        except Exception:
            pass
    if out["online"] is None:
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=0.6):
                out["online"] = True
        except OSError:
            px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            out["online"] = bool(px)      # atrás de proxy não dá para testar direto: não acusa "sem internet"
    ps = _psutil()
    if ps is not None:
        try:
            ups = [n for n, s in ps.net_if_stats().items() if s.isup and not n.lower().startswith(("lo", "loopback"))]
            low = " ".join(ups).lower()
            out["tipo"] = "wifi" if any(k in low for k in ("wi-fi", "wifi", "wlan", "wireless")) else (
                "cabo" if ups else None)
        except Exception:
            pass
    elif not IS_WIN:
        try:
            ups = [d for d in os.listdir("/sys/class/net") if d != "lo" and
                   open(f"/sys/class/net/{d}/operstate").read().strip() == "up"]
            out["tipo"] = "wifi" if any(os.path.isdir(f"/sys/class/net/{d}/wireless") for d in ups) else (
                "cabo" if ups else None)
        except Exception:
            pass
    return out


def memoria():
    out = {"total_mb": None, "livre_mb": None}
    if IS_WIN:
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong),
                            ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
            m = MS(); m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return {"total_mb": round(m.ullTotalPhys / 2**20), "livre_mb": round(m.ullAvailPhys / 2**20)}
        except Exception:
            pass
    try:
        info = dict(l.split(":", 1) for l in open("/proc/meminfo"))
        kb = lambda k: int(info[k].split()[0])
        return {"total_mb": round(kb("MemTotal") / 1024), "livre_mb": round(kb("MemAvailable") / 1024)}
    except Exception:
        return out


def cpu():
    out = {"nucleos": os.cpu_count(), "uso_pct": None}
    ps = _psutil()
    if ps is not None:
        try:
            out["uso_pct"] = round(ps.cpu_percent(interval=None), 1)
        except Exception:
            pass
    elif hasattr(os, "getloadavg"):
        try:
            out["uso_pct"] = round(min(100.0, os.getloadavg()[0] / max(1, os.cpu_count() or 1) * 100), 1)
        except Exception:
            pass
    return out


def disco(pasta=None):
    try:
        if pasta is None:
            from .model_download import data_root
            pasta = data_root()
        p = str(pasta)
        while p and not os.path.exists(p):
            p = os.path.dirname(p)
        return {"livre_gb": round(shutil.disk_usage(p or os.path.expanduser("~")).free / 1e9, 1)}
    except Exception:
        return {"livre_gb": None}


def horario():
    lt = time.localtime()
    off = -(time.altzone if lt.tm_isdst > 0 and time.daylight else time.timezone) // 60
    return {"local": time.strftime("%Y-%m-%d %H:%M", lt), "fuso": time.strftime("%Z", lt) or None,
            "utc_offset_min": int(off), "hora": lt.tm_hour, "dia_semana": lt.tm_wday}


def foco():
    """Windows: SHQueryUserNotificationState — 'não perturbe'/apresentação/tela cheia silenciam notificações."""
    out = {"nao_perturbe": None, "estado": None}
    if IS_WIN:
        try:
            import ctypes
            st = ctypes.c_int()
            if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(st)) == 0:
                nomes = {1: "sem_sessao", 2: "ocupado_tela_cheia", 3: "direct3d_tela_cheia", 4: "apresentacao",
                         5: "livre", 6: "app_tela_cheia", 7: "nao_perturbe"}
                out["estado"] = nomes.get(st.value, str(st.value))
                out["nao_perturbe"] = st.value in (2, 3, 4, 6, 7)
        except Exception:
            pass
    return out


def tela():
    """Windows: a tela está bloqueada quando não dá para abrir a área de trabalho de entrada."""
    out = {"bloqueada": None}
    if IS_WIN:
        try:
            import ctypes
            u = ctypes.windll.user32
            h = u.OpenInputDesktop(0, False, 0x0100)            # DESKTOP_SWITCHDESKTOP
            if h:
                ok = u.SwitchDesktop(h)
                u.CloseDesktop(h)
                out["bloqueada"] = not bool(ok)
            else:
                out["bloqueada"] = True
        except Exception:
            pass
    return out


# -------------------------------------------------------------------------------------------- todo --
def status(forcar: bool = False) -> dict:
    with _LOCK:
        if not forcar and _CACHE["v"] is not None and time.monotonic() - _CACHE["t"] < TTL_S:
            return dict(_CACHE["v"])
    b = bateria()
    eco = b.pop("_economia", None)
    v = {"bateria": b, "energia": {"economia": eco}, "rede": rede(), "cpu": cpu(), "memoria": memoria(),
         "disco": disco(), "horario": horario(), "foco": foco(), "tela": tela(), "lido_em": time.time()}
    with _LOCK:
        _CACHE.update(t=time.monotonic(), v=v)
    return dict(v)


def resumo(st: dict = None) -> dict:
    """Texto curto (para o contexto do comando/secretário), avisos e o que evitar agora."""
    st = st or status()
    b, r, m, d, f = st["bateria"], st["rede"], st["memoria"], st["disco"], st["foco"]
    partes, avisos = [], []
    evitar = {"baixar_modelo": False, "tarefas_pesadas": False, "notificar": False}
    if b.get("tem_bateria") and b.get("percentual") is not None:
        partes.append(f"bateria {b['percentual']}%" + (" carregando" if b.get("carregando") else ""))
        if b["percentual"] <= 20 and not b.get("na_tomada"):
            avisos.append(f"Bateria baixa ({b['percentual']}%): conecte o carregador.")
            evitar["baixar_modelo"] = evitar["tarefas_pesadas"] = True
    if (st.get("energia") or {}).get("economia"):
        partes.append("economia de energia ligada")
        evitar["tarefas_pesadas"] = True
    if r.get("online") is False:
        partes.append("sem internet")
        avisos.append("Sem internet: downloads e integrações esperam a conexão voltar.")
        evitar["baixar_modelo"] = True
    elif r.get("online"):
        partes.append("online" + (f" ({r['tipo']})" if r.get("tipo") else ""))
    if m.get("livre_mb") is not None:
        partes.append(f"{m['livre_mb'] / 1024:.1f} GB de memória livre")
        if m["livre_mb"] < 800:
            avisos.append("Pouca memória livre: o modelo pode demorar ou ficar descarregado.")
            evitar["tarefas_pesadas"] = True
    if d.get("livre_gb") is not None and d["livre_gb"] < 2:
        avisos.append(f"Pouco espaço em disco ({d['livre_gb']} GB livres).")
        evitar["baixar_modelo"] = True
    if f.get("nao_perturbe"):
        partes.append("não perturbe")
        evitar["notificar"] = True
    if (st.get("tela") or {}).get("bloqueada"):
        partes.append("tela bloqueada")
        evitar["notificar"] = True
    h = st["horario"]
    partes.append(f"{h['local']}" + (f" {h['fuso']}" if h.get("fuso") else ""))
    return {"texto": "Dispositivo: " + ", ".join(partes) + ".", "avisos": avisos, "evitar": evitar, "status": st}


def pode_baixar_modelo():
    r = resumo()
    if r["evitar"]["baixar_modelo"]:
        return False, (r["avisos"] or ["agora não é um bom momento"])[0]
    return True, ""
