# PyInstaller spec - python -m PyInstaller djorge-local.spec --noconfirm
# onedir (abre rápido: nada é descompactado a cada início) + janela sem console + ícone + splash.
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

is_win, is_mac = sys.platform.startswith("win"), sys.platform == "darwin"
icon = "assets/icon.ico" if is_win else ("assets/icon.png" if is_mac else None)

hidden = collect_submodules("djorge") + collect_submodules("djorge_local") + [
    "pystray._win32" if is_win else ("pystray._darwin" if is_mac else "pystray._xorg"),
    "keyring.backends", "plyer.platforms"] + collect_submodules("webview") + collect_submodules("transformers.models.qwen3") + \
    collect_submodules("transformers.models.qwen2") + ["transformers.models.auto.modeling_auto"]
datas = [("djorge_local/ui.html", "djorge_local"), ("djorge_local/icon.png", "djorge_local"),
         ("djorge_local/static", "djorge_local/static"),
         ("config.example.yaml", "."), ("arvores.example.yaml", "."), ("assets/icon.png", "assets")]
datas += collect_data_files("djorge") + collect_data_files("plyer") + collect_data_files("transformers") + collect_data_files("webview")
excl = ["torchvision", "torchaudio", "matplotlib", "IPython", "jupyter", "notebook", "pandas",
        "tensorboard", "triton", "sympy.testing", "pytest", "sklearn", "tensorflow", "jax", "cv2", "gradio"]

a = Analysis(["run_djorge_local.py"], pathex=["."], hiddenimports=hidden, datas=datas, excludes=excl,
             noarchive=False,
             module_collection_mode={"transformers": "pyz+py", "torch": "pyz+py"})   # both inspect their sources
pyz = PYZ(a.pure)
extra = []
try:
    import tkinter  # noqa: F401  (the splash screen needs Tcl/Tk in the build machine)
    has_tk = True
except ImportError:
    has_tk = False
if not is_mac and has_tk:
    splash = Splash("assets/splash.png", binaries=a.binaries, datas=a.datas, text_pos=(24, 300), text_size=11,
                    text_color="#ecebe6", always_on_top=False)
    extra = [splash, splash.binaries]
exe = EXE(pyz, a.scripts, *extra, [], exclude_binaries=True, name="Djorge Local", icon=icon, console=False,
          version="version_info.txt" if is_win else None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Djorge Local", upx=False)
if is_mac:
    app = BUNDLE(coll, name="Djorge Local.app", icon=icon, bundle_identifier="local.djorge.app",
                 info_plist={"LSUIElement": True, "CFBundleShortVersionString": "0.7.4"})
