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
            r = w.create_file_dialog(openf, file_types=("Modelo DJORGE (*.djx)", "Todos os arquivos (*.*)"))
        if not r:
            return None
        return r[0] if isinstance(r, (list, tuple)) else str(r)


def _window(url, cfg, app=None):
    """Returns True if the native window ran; False if no web view backend is available."""
    try:
        import webview
    except Exception:
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
    try:
        webview.start(icon=_icon_path(), private_mode=False, storage_path=str(CF.HOME / "janela"))
    except Exception:
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
    if not (CF.HOME / "config.yaml").is_file():
        from .__main__ import main as cli
        cli(["init"])
    cfg = CF.load()
    url = _panel_url(cfg)
    if _already_running(cfg):
        _close_splash()
        if not _window(url, cfg):
            webbrowser.open(url)
        return
    from .app import App, serve
    app = App(cfg)
    serve(app, block=False)
    app.models.preload()                      # torch + models load off the UI thread; the key comes from the UI
    app.start()
    _close_splash()
    if _window(url, cfg, app):
        return
    print("Janela nativa indisponível: abrindo no navegador. Painel:", url)
    webbrowser.open(url)
    threading.Event().wait()


if __name__ == "__main__":
    main()
