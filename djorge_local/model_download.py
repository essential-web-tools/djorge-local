"""Modelo baixado depois: o instalador não traz o DOFL; a interface pede o download (card do modelo).

`ModelDownloader(cfg, models)` (contrato em docs/V2_SPEC.md):
  info()   -> estado, progresso (bytes/total), caminho, resumo de metadata/djorge.json
  start()  -> inicia o download em segundo plano (thread) e devolve info()
  cancel() -> interrompe (o arquivo parcial fica para retomar)
  remove() -> apaga o modelo baixado e tira o caminho da configuração
  otimizar() -> converte agora um artefato de treino (sem onnx/) para ONNX int8 (otimizar.py)

Estados: ausente · baixando · verificando · instalado · otimizando ("Otimizando o modelo para este computador…",
com progresso e etapa) · erro. `formato` diz se o pacote é ONNX int8 ou PyTorch (formato de treino) e `motor`
o que está rodando agora. O ideal é publicar o pacote ONNX pronto (README): aí não há conversão nenhuma.

Fonte: config `model_source` ou a linha `flash=` de modelo-fonte.txt. Formatos:
  gdrive:<id do arquivo>                 .zip compartilhado "qualquer pessoa com o link"
  gdrive-folder:<id da pasta>[:<subpasta>]
  hf:<dono/repositório>[@revisão]        repositório do Hugging Face (HF_TOKEN para privados)
  https://.../modelo.zip                 URL direta de um .zip
Destino: %LOCALAPPDATA%/Djorge/modelos/<nome> (fora do Windows: DJORGE_DATA_DIR ou ~/.djorge-local).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger("djorge.model_download")

NAME = "flash"
PRODUCT = "DOFL V2"
SIZE_MB = 241                       # tamanho aproximado do pacote (usado quando o servidor não informa)
CHUNK = 1 << 20
MAX_UNZIPPED = 20 * 1024 ** 3       # proteção contra "zip bomb"
UA = "DjorgeLocal/2 (Windows)"


class Cancelled(Exception):
    pass


def data_root() -> Path:
    """%LOCALAPPDATA%/Djorge (Windows). DJORGE_DATA_DIR sobrepõe (testes, instalações portáteis)."""
    if os.environ.get("DJORGE_DATA_DIR"):
        return Path(os.environ["DJORGE_DATA_DIR"])
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Djorge"
    from .config import HOME
    return HOME


def models_root() -> Path:
    return data_root() / "modelos"


def disk_free(path: Path) -> int:
    """Bytes livres no disco de `path` (função separada para os testes simularem disco cheio)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _source_files():
    here = Path(__file__).resolve().parent
    cands = [here.parent / "modelo-fonte.txt"]
    if getattr(sys, "frozen", False):
        cands.insert(0, Path(sys.executable).resolve().parent / "modelo-fonte.txt")
        if getattr(sys, "_MEIPASS", None):
            cands.insert(1, Path(sys._MEIPASS) / "modelo-fonte.txt")
    return cands


def read_source(cfg, name=NAME) -> str:
    s = (cfg.get("model_source") or "").strip() if isinstance(cfg.get("model_source"), str) else ""
    if isinstance(cfg.get("model_source"), dict):
        s = str(cfg["model_source"].get(name) or "").strip()
    if s:
        return s
    for f in _source_files():
        if not f.is_file():
            continue
        for line in f.read_bytes().decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, sep, v = line.partition("=")
            if sep and k.strip() == name:
                return v.strip()
        break
    return ""


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else 0


def find_artifact(root: Path):
    if (root / "metadata" / "djorge.json").is_file():
        return root
    for p in sorted(root.rglob("djorge.json"), key=lambda x: len(x.parts)):
        if p.parent.name == "metadata":
            return p.parent.parent
    return None


