# PyInstaller (somente Windows 10/11 x64):  python -m PyInstaller djorge-local.spec --noconfirm
# onedir (abre rápido), janela nativa pywebview/WebView2, sem console, splash, ícone.
# O MODELO NÃO ENTRA no pacote: o DOFL é baixado depois pela interface (modelo-fonte.txt / model_source).
# Também gera "Djorge (diagnostico).exe": o mesmo programa com console, para ver erros de inicialização.
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

if not sys.platform.startswith("win"):
    raise SystemExit("o pacote do Djorge v2 é só para Windows")

hidden = (collect_submodules("djorge") + collect_submodules("djorge_local") + collect_submodules("webview")
          + ["pystray._win32", "keyring.backends", "keyring.backends.Windows", "win32com.client", "comtypes.client",
             "onnxruntime", "gdown", "huggingface_hub"]
          + collect_submodules("transformers.models.qwen3") + collect_submodules("transformers.models.qwen2")
          + ["transformers.models.auto.modeling_auto"])
datas = [("djorge_local/ui.html", "djorge_local"), ("djorge_local/icon.png", "djorge_local"),
         ("djorge_local/static", "djorge_local/static"), ("config.example.yaml", "."),
         ("arvores.example.yaml", "."), ("modelo-fonte.txt", "."),
         ("assets/icon.png", "assets"), ("assets/icon.ico", "assets")]
datas += collect_data_files("djorge") + collect_data_files("transformers") + collect_data_files("webview")
excl = ["torchvision", "torchaudio", "matplotlib", "IPython", "jupyter", "notebook", "pandas", "tensorboard",
        "triton", "sympy.testing", "pytest", "sklearn", "tensorflow", "jax", "cv2", "gradio", "tkinter",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "gi"]

a = Analysis(["run_djorge_local.py"], pathex=["."], hiddenimports=hidden, datas=datas, excludes=excl,
             binaries=collect_dynamic_libs("webview") + collect_dynamic_libs("onnxruntime"), noarchive=False,
             module_collection_mode={"transformers": "pyz+py", "torch": "pyz+py"})   # ambos leem o próprio código
pyz = PYZ(a.pure)
splash = Splash("assets/splash.png", binaries=a.binaries, datas=a.datas, text_pos=None)
exe = EXE(pyz, a.scripts, splash, [], exclude_binaries=True, name="Djorge", icon="assets/icon.ico", console=False,
          version="version_info.txt", upx=False)
dbg = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Djorge (diagnostico)", icon="assets/icon.ico",
          console=True, version="version_info.txt", upx=False)
coll = COLLECT(exe, dbg, splash.binaries, a.binaries, a.datas, name="Djorge", upx=False)
