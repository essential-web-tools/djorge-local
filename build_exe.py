"""Gera um executável com PyInstaller (opcional): python build_exe.py
Os modelos NÃO entram no executável: ficam nos .djx encriptados (baixados ou copiados)."""
import PyInstaller.__main__
PyInstaller.__main__.run(["djorge_local/__main__.py", "--name", "djorge-local", "--onedir", "--noconfirm",
                          "--add-data", "djorge_local/ui.html:djorge_local", "--add-data", "config.example.yaml:.",
                          "--add-data", "arvores.example.yaml:.", "--collect-all", "djorge",
                          "--hidden-import", "djorge_local.connectors"])
