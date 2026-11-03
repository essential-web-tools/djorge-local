"""Toasts nativos do Windows (funcionam com a janela fechada: o processo segue na bandeja).

Ordem: winotify (se instalado) -> pywin32 (balão da bandeja, Shell_NotifyIcon) -> nada (fallback silencioso).
Nada de Windows é importado no topo: os testes rodam no Linux. ULTIMOS guarda os últimos avisos (para a interface
e os testes); desligar = ENABLED = False.
"""
from __future__ import annotations

import collections
import os
import platform
import threading
import time

APP_ID = "Djorge Local"
ENABLED = True
ULTIMOS: "collections.deque" = collections.deque(maxlen=50)
_lock = threading.Lock()


def _icone() -> str:
    from pathlib import Path
    p = Path(__file__).with_name("icon.png")
    return str(p) if p.is_file() else ""


def _winotify(titulo, msg, url=None) -> bool:
    from winotify import Notification, audio            # type: ignore
    n = Notification(app_id=APP_ID, title=titulo, msg=msg, icon=_icone())
    n.set_audio(audio.Default, loop=False)
    if url:
        n.add_actions(label="Abrir", launch=url)
    n.show()
    return True


def _pywin32(titulo, msg) -> bool:
    import win32api, win32con, win32gui                 # type: ignore
    wc = win32gui.WNDCLASS()
    wc.hInstance = win32api.GetModuleHandle(None)
    wc.lpszClassName = "DjorgeToast"
    wc.lpfnWndProc = {win32con.WM_DESTROY: lambda *a: 0}
    try:
        win32gui.RegisterClass(wc)
    except Exception:
        pass                                             # classe já registrada
    hwnd = win32gui.CreateWindow("DjorgeToast", APP_ID, 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
    ico = _icone().replace(".png", ".ico")
    try:
        hicon = win32gui.LoadImage(wc.hInstance, ico, win32con.IMAGE_ICON, 0, 0,
                                   win32con.LR_LOADFROMFILE) if os.path.isfile(ico) else \
            win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
    except Exception:
        hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
    flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
    win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, (hwnd, 0, flags, win32con.WM_USER + 20, hicon, APP_ID))
    win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, (hwnd, 0, win32gui.NIF_INFO, win32con.WM_USER + 20, hicon,
                                                    APP_ID, msg[:255], 200, titulo[:63]))

    def _fim():
        time.sleep(8)
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
            win32gui.DestroyWindow(hwnd)
        except Exception:
            pass
    threading.Thread(target=_fim, daemon=True).start()
    return True


def mostrar(titulo: str, msg: str = "", url: str = None) -> bool:
    """Mostra um toast. Devolve True se algum mecanismo do Windows mostrou; False (sem erro) caso contrário."""
    titulo, msg = " ".join(str(titulo or "Djorge").split())[:120], str(msg or "")[:400]
    with _lock:
        ULTIMOS.append({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "titulo": titulo, "msg": msg, "url": url})
    if not ENABLED or platform.system() != "Windows":
        return False
    for fn in (lambda: _winotify(titulo, msg, url), lambda: _pywin32(titulo, msg)):
        try:
            if fn():
                return True
        except Exception:
            continue
    return False


def status() -> dict:
    eng = None
    if platform.system() == "Windows":
        for mod, nome in (("winotify", "winotify"), ("win32gui", "pywin32")):
            try:
                __import__(mod); eng = nome; break
            except Exception:
                continue
    return {"ativo": ENABLED, "motor": eng, "ultimos": list(ULTIMOS)[-10:][::-1]}
