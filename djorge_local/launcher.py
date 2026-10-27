"""Executable entry point: a NATIVE window (pywebview: WebView2 on Windows, WebKit on macOS, Qt/GTK on Linux)
with the Djorge Local interface. No browser is opened. The window shows up in ~1 s; models load in the background.

Tray (Windows / Linux): closing the window keeps Djorge running in the tray; menu: Abrir · Pausar conectores ·
Recarregar árvores · Abrir pasta de dados · Sair. On macOS closing the window quits.
Single instance: a second launch opens a window attached to the running one.
If no native web view is available, it falls back to the default browser (and says so).
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser

os.environ.setdefault("DJORGE_GUI", "1")
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
LOG = None


def _setup_log():
    """Windowed builds have no console: everything goes to ~/.djorge-local/logs/djorge.log, and a fatal error
    shows a message box instead of a silent hang."""
    global LOG
    import faulthandler
    import logging
    from . import config as CF
    d = CF.HOME / "logs"
    d.mkdir(parents=True, exist_ok=True)
    LOG = d / "djorge.log"
    try:
        if LOG.exists() and LOG.stat().st_size > 5_000_000:
            LOG.replace(d / "djorge.old.log")
    except OSError:
        pass
    f = open(LOG, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None or not getattr(sys.stdout, "isatty", lambda: False)():
        sys.stdout = sys.stderr = f
    faulthandler.enable(f)
    logging.basicConfig(stream=f, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.info("iniciando Djorge Local · %s · Python %s", sys.platform, sys.version.split()[0])

    def hook(t, v, tb):
        import traceback
        logging.error("erro fatal:\n%s", "".join(traceback.format_exception(t, v, tb)))
        _fatal(f"{t.__name__}: {v}")
    sys.excepthook = hook
    threading.excepthook = lambda a: logging.error("erro em thread %s: %s", a.thread.name if a.thread else "?",
                                                   a.exc_value, exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def _fatal(msg):
    _close_splash()
    text = f"O Djorge Local não conseguiu iniciar.\n\n{msg}\n\nDetalhes em:\n{LOG}"
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Djorge Local", 0x10)
        elif IS_MAC:
            os.system(f"osascript -e 'display alert \"Djorge Local\" message \"{msg[:200]}\"' >/dev/null 2>&1")
    except Exception:
        pass
    os._exit(1)


_MUTEX = None


def _single_instance() -> bool:
    """True if this is the only running instance (Windows: named mutex; elsewhere: the port check)."""
    global _MUTEX
    if not IS_WIN:
        return True
    import ctypes
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\DjorgeLocalMutex")
    return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def _webview2_ok() -> bool:
    """Windows: the Edge WebView2 runtime is what draws the window (Windows 11 has it; some Windows 10 not)."""
    if not IS_WIN:
        return True
    import winreg
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")]
    for root, k in keys:
        try:
            with winreg.OpenKey(root, k) as h:
                v, _ = winreg.QueryValueEx(h, "pv")
                if v and v != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def _panel_url(cfg):
    s = cfg["server"]
    return f"http://{s['host']}:{s['port']}/?token={s['token']}"


def _already_running(cfg) -> bool:
    try:
        with socket.create_connection((cfg["server"]["host"], int(cfg["server"]["port"])), timeout=0.4):
            return True
    except OSError:
        return False


def _close_splash():
    if "_PYI_SPLASH_IPC" not in os.environ:
        return
    try:
        import pyi_splash  # only inside the PyInstaller build
        pyi_splash.close()
    except Exception:
        pass


def _icon_path():
    return os.path.join(os.path.dirname(__file__), "icon.png")


def _open_folder(p):
    if sys.platform.startswith("win"):
        os.startfile(p)  # noqa
    elif IS_MAC:
        os.system(f'open "{p}"')
    else:
        os.system(f'xdg-open "{p}" >/dev/null 2>&1 &')


class WindowApi:
    """Exposed to the page as window.pywebview.api (native file / folder pickers)."""

    def pick(self, kind="folder"):
        import webview
        w = webview.windows[0]
        FD = getattr(webview, "FileDialog", None)
        folder = FD.FOLDER if FD else webview.FOLDER_DIALOG
        openf = FD.OPEN if FD else webview.OPEN_DIALOG
        if kind == "folder":
            r = w.create_file_dialog(folder)
        else:
            r = w.create_file_dialog(openf)
        if not r:
            return None
        return r[0] if isinstance(r, (list, tuple)) else str(r)


def total_ram_gb() -> float:
    try:
        if IS_WIN:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS(); m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1e9
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except Exception:
        return 0.0


def economy_mode(cfg):
    """Computers with little memory (< 8 GB): lossless savings only (int8 token table, unused vocabulary
    removed), one model at a time. Measured on the Sentinel/Origo: same answers, ~0.5 GB less."""
    import logging
    ram = total_ram_gb()
    if cfg.get("economy", "auto") == "off" or (cfg.get("economy", "auto") == "auto" and ram >= 8):
        return False
    sv = cfg.setdefault("sentinel", {})
    if not sv.get("unload_after_min"):
        sv["unload_after_min"] = 10                    # Sentinel leaves memory after 10 idle minutes
    os.environ.setdefault("DJORGE_EMBED_INT8", "1")
    os.environ.setdefault("DJORGE_PRUNE_VOCAB", "1")
    logging.info("modo econômico ligado (%.1f GB de RAM)", ram)
    return True


def _edge():
    cands = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    return next((c for c in cands if os.path.isfile(c)), None)


def _app_window_windows(url, cfg, app):
    """Windows: the interface opens as an APP window of Edge (no tabs, no address bar, own profile in
    ~/.djorge-local). It avoids the .NET / WebView2 embedding that crashed on some Windows 10 machines."""
    import logging
    import subprocess
    from . import config as CF
    exe = _edge()
    if not exe:
        return False
    prof = str(CF.HOME / "janela")

    def open_win():
        flags = 0x08000000 if IS_WIN else 0                       # CREATE_NO_WINDOW
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={prof}", "--window-size=1260,880",
                          "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                          "--disable-features=Translate", "--lang=pt-BR"], creationflags=flags)
        logging.info("janela de app aberta com %s", os.path.basename(exe))
    open_win()
    if app is None:
        return True
    tray = _tray(app, cfg, show=open_win, quit_=lambda: os._exit(0))
    if tray is None:                                   # no tray: keep serving until the user ends the process
        threading.Event().wait()
    threading.Event().wait()
    return True


def _window(url, cfg, app=None):
    """Returns True if the native window ran; False if no web view backend is available."""
    import logging
    if IS_WIN:
        return _app_window_windows(url, cfg, app)
    if not _webview2_ok():
        logging.warning("WebView2 ausente: usando o navegador")
        return False
    try:
        import webview
    except Exception as e:
        logging.warning("pywebview indisponível (%s): usando o navegador", e)
        return False
    from . import config as CF
    win = webview.create_window("Djorge Local", url, width=1260, height=880, min_size=(420, 600),
                                js_api=WindowApi(), background_color="#F6F3EE", text_select=True)
    tray = None
    if app is not None and not IS_MAC:
        tray = _tray(app, cfg, show=lambda: (win.show(), win.restore()), quit_=lambda: os._exit(0))

    if tray is not None:
        def on_closing():
            win.hide()
            try:
                tray.notify("Continua rodando na bandeja.", "Djorge Local")
            except Exception:
                pass
            return False                                   # keep running in the tray
        win.events.closing += on_closing
    shown = threading.Event()
    win.events.shown += lambda: (shown.set(), logging.info("janela aberta"))

    def watchdog():                                   # window never appeared -> open the browser instead
        if not shown.wait(30):
            logging.error("a janela não abriu em 30 s: abrindo no navegador")
            webbrowser.open(url)
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        kw = {"gui": "edgechromium"} if IS_WIN else {}
        webview.start(icon=_icon_path(), private_mode=False, storage_path=str(CF.HOME / "janela"), **kw)
    except Exception as e:
        logging.exception("janela nativa falhou: %s", e)
        return False
    os._exit(0)


def _tray(app, cfg, show, quit_):
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None
    from . import config as CF
    paused = {"v": False}

    def toggle(icon, item):
        from . import connectors as C
        paused["v"] = not paused["v"]
        if paused["v"]:
            for c in app.conns.values():
                c.stop()
            app.conns = {}
        else:
            app.conns = C.start_all(cfg)

    def reload(icon, item):
        try:
            n = app.reload_trees(); icon.notify(f"{n} árvore(s) carregada(s)", "Djorge Local")
        except Exception as e:
            icon.notify(f"Erro nas árvores: {e}", "Djorge Local")

    menu = pystray.Menu(
        pystray.MenuItem("Abrir Djorge Local", lambda i, it: show(), default=True),
        pystray.MenuItem(lambda it: "Retomar conectores" if paused["v"] else "Pausar conectores", toggle),
        pystray.MenuItem("Recarregar árvores", reload),
        pystray.MenuItem("Abrir pasta de dados", lambda i, it: _open_folder(str(CF.HOME))),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", lambda i, it: (i.stop(), quit_())))
    icon = pystray.Icon("djorge-local", Image.open(_icon_path()), "Djorge Local", menu)
    try:
        icon.run_detached()
    except Exception:
        threading.Thread(target=icon.run, daemon=True).start()
    return icon


def main():
    from . import config as CF
    _setup_log()
    try:
        if not (CF.HOME / "config.yaml").is_file():
            from .__main__ import main as cli
            cli(["init"])
        cfg = CF.load()
    except Exception as e:
        import logging
        logging.exception("configuração")
        _fatal(f"configuração inválida: {e}")
    url = _panel_url(cfg)
    if not _single_instance() or _already_running(cfg):
        _close_splash()
        if not _window(url, cfg):
            webbrowser.open(url)
        return
    economy_mode(cfg)
    from .app import App, serve
    app = App(cfg)
    try:
        serve(app, block=False)
    except OSError as e:
        _fatal(f"a porta {cfg['server']['port']} está ocupada por outro programa ({e}). "
               "Mude a porta em config.yaml ou feche o outro programa.")
    threading.Timer(1.5, app.models.preload).start()   # torch + models after the window is up
    app.start()
    _close_splash()
    if _window(url, cfg, app):
        return
    print("Janela nativa indisponível: abrindo no navegador. Painel:", url)
    webbrowser.open(url)
    threading.Event().wait()


if __name__ == "__main__":
    main()