def safe_extract(zpath: Path, dest: Path, cancel: threading.Event = None):
    """Extrai sem path traversal: recusa caminhos absolutos, '..', letras de unidade e links simbólicos."""
    dest = dest.resolve()
    with zipfile.ZipFile(zpath) as z:
        infos = z.infolist()
        total = 0
        for i in infos:
            n = i.filename.replace("\\", "/")
            parts = [p for p in n.split("/") if p]
            if n.startswith("/") or ":" in (parts[0] if parts else "") or ".." in parts:
                raise ValueError(f"arquivo .zip inseguro (caminho fora da pasta): {i.filename}")
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ValueError(f"arquivo .zip inseguro (link simbólico): {i.filename}")
            t = (dest / n).resolve()
            if t != dest and dest not in t.parents:
                raise ValueError(f"arquivo .zip inseguro (caminho fora da pasta): {i.filename}")
            total += i.file_size
        if total > MAX_UNZIPPED:
            raise ValueError("arquivo .zip grande demais depois de extraído")
        for i in infos:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            z.extract(i, dest)


def summarize(meta: dict, art: Path = None) -> dict:
    """Resumo legível de metadata/djorge.json para o card do modelo."""
    def j(v):
        if isinstance(v, str) and v[:1] == "{":
            try:
                import ast
                return ast.literal_eval(v)
            except Exception:
                return {}
        return v if isinstance(v, dict) else {}
    ident, vers, tiny, mc = j(meta.get("identity")), j(meta.get("versions")), j(meta.get("tiny_config")), j(meta.get("model_config"))
    name = meta.get("product") or meta.get("name") or ident.get("name") or meta.get("model_id") or PRODUCT
    version = meta.get("version") or meta.get("recipe") or vers.get("djorge") or meta.get("djorge_version") or ""
    recipe = meta.get("recipe") or meta.get("dataset_version") or ""
    vocab = meta.get("vocab_size") or meta.get("vocab") or tiny.get("vocab") or mc.get("vocab") or mc.get("vocab_size")
    quant = meta.get("quantization") or meta.get("quant")
    oq = (meta.get("onnx") or {}).get("quant") if isinstance(meta.get("onnx"), dict) else None
    if not quant and oq:
        from .otimizar import descrever_quant
        quant = descrever_quant(oq)
    if not quant and art is not None:
        q = []
        if (art / "onnx" / "encoder.int8.onnx").is_file():
            q.append("int8 (CPU)")
        if (art / "onnx" / "encoder.fp16.onnx").is_file():
            q.append("fp16 (GPU)")
        quant = " · ".join(q) or ("ONNX" if (art / "onnx").is_dir() else "PyTorch (sem quantização)")
    date = meta.get("created") or meta.get("date") or meta.get("built")
    if not date and art is not None and (art / "metadata" / "djorge.json").is_file():
        date = time.strftime("%Y-%m-%d", time.localtime((art / "metadata" / "djorge.json").stat().st_mtime))
    size_mb = round(_dir_size(art) / 1e6, 1) if art is not None else None
    out = {"produto": str(name), "versao": str(version), "receita": str(recipe), "tamanho_mb": size_mb,
           "quantizacao": quant or "", "vocabulario": vocab, "data": str(date or ""),
           "base": str(meta.get("backbone") or ""), "familia": ident.get("family", "")}
    out["texto"] = " · ".join(x for x in (
        out["produto"], f"versão {out['versao']}" if out["versao"] else "",
        f"{size_mb:.0f} MB" if size_mb else "", out["quantizacao"],
        f"vocabulário {vocab}" if vocab else "", out["data"]) if x)
    return out


