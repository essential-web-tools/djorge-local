"""Djorge Local - command line.

  python -m djorge_local init                      cria ~/.djorge-local/config.yaml e arvores.yaml de exemplo
  python -m djorge_local run [--config C]          liga conectores, árvores e o painel (http://127.0.0.1:8765)
  python -m djorge_local seal PASTA SAIDA.djx      encripta um artefato (pede a chave)
  python -m djorge_local info ARQUIVO.djx          confere a chave e mostra o manifesto
  python -m djorge_local download                  baixa os .djx do Google Drive (continuam encriptados)
  python -m djorge_local test "texto" [--tree T]   passa um texto pelas árvores e mostra o caminho
  python -m djorge_local validate                  confere config e árvores sem carregar modelos
"""
from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
from pathlib import Path

from . import config as CF


def _key_twice():
    a = getpass.getpass("Nova chave (mín. 10 caracteres): ")
    if a != getpass.getpass("Repita a chave: "):
        sys.exit("as chaves não conferem")
    return a


def main(argv=None):
    ap = argparse.ArgumentParser(prog="djorge_local")
    ap.add_argument("cmd", choices=["init", "run", "seal", "info", "download", "test", "validate"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--config", default=None)
    ap.add_argument("--tree", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args(argv)
    here = Path(__file__).resolve().parent.parent

    if a.cmd == "init":
        CF.HOME.mkdir(parents=True, exist_ok=True)
        for src, dst in (("config.example.yaml", "config.yaml"), ("arvores.example.yaml", "arvores.yaml")):
            d = CF.HOME / dst
            if d.exists():
                print("já existe:", d)
            else:
                shutil.copy(here / src, d); print("criado:", d)
        cfg = CF.load(a.config)
        print("token do painel:", cfg["server"]["token"])
        return
    cfg = CF.load(a.config)

    if a.cmd == "seal":
        from .crypto import seal
        if len(a.args) != 2:
            sys.exit("uso: seal PASTA_DO_ARTEFATO SAIDA.djx")
        print(json.dumps(seal(a.args[0], a.args[1], _key_twice(), a.name), indent=1))
        return
    if a.cmd == "info":
        from .crypto import info
        from .models import passphrase
        print(json.dumps(info(a.args[0], passphrase(cfg)), indent=1))
        return
    if a.cmd == "download":
        from .models import download
        print(json.dumps(download(cfg), indent=1))
        return

    from .app import App
    app = App(cfg)
    if a.cmd == "validate":
        print(f"ok: {len(app.trees)} árvore(s):", ", ".join(t["name"] for t in app.trees))
        print("conectores ligados:", [k for k, v in cfg["connectors"].items() if v.get("enabled")])
        return
    if a.cmd == "test":
        ev = {"id": "teste", "source": "teste", "title": "", "app": "", "sender": "", "text": " ".join(a.args)}
        print(json.dumps(app.handle(ev, only=a.tree), ensure_ascii=False, indent=1, default=str))
        return
    if a.cmd == "run":
        from .app import serve
        for m in [k for k, v in cfg["models"].items() if v.get("enabled", True)]:
            try:
                app.models.get(m); print(f"modelo {m}: carregado")
            except Exception as e:
                print(f"modelo {m}: não carregado ({e})")
        app.start(a.workers)
        for k, c in app.conns.items():
            print(f"conector {k}: iniciado")
        serve(app)


if __name__ == "__main__":
    main()
