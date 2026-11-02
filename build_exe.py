"""Gera o pacote do Windows (atalho para o spec): python build_exe.py
Só Windows 10/11 x64. O modelo NÃO entra no pacote: o DOFL é baixado depois, pela interface."""
import sys

if not sys.platform.startswith("win"):
    sys.exit("o Djorge v2 só é empacotado no Windows (use build_windows.bat)")
import PyInstaller.__main__

PyInstaller.__main__.run(["djorge-local.spec", "--noconfirm"])