class ModelDownloader:
    def __init__(self, cfg, models, save=None, name=NAME):
        self.cfg, self.models, self.name, self._save = cfg, models, name, save
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._st = {"state": None, "progress": 0.0, "bytes": 0, "total": 0, "error": None}

    # ------------------------------------------------------------------ estado --
    def _path(self):
        p = ((self.cfg.get("models") or {}).get(self.name) or {}).get("path") or ""
        if not p:
            return None
        try:
            from .models import model_path
            art = model_path(self.cfg, p)
        except Exception:
            art = Path(p)
        return art if (art / "metadata" / "djorge.json").is_file() else None

    def target(self) -> Path:
        return models_root() / self.name

    def info(self) -> dict:
        art = self._path()
        st = dict(self._st)
        busy = self._thread is not None and self._thread.is_alive()
        state = st["state"] if busy or st["state"] == "erro" else None
        if state is None:
            state = "instalado" if art else "ausente"
        details, version, size = {}, "", SIZE_MB
        if art:
            try:
                details = summarize(json.load(open(art / "metadata" / "djorge.json", encoding="utf-8")), art)
                version, size = details.get("versao", ""), details.get("tamanho_mb") or SIZE_MB
            except Exception as e:
                details = {"erro": f"metadata/djorge.json ilegível: {e}"}
        loaded = False
        try:
            loaded = self.name in (self.models.loaded() if self.models is not None else [])
        except Exception:
            pass
        out = {"installed": bool(art), "name": PRODUCT, "version": version, "path": str(art) if art else None,
               "size_mb": size, "state": state, "progress": 1.0 if state == "instalado" else st["progress"],
               "bytes": st["bytes"], "total": st["total"], "error": st["error"],
               "source": read_source(self.cfg, self.name), "details": details, "loaded": loaded,
               "formato": None, "motor": None, "mensagem": "", "otimizacao": None,
               "quantizacao": "", "ms_medio": None, "cache": None, "motor_metricas": None}
        if art:
            from . import otimizar as O
            out["formato"] = "ONNX int8" if O.eh_pacote_onnx(art) else "PyTorch (formato de treino)"
            try:
                out["motor"] = getattr(self.models, "engine", {}).get(self.name)
            except Exception:
                pass
            out["quantizacao"] = details.get("quantizacao") or ""
            try:
                mi = self.models.motor_info(self.name) if hasattr(self.models, "motor_info") else {}
            except Exception:
                mi = {}
            out["ms_medio"] = mi.get("ms_por_pergunta_modelo") if mi else None
            out["cache"] = mi.get("cache") if mi else None
            out["motor_metricas"] = mi or None
            opt = O.OTIMIZADOR.info(art)
            if opt:
                out["otimizacao"] = opt
                if opt.get("state") == "otimizando" and state == "instalado":
                    out.update(state="otimizando", progress=float(opt.get("progress") or 0.0),
                               mensagem=opt.get("mensagem") or O.MENSAGEM, etapa=opt.get("etapa") or O.MENSAGEM)
                elif opt.get("state") in ("indisponivel", "erro"):
                    out["mensagem"] = opt.get("mensagem") or ""
        return out

    def _set(self, **kw):
        with self._lock:
            self._st.update(kw)
            if self._st["total"]:
                self._st["progress"] = max(0.0, min(1.0, self._st["bytes"] / self._st["total"]))

    # ----------------------------------------------------------------- ações --
    def start(self) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.info()
            src = read_source(self.cfg, self.name)
            self._cancel.clear()
            self._st.update(state="baixando", progress=0.0, bytes=0, total=0, error=None)
            if not src:
                self._st.update(state="erro", error="fonte do modelo não configurada (model_source ou modelo-fonte.txt)")
                return self.info()
            self._thread = threading.Thread(target=self._run, args=(src,), daemon=True, name="baixar-modelo")
            self._thread.start()
        return self.info()

    def cancel(self) -> dict:
        self._cancel.set()
        t = self._thread
        if t is not None:
            t.join(timeout=10)
        return self.info()

    def remove(self) -> dict:
        self.cancel()
        try:
            if self.models is not None and hasattr(self.models, "unload"):
                self.models.unload(self.name)
        except Exception:
            pass
        t = self.target()
        shutil.rmtree(t, ignore_errors=True)
        shutil.rmtree(models_root() / f".{self.name}.parcial", ignore_errors=True)
        try:
            from . import otimizar as O
            O.OTIMIZADOR.esquecer(t)
        except Exception:
            pass
        m = self.cfg.setdefault("models", {}).setdefault(self.name, {"enabled": True, "role": ""})
        m["path"] = ""
        self._persist()
        self._st.update(state=None, progress=0.0, bytes=0, total=0, error=None)
        return self.info()

    def otimizar(self) -> dict:
        """Converte o modelo instalado para ONNX int8 agora (normalmente acontece sozinho ao carregar)."""
        art = self._path()
        if art is None:
            return self.info()
        from . import otimizar as O
        if O.precisa_otimizar(art):
            O.OTIMIZADOR.esquecer(art)
            if self.models is not None and hasattr(self.models, "_maybe_optimize"):
                self.models._maybe_optimize(self.name, art)
            else:
                O.OTIMIZADOR.iniciar(art)
        return self.info()

    def instalar_de_arquivo(self, caminho_zip, fundo: bool = False) -> dict:
        """Instala um pacote .zip já baixado pela pessoa (ex.: o pacote ONNX candidato do treino).
        Valida (zip seguro, metadata/djorge.json legível e onnx/encoder.json com o arquivo do encoder presente),
        extrai para a pasta de modelos (%LOCALAPPDATA%/Djorge/modelos/<nome>), grava o caminho na config e
        carrega o modelo. fundo=True: roda numa thread (acompanhe por info()). Devolve info().
        Levanta ValueError (síncrono) se o arquivo não for um pacote válido."""
        zp = Path(caminho_zip)
        if not zp.is_file() or not zipfile.is_zipfile(zp):
            raise ValueError(f"não é um arquivo .zip: {zp}")
        if self._thread is not None and self._thread.is_alive():
            raise ValueError("já há uma instalação/baixa do modelo em andamento")
        self._cancel.clear()

        def run():
            work = models_root() / f".{self.name}.arquivo"
            try:
                shutil.rmtree(work, ignore_errors=True)
                work.mkdir(parents=True, exist_ok=True)
                self._set(state="verificando", progress=0.1, error=None)
                need = sum(i.file_size for i in zipfile.ZipFile(zp).infolist())
                self._need_space(need)
                safe_extract(zp, work / "x", self._cancel)
                art = find_artifact(work / "x")
                if art is None:
                    raise ValueError("o arquivo não é um modelo DJORGE (falta metadata/djorge.json)")
                json.load(open(art / "metadata" / "djorge.json", encoding="utf-8"))
                from . import otimizar as O
                if not O.eh_pacote_onnx(art) and not (art / "onnx" / "encoder.json").is_file():
                    raise ValueError("o arquivo não traz o encoder ONNX (onnx/encoder.json + onnx/encoder.*.onnx)")
                info = json.load(open(art / "onnx" / "encoder.json", encoding="utf-8"))
                if not any((art / f).is_file() for f in (info.get("files") or {}).values()):
                    raise ValueError("onnx/encoder.json aponta para um encoder que não está no arquivo")
                self._set(progress=0.9)
                self._install(art)
                shutil.rmtree(work, ignore_errors=True)
                self._set(state="instalado", progress=1.0, error=None)
                self._load()
            except Exception as e:
                log.exception("instalar modelo de arquivo falhou")
                shutil.rmtree(work, ignore_errors=True)
                self._set(state="erro", error=str(e) or e.__class__.__name__)
                if not fundo:
                    raise
        if fundo:
            self._thread = threading.Thread(target=run, daemon=True, name="instalar-modelo-arquivo")
            self._thread.start()
        else:
            run()
        return self.info()

    def wait(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)
        return self.info()

    # ------------------------------------------------------------- execução --
    def _run(self, src):
        work = models_root() / f".{self.name}.parcial"
        try:
            work.mkdir(parents=True, exist_ok=True)
            x = work / "x"
            shutil.rmtree(x, ignore_errors=True)
            kind, _, rest = src.partition(":")
            if kind in ("http", "https"):
                self._get_zip(src, work / "m.zip", x)
            elif kind == "gdrive":
                self._gdrive(rest, work, x)
            elif kind == "gdrive-folder":
                self._gdrive_folder(rest, x)
            elif kind == "hf":
                self._hf(rest, x)
            else:
                raise ValueError(f"fonte desconhecida: {src} (use gdrive:, gdrive-folder:, hf: ou https://)")
            self._check()
            self._set(state="verificando")
            art = find_artifact(x)
            if art is None:
                raise ValueError("o pacote baixado não é um modelo DJORGE (falta metadata/djorge.json)")
            json.load(open(art / "metadata" / "djorge.json", encoding="utf-8"))
            from . import otimizar as O
            if O.precisa_otimizar(art):
                log.info("pacote baixado está no formato de treino (sem onnx/): será otimizado ao carregar")
            self._install(art)
            shutil.rmtree(work, ignore_errors=True)
            self._set(state="instalado", progress=1.0, error=None)
            log.info("modelo %s instalado em %s", self.name, self.target())
            self._load()
        except Cancelled:
            log.info("download do modelo cancelado")
            shutil.rmtree(work / "x", ignore_errors=True)
            self._set(state=None, error=None)
        except Exception as e:
            log.exception("download do modelo falhou")
            shutil.rmtree(work / "x", ignore_errors=True)
            self._set(state="erro", error=str(e) or e.__class__.__name__)

    def _check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def _need_space(self, total):
        need = int((total or SIZE_MB * 1_000_000) * 2.2) + 50_000_000     # zip + extraído + folga
        free = disk_free(models_root())
        if free < need:
            raise OSError(f"espaço insuficiente em disco: precisa de {need / 1e6:.0f} MB, livres {free / 1e6:.0f} MB")

    def _get_zip(self, url, zpath, x):
        self._download(url, zpath)
        self._check()
        self._set(state="verificando")
        x.mkdir(parents=True, exist_ok=True)
        safe_extract(zpath, x, self._cancel)

    def _download(self, url, out: Path):
        """HTTP com progresso real e retomada (Range) a partir de out.part."""
        part = out.with_suffix(out.suffix + ".part")
        have = part.stat().st_size if part.is_file() else 0
        req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Range": f"bytes={have}-"} if have else {})})
        r = urllib.request.urlopen(req, timeout=60)
        ctype = r.headers.get("Content-Type", "")
        if "text/html" in ctype:
            r.close()
            raise ValueError("a fonte devolveu uma página em vez do arquivo .zip (link sem permissão pública?)")
        if have and r.status == 206:
            rng = r.headers.get("Content-Range", "")
            total = int(rng.rsplit("/", 1)[-1]) if "/" in rng and rng.rsplit("/", 1)[-1].isdigit() else have + int(r.headers.get("Content-Length") or 0)
            mode = "ab"
        else:
            have, mode = 0, "wb"
            total = int(r.headers.get("Content-Length") or 0)
        self._need_space(max(0, total - have) if total else 0)
        self._set(total=total, bytes=have)
        with r, open(part, mode) as f:
            while True:
                self._check()
                b = r.read(CHUNK)
                if not b:
                    break
                f.write(b)
                have += len(b)
                self._set(bytes=have)
        if total and have < total:
            raise IOError("download interrompido; tente de novo para continuar de onde parou")
        os.replace(part, out)

    def _gdrive(self, fid, work, x):
        url = f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
        z = work / "m.zip"
        try:
            self._download(url, z)
        except (ValueError, OSError) as e:
            if isinstance(e, OSError) and "espaço" in str(e):
                raise
            self._check()
            import gdown                                       # aviso de vírus / cota: o gdown sabe lidar
            self._watch(work, lambda: gdown.download(id=fid, output=str(z), quiet=True))
        self._check()
        self._set(state="verificando")
        x.mkdir(parents=True, exist_ok=True)
        safe_extract(z, x, self._cancel)

    def _gdrive_folder(self, spec, x):
        import gdown
        fid, _, sub = spec.partition(":")
        self._need_space(0)
        self._watch(x, lambda: gdown.download_folder(id=fid, output=str(x), quiet=True))
        if sub:
            c = [p for p in x.rglob(sub) if p.is_dir()]
            if not c:
                raise ValueError(f"subpasta {sub} não encontrada na pasta do Drive")
            keep = x.parent / "sel"
            shutil.move(str(c[0]), str(keep)); shutil.rmtree(x); keep.rename(x)

    def _hf(self, spec, x):
        from huggingface_hub import snapshot_download
        repo, _, rev = spec.partition("@")
        total = 0
        try:
            from huggingface_hub import HfApi
            mi = HfApi().model_info(repo, revision=rev or None, files_metadata=True, token=os.environ.get("HF_TOKEN") or None)
            total = sum(int(getattr(s, "size", 0) or 0) for s in mi.siblings or [])
        except Exception:
            pass
        self._need_space(total)
        self._set(total=total)
        self._watch(x, lambda: snapshot_download(repo, revision=rev or None, local_dir=str(x),
                                                 token=os.environ.get("HF_TOKEN") or None))

    def _watch(self, path: Path, fn):
        """Bibliotecas sem callback de progresso: mede o que já chegou no disco enquanto `fn` roda."""
        done, err = threading.Event(), []

        def go():
            try:
                fn()
            except Exception as e:
                err.append(e)
            finally:
                done.set()
        threading.Thread(target=go, daemon=True).start()
        if not self._st["total"]:
            self._set(total=SIZE_MB * 1_000_000)
        while not done.wait(0.5):
            self._check()               # a thread da biblioteca continua, mas o resultado é descartado
            n = _dir_size(path) if path.is_dir() else (path.stat().st_size if path.is_file() else 0)
            self._set(bytes=min(n, self._st["total"] - 1))
        if err:
            raise err[0]
        self._set(bytes=self._st["total"])

    def _install(self, art: Path):
        self._check()
        try:
            if self.models is not None and hasattr(self.models, "unload") and self.name in self.models.loaded():
                self.models.unload(self.name)
        except Exception:
            pass
        t = self.target()
        old = t.with_name(f".{self.name}.antigo")
        shutil.rmtree(old, ignore_errors=True)
        if t.exists():
            os.replace(t, old)
        shutil.move(str(art), str(t))
        shutil.rmtree(old, ignore_errors=True)
        m = self.cfg.setdefault("models", {}).setdefault(self.name, {"enabled": True, "role": ""})
        m["path"], m["enabled"] = str(t), True
        self._persist()

    def _persist(self):
        if self._save:
            self._save()
            return
        p = self.cfg.get("_path")
        if not p:
            return
        import yaml
        p = Path(p)
        raw = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else None) or {}
        ms = raw.setdefault("models", {})
        if not isinstance(ms, dict):
            ms = raw["models"] = {}
        ms[self.name] = {k: v for k, v in self.cfg["models"][self.name].items() if not str(k).startswith("_")}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, p)

    def _load(self):
        try:
            if self.models is not None and hasattr(self.models, "preload"):
                if hasattr(self.models, "state"):
                    self.models.state.pop(self.name, None)
                self.models.preload([self.name])
        except Exception as e:
            log.warning("modelo instalado, mas não carregou agora: %s", e)


