"""Executable entry point: tray icon in ~1 s, panel opens in the browser, models load in the background.

Tray menu: Abrir painel · Pausar/retomar conectores · Recarregar árvores · Abrir pasta de dados · Sair.
Single instance: a second launch only opens the panel of the running one.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser

os.environ.setdefault("DJORGE_GUI", "1")


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


def main():
    from . import config as CF
    first = not (CF.HOME / "config.yaml").is_file()
    if first:
        from .__main__ import main as cli
        cli(["init"])
    cfg = CF.load()
    if _already_running(cfg):
        webbrowser.open(_panel_url(cfg)); _close_splash(); return
    from .app import App, serve
    app = App(cfg)
    serve(app, block=False)
    if any(not m.get("path") for m in cfg["models"].values() if m.get("enabled", True)):
        from .models import passphrase       # asked here, on the main thread (dialogs need it)
        try:
            app.models._key = passphrase(cfg)
        except Exception as e:
            print("modelos encriptados sem chave:", e)
    app.models.preload()                      # heavy imports (torch) happen here, off the UI thread
    app.start()
    _close_splash()
    url = _panel_url(cfg)
    if cfg.get("open_panel_on_start", True):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        import pystray
        from PIL import Image
    except Exception:                         # no tray (no display / backend): keep running headless
        print("Djorge Local rodando. Painel:", url)
        threading.Event().wait()
        return
    ico = os.path.join(os.path.dirname(__file__), "icon.png")
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

    def folder(icon, item):
        p = str(CF.HOME)
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa
        elif sys.platform == "darwin":
            os.system(f'open "{p}"')
        else:
            os.system(f'xdg-open "{p}" >/dev/null 2>&1 &')

    def quit_(icon, item):
        icon.stop(); os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("Abrir painel", lambda i, it: webbrowser.open(url), default=True),
        pystray.MenuItem(lambda it: "Retomar conectores" if paused["v"] else "Pausar conectores", toggle),
        pystray.MenuItem("Recarregar árvores", reload),
        pystray.MenuItem("Abrir pasta de dados", folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", quit_))
    pystray.Icon("djorge-local", Image.open(ico), "Djorge Local", menu).run()


if __name__ == "__main__":
    main()
