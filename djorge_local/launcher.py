"""Ponto de entrada do executável (Windows 10/11 x64): janela nativa (pywebview + WebView2) com a interface.

- Moldura nativa do Windows (título "Djorge"), 1280x820, mínimo 960x640, ícone do app.
- Bandeja (pystray): fechar a janela deixa o Djorge rodando (notificações continuam); menu Abrir ·
  Pausar/Retomar Djorge · Liberar memória · Sair (encerra de verdade).
- Modo leve (leveza.py): inicia com o Windows (padrão ligado) minimizado na bandeja (`--minimizado`),
  prioridade baixa quando ocioso, modelo descarregado após N minutos sem uso.
- Instância única (mutex nomeado): uma segunda abertura só mostra a janela que já existe.
- Splash do PyInstaller fechado quando o servidor responde; logs em %LOCALAPPDATA%/Djorge/logs.
- O modelo NÃO vem no instalador: a janela abre sem ele e o card do modelo oferece o download.
Imports de Windows ficam dentro das funções: `import djorge_local.launcher` funciona em qualquer sistema.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

os.environ.setdefault("DJORGE_GUI", "1")
IS_WIN = sys.platform.startswith("win")
TITLE = "Djorge"
SIZE, MIN_SIZE = (1280, 820), (960, 640)
MUTEX_NAME = "Local\\DjorgeLocalMutex"            # o installer.iss usa o mesmo nome (AppMutex)
SHOW_EVENT = "Local\\DjorgeLocalMostrar"
LOG = None


def data_dir() -> Path:
    from .model_download import data_root
    return data_root()


def _setup_log():
    """Sem console no executável: tudo vai para %LOCALAPPDATA%/Djorge/logs/djorge.log; erro fatal vira caixa
    de mensagem em vez de travar em silêncio."""
    global LOG
    import faulthandler
    import logging
    d = data_dir() / "logs"
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
    logging.info("iniciando Djorge · %s · Python %s", sys.platform, sys.version.split()[0])

    def hook(t, v, tb):
        import traceback
        logging.error("erro fatal:\n%s", "".join(traceback.format_exception(t, v, tb)))
        _fatal(f"{t.__name__}: {v}")
    sys.excepthook = hook
    threading.excepthook = lambda a: logging.error("erro em thread %s: %s", a.thread.name if a.thread else "?",
                                                   a.exc_value, exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def _message(text, flags=0x40):
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)
            return
        except Exception:
            pass
    print(text, file=sys.__stderr__ or sys.stderr)


def _fatal(msg):
    _close_splash()
    _message(f"O Djorge não conseguiu iniciar.\n\n{msg}\n\nDetalhes em:\n{LOG}", 0x10)
    os._exit(1)


# ------------------------------------------------------------------ instância única --
_MUTEX = None


def _single_instance() -> bool:
    """True se esta é a única instância (mutex nomeado). Fora do Windows: sempre True (desenvolvimento)."""
    global _MUTEX
    if not IS_WIN:
        return True
    import ctypes
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def _signal_show() -> bool:
    """Pede para a instância que já roda mostrar a janela (evento nomeado)."""
    if not IS_WIN:
        return False
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenEventW(0x0002, False, SHOW_EVENT)                    # EVENT_MODIFY_STATE
    if not h:
        return False
    k.SetEvent(h); k.CloseHandle(h)
    return True


def _listen_show(show):
    if not IS_WIN:
        return
    import ctypes
    k = ctypes.windll.kernel32
    h = k.CreateEventW(None, False, False, SHOW_EVENT)

    def loop():
        while True:
            if k.WaitForSingleObject(h, 0xFFFFFFFF) == 0:
                try:
                    show()
                except Exception:
                    pass
    threading.Thread(target=loop, daemon=True, name="mostrar-janela").start()


def _webview2_ok() -> bool:
    """O runtime Edge WebView2 desenha a janela (o Windows 11 já traz; alguns Windows 10 não)."""
    if not IS_WIN:
        return True
    import winreg
    guid = r"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    keys = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}")]
    for root, k in keys:
        try:
            with winreg.OpenKey(root, k) as h:
                v, _ = winreg.QueryValueEx(h, "pv")
                if v and v != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


# ------------------------------------------------------------------------ utilidades --
def _panel_url(cfg):
    s = cfg["server"]
    return f"http://{s['host']}:{s['port']}/?token={s['token']}"


def _already_running(cfg) -> bool:
    try:
        with socket.create_connection((cfg["server"]["host"], int(cfg["server"]["port"])), timeout=0.4):
            return True
    except OSError:
        return False


def _is_v2(cfg) -> bool:
    """A instância que ocupa a porta é a v2 (tem /api/model/info)? A v1 ("Djorge Local") não tem."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://{cfg['server']['host']}:{cfg['server']['port']}/api/model/info",
                                     headers={"X-Token": cfg["server"]["token"]})
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return r.status == 200
    except Exception as e:
        return getattr(e, "code", None) not in (404, None)


