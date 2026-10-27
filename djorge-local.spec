# PyInstaller spec - python -m PyInstaller djorge-local.spec --noconfirm
# onedir (abre rápido: nada é descompactado a cada início) + janela nativa sem console + ícone.
# Também gera "Djorge Local (diagnóstico)": o mesmo programa com console, para ver erros de inicialização.
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

is_win, is_mac = sys.platform.startswith("win"), sys.platform == "darwin"
icon = "assets/icon.ico" if is_win else ("assets/icon.png" if is_mac else None)

hidden = collect_submodules("djorge") + collect_submodules("djorge_local") + [
    "pystray._win32" if is_win else ("pystray._darwin" if is_mac else "pystray._xorg"),
    "keyring.backends", "plyer.platforms"] + ([] if is_win else collect_submodules("webview")) + collect_submodules("transformers.models.qwen3") + \
    collect_submodules("transformers.models.qwen2") + ["transformers.models.auto.modeling_auto"]
datas = [("djorge_local/ui.html", "djorge_local"), ("djorge_local/icon.png", "djorge_local"),
         ("djorge_local/static", "djorge_local/static"),
         ("config.example.yaml", "."), ("arvores.example.yaml", "."), ("assets/icon.png", "assets")]
datas += collect_data_files("djorge") + collect_data_files("plyer") + collect_data_files("transformers") + ([] if is_win else collect_data_files("webview"))
excl = (["webview", "clr", "pythonnet", "clr_loader"] if is_win else []) + ["torchvision", "torchaudio", "matplotlib", "IPython", "jupyter", "notebook", "pandas",
        "tensorboard", "triton", "sympy.testing", "pytest", "sklearn", "tensorflow", "jax", "cv2", "gradio"]

bins = [] if is_win else collect_dynamic_libs("webview")   # Windows uses an Edge app window (no .NET)
a = Analysis(["run_djorge_local.py"], pathex=["."], hiddenimports=hidden, datas=datas, excludes=excl, binaries=bins,
             noarchive=False,
             module_collection_mode={"transformers": "pyz+py", "torch": "pyz+py"})   # both inspect their sources
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Djorge Local", icon=icon, console=False,
          version="version_info.txt" if is_win else None, upx=False)
dbg = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Djorge Local (diagnostico)", icon=icon, console=True,
          version="version_info.txt" if is_win else None, upx=False)
coll = COLLECT(exe, dbg, a.binaries, a.datas, name="Djorge Local", upx=False)
if is_mac:
    app = BUNDLE(coll, name="Djorge Local.app", icon=icon, bundle_identifier="local.djorge.app",
                 info_plist={"LSUIElement": False, "CFBundleShortVersionString": "0.7.7"})
