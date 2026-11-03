"""Otimizar o modelo para este computador: artefato de treino (PyTorch, sem onnx/) -> pacote Local ONNX int8.

Feito UMA vez, em segundo plano, quando o modelo instalado não traz `onnx/` nem `local_profiles.json`
(ex.: o .zip publicado era a pasta DOFL_FINAL do treino). Mesma receita de tools/export_local.py do repositório
de treino: cópia enxuta (cabeças de decisão, calibração, tokenizador, metadata), corte de vocabulário, encoder
ONNX, quantização int8 (reduce_range) em escada (all -> mlp -> embed -> none) e TESTE DE PARIDADE contra o
PyTorch (>= 99 % das respostas iguais e diferença média de probabilidade < 0,03). Pacote que muda respostas é
recusado e o app segue em PyTorch.

A conversão precisa de PyTorch + onnx + onnxscript (só para converter: o pacote pronto roda sem torch). O
instalador do Djorge NÃO traz o PyTorch; nele `pode_converter()` é falso e o card pede o pacote ONNX pronto
(veja o README: "Publicar o pacote ONNX").

    pode_converter() -> (bool, motivo)
    precisa_otimizar(pasta) -> bool
    OTIMIZADOR.iniciar(pasta, antes_de_trocar=None, depois=None) / .info(pasta) / .ocupado()
    converter(artefato, saida, progresso=None) -> relatório        (síncrono, usado pela thread)
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import threading
import time
from collections import Counter
from pathlib import Path

log = logging.getLogger("djorge.otimizar")

MENSAGEM = "Otimizando o modelo para este computador…"
PRECISA_PACOTE = ("Este modelo veio no formato de treino (PyTorch) e este computador não consegue convertê-lo. "
                  "Baixe o pacote leve (ONNX int8) do modelo — o dono do modelo precisa publicá-lo.")
PROFILES = {"cpu": {"needs": "any computer", "onnx": "int8", "providers": ["CPUExecutionProvider"]}}
ENCODER_KEYS = ("adapter.", "proj.", "role.", "ln.", "mix", "special")
META_DROP = ("train_args", "train_log", "train_seconds", "teacher", "distill", "dev_curve", "init_from",
             "public_file", "dataset_version", "seed")
TOKENIZER_KEEP = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")
LADDER = ("all", "all_fr", "attn", "mlp", "embed", "none")


def _tem(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def pode_converter():
    """Converter exige PyTorch, onnx, onnxscript (exportador) e onnxruntime.quantization."""
    if os.environ.get("DJORGE_SEM_CONVERSAO") == "1":
        return False, "conversão desligada (DJORGE_SEM_CONVERSAO=1)"
    falta = [m for m in ("torch", "onnx", "onnxscript", "onnxruntime", "transformers", "safetensors") if not _tem(m)]
    if falta:
        return False, "falta " + ", ".join(falta)
    return True, ""


def eh_pacote_onnx(art: Path) -> bool:
    art = Path(art)
    if not (art / "local_profiles.json").is_file() or not (art / "onnx" / "encoder.json").is_file():
        return False
    try:
        files = json.load(open(art / "onnx" / "encoder.json", encoding="utf-8")).get("files", {})
    except Exception:
        return False
    return any((art / f).is_file() for f in files.values())


def pasta_gerenciada(art: Path) -> bool:
    """A pasta foi baixada pelo app (models_root)? Só essas são convertidas no lugar."""
    try:
        from .model_download import models_root
        r = models_root().resolve()
        a = Path(art).resolve()
        return a != r and r in a.parents
    except Exception:
        return False


def precisa_otimizar(art: Path) -> bool:
    art = Path(art)
    return (art / "metadata" / "djorge.json").is_file() and not eh_pacote_onnx(art)


# ------------------------------------------------------------------------------------------ receita --
def _size(p):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs)


def slim_copy(src, out):
    from safetensors.torch import load_file, save_file
    os.makedirs(os.path.join(out, "metadata")); os.makedirs(os.path.join(out, "decision"))
    meta = json.load(open(os.path.join(src, "metadata", "djorge.json"), encoding="utf-8"))
    json.dump({k: v for k, v in meta.items() if k not in META_DROP},
              open(os.path.join(out, "metadata", "djorge.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    sd = load_file(os.path.join(src, "decision", "core.safetensors"))
    heads = {k: v.float().contiguous() for k, v in sd.items()
             if not k.startswith(ENCODER_KEYS) and not k.startswith("backbone.")}
    save_file(heads, os.path.join(out, "decision", "core.safetensors"))
    cal = os.path.join(src, "calibration")
    if os.path.isdir(cal):
        os.makedirs(os.path.join(out, "calibration"))
        for f in os.listdir(cal):
            if f.endswith(".json"):
                shutil.copy(os.path.join(cal, f), os.path.join(out, "calibration", f))
    for extra in ("reputation.json",):
        if os.path.isfile(os.path.join(src, extra)):
            shutil.copy(os.path.join(src, extra), os.path.join(out, extra))
    tok_src = next((os.path.join(src, d) for d in ("tokenizer", "backbone")
                    if os.path.exists(os.path.join(src, d, "tokenizer_config.json"))), None)
    tdir = os.path.join(out, "tokenizer")
    from transformers import AutoTokenizer
    full = AutoTokenizer.from_pretrained(tok_src) if tok_src else \
        AutoTokenizer.from_pretrained(meta["backbone"], revision=meta.get("revision"))
    full.save_pretrained(tdir)
    keep_all = {f: open(os.path.join(tdir, f), "rb").read() for f in os.listdir(tdir)}
    for f in list(keep_all):
        if f not in TOKENIZER_KEEP:
            os.remove(os.path.join(tdir, f))
    try:                                   # o tokenizador rápido sozinho precisa dar os mesmos ids
        slim = AutoTokenizer.from_pretrained(tdir)
        txt = ["O boleto vence amanhã, R$ 1.234,56 — ok?", "Ana mora em Lima 😀 https://x.com/a?b=1", "日本語 テスト"]
        assert all(slim(t)["input_ids"] == full(t)["input_ids"] for t in txt)
    except Exception:
        for f, b in keep_all.items():
            open(os.path.join(tdir, f), "wb").write(b)
    return meta


def vocab_keep(tok, V, cjk_top=6000):
    """Linhas de token mantidas (mesma regra do export_local.py): latim/cirílico/grego/símbolos/emoji, bytes,
    partes de UTF-8, especiais e os chineses mais frequentes."""
    from djorge.runtime.clean import script_of
    keep, cat, cjk = set(), {}, []
    base = getattr(tok, "vocab_size", V)
    for i in range(min(V, len(tok))):
        if i >= base:
            keep.add(i); cat[i] = "especiais"; continue
        piece = tok.convert_ids_to_tokens(i) or ""
        if len(piece) <= 1:
            keep.add(i); cat[i] = "bytes"; continue
        txt = tok.decode([i])
        if "�" in txt:
            keep.add(i); cat[i] = "partes de byte"; continue
        sc = {script_of(c) for c in txt} - {""}
        if not sc or sc <= {"LATIN", "CYRILLIC", "GREEK"}:
            keep.add(i); cat[i] = "latim/cirílico/grego/símbolos"
        elif sc <= {"CJK", "LATIN"}:
            cjk.append(i)
    for i in sorted(cjk)[:cjk_top]:
        keep.add(i); cat[i] = "chinês frequente"
    for i in getattr(tok, "all_special_ids", []):
        keep.add(i); cat.setdefault(i, "especiais")
    return sorted(keep), {"total": V, "kept": len(keep), "by_category": dict(Counter(cat.values()))}


def quantize(fp32, out, mode, reduce_range=True, inter=None):
    import onnx
    if mode == "none":
        onnx.save(onnx.load(fp32), out, save_as_external_data=False)
        return
    from onnxruntime.quantization import QuantType, quantize_dynamic
    m = onnx.load(fp32, load_external_data=False)
    shapes = {t.name: list(t.dims) for t in m.graph.initializer}
    exclude = []
    if mode == "all_fr":                       # int8 completo com faixa cheia (mais precisão)
        mode, reduce_range = "all", False
    if mode in ("mlp", "embed", "attn"):
        for n in m.graph.node:
            if n.op_type != "MatMul":
                continue
            dims = next((shapes[i] for i in n.input if i in shapes), None)
            is_mlp = bool(dims and inter and inter in dims)
            if mode == "embed" or (mode == "mlp" and not is_mlp) or (mode == "attn" and is_mlp):
                exclude.append(n.name)
    quantize_dynamic(fp32, out, weight_type=QuantType.QInt8, per_channel=True, reduce_range=reduce_range,
                     nodes_to_exclude=exclude, use_external_data_format=False)


def linhas_de_teste():
    from djorge.runtime.semantic import SemReq
    txt = ["O boleto vence amanhã e custa R$ 120,00.", "Reunião urgente com o cliente às 9h.",
           "Seu pedido saiu para entrega.", "Ganhe um iPhone agora, clique no link!", "Ana mora em Lima.",
           "Me lembra de ligar pro dentista sexta às 10h.", "Quanto é 15% de 230?", "Cria uma lista de compras."]
    return [SemReq("CHOICE", t, options=["cobrança", "entrega", "trabalho", "outro"], ask="Do que se trata?")
            for t in txt] + [SemReq("NOUL", t, options=("o texto fala de dinheiro",)) for t in txt]


def converter(artifact, out, progresso=None, ladder=LADDER, cjk_top=6000):
    """Artefato PyTorch -> pacote Local ONNX int8 em `out`. Levanta RuntimeError se a paridade falhar."""
    p = progresso or (lambda f, etapa: None)
    artifact, out = str(artifact), str(out)
    from djorge.runtime.onnx_backend import export_encoder
    from djorge.runtime.semantic import SemanticEngine
    if os.path.exists(out):
        shutil.rmtree(out)
    t0, before = time.time(), _size(artifact)
    p(0.05, "Copiando as partes de decisão…")
    slim_copy(artifact, out)
    p(0.12, "Escolhendo o vocabulário usado…")
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(os.path.join(out, "tokenizer"))
    V = None
    bb = os.path.join(artifact, "backbone", "model.safetensors")
    if os.path.exists(bb):
        from safetensors import safe_open
        with safe_open(bb, "pt") as fh:
            k = next((k for k in fh.keys() if k.endswith("embed_tokens.weight")), None)
            V = fh.get_slice(k).get_shape()[0] if k else None
    keep, vrep = vocab_keep(tok, V or len(tok), cjk_top)
    p(0.2, "Gerando o encoder ONNX (pode levar alguns minutos)…")
    info = export_encoder(artifact, out, int8=False, fp16=False, keep_ids=keep, reduce_range=True)
    fp32 = os.path.join(out, "onnx", "encoder.onnx")
    q = os.path.join(out, "onnx", "encoder.int8.onnx")
    info["files"]["int8"] = "onnx/encoder.int8.onnx"
    json.dump(info, open(os.path.join(out, "onnx", "encoder.json"), "w"), indent=1)
    p(0.6, "Conferindo as respostas com o modelo original…")
    ref = SemanticEngine.from_artifact(artifact, device="cpu")
    reqs = []
    for r in linhas_de_teste():
        try:
            ref._rows(r); reqs.append(r)
        except ValueError:
            pass
    r0 = ref.run(reqs)
    del ref
    report = {"rows": len(reqs), "ladder": []}

    def measure():
        eng = SemanticEngine.from_artifact(out, device="cpu", onnx="int8")
        t = time.time(); r1 = eng.run(reqs); dt = (time.time() - t) / max(1, len(reqs))
        agree = sum(x.get("answer") == y.get("answer") for x, y in zip(r0, r1)) / max(1, len(reqs))
        dp = sum(abs(float(x.get("probability") or 0) - float(y.get("probability") or 0))
                 for x, y in zip(r0, r1)) / max(1, len(reqs))
        return {"agreement": round(agree, 4), "mean_abs_prob_diff": round(dp, 4), "ms_per_row_cpu": round(dt * 1000, 1)}

    passes = lambda m: m["agreement"] >= 0.99 and m["mean_abs_prob_diff"] < 0.03
    chosen = None
    for i, mode in enumerate(ladder):
        p(0.65 + 0.3 * i / max(1, len(ladder)), f"Quantizando em int8 ({mode}) e conferindo…")
        quantize(fp32, q, mode, reduce_range=True, inter=info.get("intermediate"))
        m = measure()
        m["mode"], m["mb"] = mode, round(os.path.getsize(q) / 1e6, 1)
        report["ladder"].append(m)
        log.info("quantização %s: %s", mode, m)
        if passes(m):
            chosen = m
            break
    for f in ("encoder.onnx", "encoder.onnx.data"):
        fp = os.path.join(out, "onnx", f)
        if os.path.exists(fp):
            os.remove(fp)
    info["files"].pop("fp32", None)
    if not chosen:
        shutil.rmtree(out, ignore_errors=True)
        raise RuntimeError("a versão ONNX mudaria as respostas do modelo (teste de paridade): seguindo em PyTorch")
    info["quant"] = chosen["mode"]
    report["variants"] = {"int8": chosen}
    report["passed"] = True
    json.dump(info, open(os.path.join(out, "onnx", "encoder.json"), "w"), indent=1)
    json.dump({"name": "Djorge Origo Flash Local", "profiles": PROFILES, "default": "cpu", "converted_locally": True},
              open(os.path.join(out, "local_profiles.json"), "w"), indent=1)
    mp = os.path.join(out, "metadata", "djorge.json")
    meta_o = json.load(open(mp, encoding="utf-8"))
    meta_o.update(local_package=True, onnx=info, parity=report, vocab_report=vrep, quantization="int8 (CPU)",
                  converted=time.strftime("%Y-%m-%d"))
    json.dump(meta_o, open(mp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    report.update(size_mb=round(_size(out) / 1e6, 1), source_mb=round(before / 1e6, 1),
                  minutes=round((time.time() - t0) / 60, 1))
    return report


# ----------------------------------------------------------------------------- em segundo plano --
class Otimizador:
    """Uma conversão por vez; estado por pasta (o card do modelo lê via ModelDownloader.info())."""

    def __init__(self):
        self._lock = threading.Lock()
        self._st = {}                # pasta -> {state, progress, etapa, error, report}
        self._thread = None
        self.converter = converter   # trocado nos testes

    def ocupado(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def info(self, art=None) -> dict:
        if art is None:
            return {k: dict(v) for k, v in self._st.items()}
        return dict(self._st.get(str(Path(art)), {}))

    def _set(self, art, **kw):
        with self._lock:
            self._st.setdefault(str(art), {}).update(kw)

    def iniciar(self, art, antes_de_trocar=None, depois=None) -> dict:
        """Começa a conversão de `art` (se possível). antes_de_trocar(): descarrega o modelo (no Windows a
        pasta não pode ser trocada com arquivos abertos). depois(ok: bool): recarrega."""
        art = Path(art)
        with self._lock:
            if self.ocupado():
                return dict(self._st.get(str(art), {}))
            st = self._st.get(str(art), {})
            if st.get("state") in ("erro", "indisponivel"):
                return dict(st)                      # não tenta de novo a cada carregamento
        ok, motivo = pode_converter()
        if not ok:
            self._set(art, state="indisponivel", progress=0.0, etapa="", error=motivo, mensagem=PRECISA_PACOTE)
            return self.info(art)
        self._set(art, state="otimizando", progress=0.0, etapa=MENSAGEM, error=None, mensagem=MENSAGEM)
        self._thread = threading.Thread(target=self._run, args=(art, antes_de_trocar, depois), daemon=True,
                                        name="otimizar-modelo")
        self._thread.start()
        return self.info(art)

    def esquecer(self, art):
        with self._lock:
            self._st.pop(str(Path(art)), None)

    def wait(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, art: Path, antes, depois):
        tmp = art.with_name(f".{art.name}.onnx-tmp")
        old = art.with_name(f".{art.name}.pytorch")
        ok = False
        try:
            _baixa_prioridade_thread()
            rep = self.converter(art, tmp, lambda f, e: self._set(art, progress=round(float(f), 3), etapa=e))
            self._set(art, progress=0.97, etapa="Trocando para a versão leve…")
            if antes:
                antes()
            shutil.rmtree(old, ignore_errors=True)
            os.replace(art, old)
            try:
                os.replace(tmp, art)
            except Exception:
                os.replace(old, art)
                raise
            shutil.rmtree(old, ignore_errors=True)
            self._set(art, state="pronto", progress=1.0, etapa="Modelo otimizado (ONNX int8)", error=None,
                      mensagem="", report=rep)
            log.info("modelo convertido para ONNX int8: %s", rep)
            ok = True
        except Exception as e:
            log.exception("otimização do modelo falhou")
            shutil.rmtree(tmp, ignore_errors=True)
            self._set(art, state="erro", error=str(e) or type(e).__name__,
                      mensagem="Não deu para otimizar o modelo neste computador: ele segue no PyTorch.")
        finally:
            if depois:
                try:
                    depois(ok)
                except Exception:
                    log.exception("recarregar depois da otimização")


def _baixa_prioridade_thread():
    """A conversão é pesada: a thread roda com prioridade baixa no Windows para não travar o computador."""
    try:
        import sys
        if sys.platform.startswith("win"):
            import ctypes
            k = ctypes.windll.kernel32
            k.SetThreadPriority(k.GetCurrentThread(), -2)          # THREAD_PRIORITY_LOWEST
    except Exception:
        pass


OTIMIZADOR = Otimizador()