def _wait_server(cfg, timeout=15.0) -> bool:
    import time
    end = time.time() + timeout
    while time.time() < end:
        if _already_running(cfg):
            return True
        time.sleep(0.1)
    return False


def _close_splash():
    if "_PYI_SPLASH_IPC" not in os.environ:
        return
    try:
        import pyi_splash  # só dentro do executável do PyInstaller
        pyi_splash.close()
    except Exception:
        pass


def _res(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base.joinpath(*parts))


def _icon_path(ico=False):
    if ico:
        p = _res("assets", "icon.ico")
        if os.path.isfile(p):
            return p
    return str(Path(__file__).resolve().parent / "icon.png")


def _open_folder(p):
    if IS_WIN:
        os.startfile(p)  # noqa
    else:
        webbrowser.open(Path(p).as_uri())


def _set_app_id():
    """Agrupa a janela e a bandeja sob o ícone do Djorge na barra de tarefas."""
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Djorge.Local.2")
        except Exception:
            pass


class WindowApi:
    """Exposta à página como window.pywebview.api (seletores nativos de arquivo / pasta)."""

    def pick(self, kind="folder"):
        import webview
        w = webview.windows[0]
        FD = getattr(webview, "FileDialog", None)
        folder = FD.FOLDER if FD else webview.FOLDER_DIALOG
        openf = FD.OPEN if FD else webview.OPEN_DIALOG
        r = w.create_file_dialog(folder if kind == "folder" else openf)
        if not r:
            return None
        return r[0] if isinstance(r, (list, tuple)) else str(r)

    def open_data_folder(self):
        _open_folder(str(data_dir()))
        return True


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
    """Pouca memória (< 8 GB): só economias sem perda (tabela de tokens int8, vocabulário não usado
    removido) e descarregar o modelo ocioso."""
    import logging
    ram = total_ram_gb()
    if cfg.get("economy", "auto") == "off" or (cfg.get("economy", "auto") == "auto" and ram >= 8):
        return False
    sv = cfg.setdefault("sentinel", {})
    if not sv.get("unload_after_min"):
        sv["unload_after_min"] = 10
    os.environ.setdefault("DJORGE_EMBED_INT8", "1")
    os.environ.setdefault("DJORGE_PRUNE_VOCAB", "1")
    logging.info("modo econômico ligado (%.1f GB de RAM)", ram)
    return True


# --------------------------------------------------------------------------- janela --
def _tray_disponivel() -> bool:
    try:
        import pystray  # noqa: F401
        return True
    except Exception:
        return False


def _sair(app):
    """Sair da bandeja: para tudo e encerra o processo (não fica nada rodando)."""
    from . import leveza
    leveza.encerrar(app, 0)


def _window(url, cfg, app=None, minimizado=False):
    """Roda a janela nativa. Devolve False se não houver WebView2/pywebview (aí usamos o navegador)."""
    import logging
    if not _webview2_ok():
        logging.warning("WebView2 ausente: usando o navegador")
        _message("O componente de janela do Windows (Microsoft Edge WebView2) não foi encontrado.\n"
                 "O Djorge vai abrir no navegador. Reinstale o Djorge para instalar o WebView2.")
        return False
    try:
        import webview
    except Exception as e:
        logging.warning("pywebview indisponível (%s): usando o navegador", e)
        return False
    _set_app_id()
    oculto = bool(minimizado) and app is not None and _tray_disponivel()
    win = webview.create_window(TITLE, url, width=SIZE[0], height=SIZE[1], min_size=MIN_SIZE,
                                js_api=WindowApi(), background_color="#F6F3EE", text_select=True, hidden=oculto)

    def show():
        win.show(); win.restore()
    tray = _tray(app, cfg, show=show, quit_=lambda: _sair(app)) if app is not None else None
    if tray is None and oculto:
        oculto = False
        win.show()
    if oculto:
        _close_splash()
        logging.info("iniciado minimizado na bandeja")
    if tray is not None:
        _listen_show(show)

        def on_closing():
            win.hide()
            try:
                tray.notify("O Djorge continua rodando na bandeja.", TITLE)
            except Exception:
                pass
            return False                                   # não fecha: continua na bandeja
        win.events.closing += on_closing
    shown = threading.Event()
    win.events.shown += lambda: (shown.set(), _close_splash(), logging.info("janela aberta"))

    def watchdog():                                       # a janela não apareceu -> navegador
        if oculto:
            return
        if not shown.wait(30):
            logging.error("a janela não abriu em 30 s: abrindo no navegador")
            _close_splash()
            webbrowser.open(url)
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        ico = _icon_path(ico=True)                         # WinForms só aceita .ico (um .png derruba o processo)
        webview.start(gui="edgechromium" if IS_WIN else None, icon=ico if ico.lower().endswith(".ico") else None,
                      private_mode=False,
                      storage_path=str(data_dir() / "janela"))
    except Exception as e:
        logging.exception("janela nativa falhou: %s", e)
        return False
    os._exit(0)