# ------------------------------------------------------------------------------ catálogo de modelos --
class _CatalogoDownloader(ModelDownloader):
    """Baixa um modelo do catálogo para modelos/<id> SEM mexer em cfg.models (não vira um segundo modelo
    carregado): só registra o caminho no catálogo."""

    def __init__(self, cat, ident, fonte):
        super().__init__(cat.cfg, None, None, name=ident)
        self.cat, self.fonte = cat, fonte

    def start(self) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.info()
            self._cancel.clear()
            self._st.update(state="baixando", progress=0.0, bytes=0, total=0, error=None)
            self._thread = threading.Thread(target=self._run, args=(self.fonte,), daemon=True,
                                            name=f"baixar-{self.name}")
            self._thread.start()
        return self.info()

    def _install(self, art: Path):
        self._check()
        t = models_root() / self.name
        shutil.rmtree(t, ignore_errors=True)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(art), str(t))
        self.cat._atualizar(self.name, path=str(t))

    def _load(self):
        pass

    def _persist(self):
        pass


class Catalogo:
    """Modelos que a pessoa pode escolher. O DOFL V2 int8 (fonte padrão: modelo-fonte.txt) é sempre o
    primeiro; outros entram por URL (https:, hf:, gdrive:), arquivo .zip ou pasta local. Um só é usado por vez
    como o modelo `flash` do app; trocar não exige reiniciar (descarrega e recarrega em segundo plano).
    Guardado em <dados>/modelos/catalogo.json.

        listar() -> [{id, nome, fonte, tipo, path, tamanho_mb, estado, em_uso, padrao, progresso, erro}]
        adicionar(fonte, nome=None) -> item      (URL: começa a baixar; .zip: extrai; pasta: só registra)
        usar(id) -> item                         remover(id) -> {"removido": id}
        cancelar(id) -> item
    """
    PADRAO_ID = "dofl-v2-int8"
    ID = re.compile(r"[^a-z0-9_-]+")

    def __init__(self, cfg, models=None, save=None, alvo=NAME):
        self.cfg, self.models, self.save, self.alvo = cfg, models, save, alvo
        self._lock = threading.Lock()
        self._baixando = {}

    # ---------------------------------------------------------------------------- arquivo --
    def _arq(self) -> Path:
        return models_root() / "catalogo.json"

    def _ler(self) -> list:
        try:
            v = json.load(open(self._arq(), encoding="utf-8"))
            return v if isinstance(v, list) else []
        except Exception:
            return []

    def _gravar(self, itens):
        p = self._arq()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(itens, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    def _atualizar(self, ident, **kw):
        with self._lock:
            itens = self._ler()
            for it in itens:
                if it["id"] == ident:
                    it.update(kw)
            self._gravar(itens)

    # ----------------------------------------------------------------------------- leitura --
    def _caminho_em_uso(self):
        p = ((self.cfg.get("models") or {}).get(self.alvo) or {}).get("path") or ""
        if not p:
            return None
        try:
            from .models import model_path
            return str(model_path(self.cfg, p).resolve())
        except Exception:
            return str(Path(p).resolve())

    def _padrao(self) -> dict:
        alvo = models_root() / self.alvo
        return {"id": self.PADRAO_ID, "nome": f"{PRODUCT} V2 (int8)", "fonte": read_source(self.cfg, self.alvo),
                "tipo": "padrao", "path": str(alvo), "padrao": True}

    def listar(self) -> list:
        uso = self._caminho_em_uso()
        out = []
        for it in [self._padrao()] + self._ler():
            p = Path(it.get("path") or "")
            ok = bool(it.get("path")) and (p / "metadata" / "djorge.json").is_file()
            d = self._baixando.get(it["id"])
            st = d.info() if d is not None else {}
            estado = "instalado" if ok else "ausente"
            if st.get("state") in ("baixando", "verificando", "erro"):
                estado = st["state"]
            tam = None
            if ok:
                try:
                    tam = summarize(json.load(open(p / "metadata" / "djorge.json", encoding="utf-8")), p).get("tamanho_mb")
                except Exception:
                    tam = round(_dir_size(p) / 1e6, 1)
            out.append(dict(it, estado=estado, tamanho_mb=tam, em_uso=bool(ok and uso and str(p.resolve()) == uso),
                            padrao=bool(it.get("padrao")), progresso=st.get("progress"), erro=st.get("error")))
        return out

    def item(self, ident) -> dict:
        for it in self.listar():
            if it["id"] == ident:
                return it
        raise KeyError(f"modelo não está no catálogo: {ident}")

    # ------------------------------------------------------------------------------ escrita --
    def _novo_id(self, nome):
        base = self.ID.sub("-", str(nome or "modelo").lower()).strip("-")[:28] or "modelo"
        ids = {i["id"] for i in self._ler()} | {self.PADRAO_ID, self.alvo}
        k, n = base, 2
        while k in ids:
            k, n = f"{base}-{n}", n + 1
        return k

    def adicionar(self, fonte: str, nome: str = None) -> dict:
        fonte = str(fonte or "").strip()
        if not fonte:
            raise ValueError("informe uma URL, um arquivo .zip ou uma pasta de modelo")
        p = Path(fonte).expanduser()
        if p.is_dir():
            art = find_artifact(p)
            if art is None:
                raise ValueError("essa pasta não é um modelo DJORGE (falta metadata/djorge.json)")
            tipo, path = "pasta", str(art)
        elif p.is_file():
            if not zipfile.is_zipfile(p):
                raise ValueError("o arquivo precisa ser um .zip com o modelo")
            tipo, path = "zip", ""
        elif fonte.split(":", 1)[0] in ("http", "https", "hf", "gdrive", "gdrive-folder"):
            tipo, path = "url", ""
        else:
            raise ValueError("fonte não reconhecida: use https://…, hf:dono/repo, gdrive:<id>, um .zip ou uma pasta")
        if not nome:
            nome = p.stem if tipo in ("zip", "pasta") else fonte.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
            nome = re.sub(r"\.zip$", "", nome) or "modelo"
        ident = self._novo_id(nome)
        with self._lock:
            itens = self._ler()
            itens.append({"id": ident, "nome": str(nome)[:60], "fonte": fonte, "tipo": tipo, "path": path,
                          "adicionado": time.strftime("%Y-%m-%d %H:%M")})
            self._gravar(itens)
        if tipo == "zip":
            d = _CatalogoDownloader(self, ident, fonte)
            self._baixando[ident] = d
            try:
                d.instalar_de_arquivo(p)
            except Exception:
                self.remover(ident, apagar=True)
                raise
        elif tipo == "url":
            d = _CatalogoDownloader(self, ident, fonte)
            self._baixando[ident] = d
            d.start()
        return self.item(ident)

    def baixar_padrao(self) -> dict:
        """O padrão é baixado pelo ModelDownloader do card (fonte modelo-fonte.txt)."""
        d = ModelDownloader(self.cfg, self.models, self.save, name=self.alvo)
        d.start()
        return self.item(self.PADRAO_ID)

    def usar(self, ident) -> dict:
        """Passa a usar este modelo como o `flash` do app, sem reiniciar."""
        it = self.item(ident)
        if it["estado"] != "instalado":
            raise ValueError(f"o modelo {it['nome']} ainda não está instalado ({it['estado']})")
        m = self.cfg.setdefault("models", {}).setdefault(self.alvo, {"enabled": True, "role": ""})
        m["path"], m["enabled"] = it["path"], True
        if self.models is not None:
            try:
                self.models.unload(self.alvo)
                mo = (getattr(self.models, "motores", {}) or {}).get(self.alvo)
                if mo is not None:
                    mo.limpar()                       # respostas do modelo anterior não valem mais
                self.models.state.pop(self.alvo, None)
                self.models.preload([self.alvo])
            except Exception as e:
                log.warning("trocar de modelo: %s", e)
        ModelDownloader(self.cfg, self.models, self.save, name=self.alvo)._persist()
        return self.item(ident)

    def cancelar(self, ident) -> dict:
        d = self._baixando.get(ident)
        if d is not None:
            d.cancel()
        return self.item(ident)

    def remover(self, ident, apagar: bool = True) -> dict:
        """Tira do catálogo (e apaga a pasta se foi o app que baixou/extraiu). O modelo em uso não pode ser
        removido: escolha outro antes. O padrão não sai do catálogo (só a pasta baixada, pelo card)."""
        if ident == self.PADRAO_ID:
            raise ValueError("o modelo padrão não sai do catálogo (use Remover no card do modelo)")
        it = next((i for i in self.listar() if i["id"] == ident), None)
        if it is None:
            raise KeyError(f"modelo não está no catálogo: {ident}")
        if it["em_uso"]:
            raise ValueError("este modelo está em uso: escolha outro antes de remover")
        self.cancelar(ident) if ident in self._baixando else None
        self._baixando.pop(ident, None)
        p = Path(it.get("path") or "")
        if apagar and it.get("path") and models_root().resolve() in p.resolve().parents:
            shutil.rmtree(p, ignore_errors=True)       # pasta local escolhida pela pessoa nunca é apagada
        with self._lock:
            self._gravar([i for i in self._ler() if i["id"] != ident])
        return {"removido": ident}