_TRAY = None


def _tray(app, cfg, show, quit_):
    """Bandeja: Abrir · Pausar/Retomar Djorge · Liberar memória · Sair. Uma só por processo."""
    global _TRAY
    if _TRAY is not None:
        return _TRAY
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None
    from . import leveza

    def toggle(icon, item):
        try:
            if leveza.pausado(app):
                leveza.retomar(app)
                icon.notify("Djorge retomado.", TITLE)
            else:
                r = leveza.pausar(app)
                icon.notify(f"Djorge pausado. Usando {r['memoria_mb']:.0f} MB.", TITLE)
        except Exception as e:
            icon.notify(f"Erro: {e}", TITLE)

    def liberar(icon, item):
        try:
            r = leveza.liberar_memoria(app, 0)
            icon.notify(f"Memória liberada: {r['liberado_mb']:.0f} MB (agora {r['depois_mb']:.0f} MB).", TITLE)
        except Exception as e:
            icon.notify(f"Erro: {e}", TITLE)

    def sair(icon, item):
        try:
            icon.stop()
        finally:
            quit_()

    menu = pystray.Menu(
        pystray.MenuItem("Abrir", lambda i, it: show(), default=True),
        pystray.MenuItem(lambda it: "Retomar Djorge" if leveza.pausado(app) else "Pausar Djorge", toggle),
        pystray.MenuItem("Liberar memória", liberar),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", sair))
    icon = pystray.Icon("djorge", Image.open(_icon_path()), TITLE, menu)
    try:
        icon.run_detached()
    except Exception:
        threading.Thread(target=icon.run, daemon=True).start()
    _TRAY = icon
    return icon


def _modo_leve(app):
    """Vigia de ociosidade (prioridade baixa, descarregar modelo) e início com o Windows (1ª execução)."""
    import logging
    from . import leveza
    try:
        leveza.aplicar_inicio()
    except Exception as e:
        logging.warning("início com o Windows: %s", e)
    app.vigia = leveza.Vigia(app).start()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    from . import config as CF
    _setup_log()
    import logging
    try:
        if not (CF.HOME / "config.yaml").is_file():
            from .__main__ import main as cli
            cli(["init"])
        cfg = CF.load()
    except Exception as e:
        logging.exception("configuração")
        _fatal(f"configuração inválida: {e}")
    url = _panel_url(cfg)
    if _already_running(cfg) and not _is_v2(cfg):
        logging.warning("porta %s ocupada pela versão antiga: encerrando-a", cfg["server"]["port"])
        if IS_WIN:
            import subprocess
            for exe in ("Djorge Local.exe", "Djorge Local (diagnostico).exe"):
                subprocess.run(["taskkill", "/F", "/IM", exe, "/T"], capture_output=True,
                               creationflags=0x08000000)
            import time
            for _ in range(30):
                if not _already_running(cfg):
                    break
                time.sleep(0.2)
    if not _single_instance() or _already_running(cfg):
        _close_splash()
        if not _signal_show():
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
    app.start()
    _wait_server(cfg)
    try:
        _modo_leve(app)
    except Exception:
        logging.exception("modo leve")
    from . import leveza
    minimizado = "--minimizado" in argv or "--minimized" in argv
    # Sem modelo instalado não há nada a carregar: a interface mostra o card para baixar.
    try:
        from .model_download import ModelDownloader
        if ModelDownloader(cfg, app.models).info()["installed"]:
            threading.Timer(1.5, app.models.preload).start()
        else:
            logging.info("modelo ausente: aguardando download pela interface")
    except Exception:
        threading.Timer(1.5, app.models.preload).start()
    if _window(url, cfg, app, minimizado=minimizado):
        return
    _close_splash()
    print("Janela nativa indisponível: abrindo no navegador. Painel:", url)
    if not minimizado:
        webbrowser.open(url)
    _tray(app, cfg, show=lambda: webbrowser.open(url), quit_=lambda: leveza.encerrar(app, 0))
    threading.Event().wait()


if __name__ == "__main__":
    main()
