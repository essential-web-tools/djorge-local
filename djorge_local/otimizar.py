"""Otimizar o modelo para este computador: artefato de treino (PyTorch, sem onnx/) -> pacote Local ONNX int8.

Feito UMA vez, em segundo plano, quando o modelo instalado não traz `onnx/` nem `local_profiles.json`
(ex.: o .zip publicado era a pasta DOFL_FINAL do treino). Mesma receita de tools/export_local.py do repositório
de treino: cópia enxuta (cabeças de decisão, calibração, tokenizador, metadata), corte de vocabulário, encoder
ONNX (LoRA fundida nos pesos), quantização em escada (int8 completo / só pesos / estático calibrado ->
sem os blocos sensíveis -> mlp/atenção -> só vocabulário + fp16 -> fp32; fica o MENOR que passa) e TESTE DE PARIDADE contra o
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


# Escada de quantização, do pacote mais leve ao mais pesado. Dentro de um degrau todas as variantes são medidas e
# fica a MENOR que passa a paridade; o primeiro degrau com alguma aprovada encerra a escada.
#   all / all_fr   int8 dinâmico per-channel em tudo (reduce_range 7 bits / faixa cheia)
#   wo8            int8 só nos PESOS (MatMulNBits, blocos de 32, conta em fp32): ~3x menos erro que o dinâmico
#   static         int8 estático (QDQ) calibrado com frases pt-BR do próprio app
#   sens           int8 dinâmico, menos os blocos de camadas mais sensíveis (medidos um a um), que ficam em fp16
#   mlp / attn     int8 só nas MLPs / só na atenção
#   embed          só o vocabulário em int8; o resto em fp16 no disco (o pacote publicado até agora, sem fp16)
#   none           fp32
LADDER = (("all", "all_fr", "wo8", "static"), ("sens",), ("mlp", "attn"), ("embed",), ("none",))
PARIDADE = {"agreement": 0.99, "mean_abs_prob_diff": 0.03}


def _flat(ladder):
    out = []
    for g in ladder:
        out += [g] if isinstance(g, str) else list(g)
    return out


def _grupos(ladder):
    return [[g] if isinstance(g, str) else list(g) for g in ladder]


def _weight_matmuls(m):
    """MatMuls com peso (initializer) na ordem do grafo = ordem das camadas."""
    ini = {t.name for t in m.graph.initializer}
    return [n.name for n in m.graph.node if n.op_type == "MatMul" and any(i in ini for i in n.input)]


def blocos(m, n_blocos):
    """Divide as MatMuls com peso em blocos contíguos (≈ uma camada cada) para medir a sensibilidade."""
    mm = _weight_matmuls(m)
    n = max(1, min(len(mm), int(n_blocos)))
    tam = -(-len(mm) // n)
    return [mm[i:i + tam] for i in range(0, len(mm), tam)]


def fp16_storage(path, min_elems=4096):
    """Pesos fp32 que sobraram (não quantizados) gravados em fp16 + Cast: o disco cai pela metade nessas
    partes; a conta continua em fp32 (o ONNX Runtime CPU expande na carga). Só pesos grandes de MatMul/Gather."""
    import numpy as np
    import onnx
    from onnx import helper, numpy_helper
    m = onnx.load(path)
    uso = {}
    for n in m.graph.node:
        for i in n.input:
            uso.setdefault(i, set()).add(n.op_type)
    novos, casts = [], []
    for t in list(m.graph.initializer):
        if t.data_type != onnx.TensorProto.FLOAT or np.prod(t.dims or [1]) < min_elems:
            continue
        if not uso.get(t.name) or not uso[t.name] <= {"MatMul", "Gather", "Gemm"}:
            continue
        arr = numpy_helper.to_array(t)
        if not np.isfinite(arr).all() or np.abs(arr).max() > 6e4:
            continue
        h = numpy_helper.from_array(arr.astype(np.float16), t.name + "__fp16")
        m.graph.initializer.remove(t)
        novos.append(h)
        casts.append(helper.make_node("Cast", [h.name], [t.name], to=onnx.TensorProto.FLOAT, name=t.name + "__cast"))
    if not casts:
        return 0
    m.graph.initializer.extend(novos)
    nodes = casts + list(m.graph.node)
    del m.graph.node[:]
    m.graph.node.extend(nodes)
    onnx.save(m, path, save_as_external_data=False)
    return len(casts)


def _embed_gather_int8(src, out):
    from onnxruntime.quantization import QuantType, quantize_dynamic
    quantize_dynamic(src, out, weight_type=QuantType.QInt8, per_channel=True, reduce_range=True,
                     op_types_to_quantize=["Gather"], use_external_data_format=False)


class _Calib:
    """CalibrationDataReader para o int8 estático: linhas serializadas das frases do app."""

    def __init__(self, feeds):
        self.it = iter(feeds)

    def get_next(self):
        return next(self.it, None)

    def rewind(self):
        pass


def quantize(fp32, out, mode, reduce_range=True, inter=None, exclude=None, calib=None, fp16_rest=None):
    """Gera `out` a partir do encoder fp32 no modo pedido (veja LADDER). `exclude`: MatMuls que ficam fora do
    int8 (modo sens). `calib`: entradas de calibração (modo static). `fp16_rest`: grava em fp16 os pesos que
    ficaram em fp32 (padrão: sim nos modos parciais)."""
    import onnx
    if mode == "none":
        onnx.save(onnx.load(fp32), out, save_as_external_data=False)
        return
    from onnxruntime.quantization import QuantType, quantize_dynamic
    m = onnx.load(fp32, load_external_data=False)
    shapes = {t.name: list(t.dims) for t in m.graph.initializer}
    excl = list(exclude or [])
    if fp16_rest is None:
        fp16_rest = mode in ("sens", "mlp", "attn", "embed")
    if mode == "all_fr":                       # int8 completo com faixa cheia (mais precisão)
        mode, reduce_range = "all", False
    if mode == "wo8":
        from onnxruntime.quantization.matmul_nbits_quantizer import MatMulNBitsQuantizer
        q = MatMulNBitsQuantizer(onnx.load(fp32), bits=8, block_size=32, is_symmetric=True, accuracy_level=0,
                                 op_types_to_quantize=("MatMul",), nodes_to_exclude=excl)
        q.process()
        tmp = out + ".wo8.onnx"
        onnx.save(q.model.model, tmp, save_as_external_data=False)
        try:
            _embed_gather_int8(tmp, out)       # vocabulário em int8 também (o Gather não aceita 8 bits por bloco)
        finally:
            os.remove(tmp)
        return
    if mode == "static":
        from onnxruntime.quantization import CalibrationMethod, QuantFormat, quantize_static
        pre = out + ".pre.onnx"
        onnx.save(onnx.load(fp32), pre, save_as_external_data=False)
        try:
            quantize_static(pre, out, _Calib(calib or []), quant_format=QuantFormat.QDQ, per_channel=True,
                            reduce_range=reduce_range, activation_type=QuantType.QInt8, weight_type=QuantType.QInt8,
                            op_types_to_quantize=["MatMul", "Gather"], nodes_to_exclude=excl,
                            calibrate_method=CalibrationMethod.MinMax, use_external_data_format=False,
                            extra_options={"ActivationSymmetric": True, "WeightSymmetric": True})
        finally:
            for f in (pre, pre + ".data"):
                if os.path.exists(f):
                    os.remove(f)
        return
    if mode in ("mlp", "embed", "attn"):
        for n in m.graph.node:
            if n.op_type != "MatMul":
                continue
            dims = next((shapes[i] for i in n.input if i in shapes), None)
            is_mlp = bool(dims and inter and inter in dims)
            if mode == "embed" or (mode == "mlp" and not is_mlp) or (mode == "attn" and is_mlp):
                excl.append(n.name)
    quantize_dynamic(fp32, out, weight_type=QuantType.QInt8, per_channel=True, reduce_range=reduce_range,
                     nodes_to_exclude=excl, use_external_data_format=False)
    if fp16_rest:
        fp16_storage(out)


FRASES_APP = [  # pedidos, mensagens e notificações como os do app (testes e exemplos), para paridade e calibração
    "O boleto vence amanhã e custa R$ 120,00.", "Reunião urgente com o cliente às 9h.",
    "Seu pedido saiu para entrega.", "Ganhe um iPhone agora, clique no link!", "Ana mora em Lima.",
    "Me lembra de ligar pro dentista sexta às 10h.", "Quanto é 15% de 230?", "Cria uma lista de compras.",
    "crie um card para pagar a conta de luz dia 10", "mande uma mensagem pra Maria dizendo que vou atrasar",
    "Sempre que chegar e-mail do banco, marque como importante.", "Seu código de verificação é 482913.",
    "Fatura do cartão fechada: R$ 2.340,17, vencimento 15/11.", "Oi, tudo bem? Podemos remarcar para quinta?",
    "Orçamento da reforma: piso R$ 4.800, mão de obra R$ 2.100.", "Sua encomenda foi entregue na portaria.",
    "O chefe pediu o relatório até amanhã cedo.", "Lembrete: consulta com o Dr. Paulo amanhã às 14h30.",
    "Promoção imperdível só hoje!!! 70% off", "anota aí: comprar pão, leite e café",
    "Quais são minhas tarefas de hoje?", "Pix recebido de João Silva: R$ 350,00.",
    "Atenção: tentativa de login no seu Gmail a partir de um novo dispositivo.",
    "Vamos almoçar sábado na casa da vó?", "Pagamento do aluguel atrasado há 3 dias.",
    "desliga o modo leve e abre as notificações", "Seu voo LA3456 para Recife foi alterado para 18h40.",
    "Não esquece de levar o documento do carro.", "Bom dia! Segue a nota fiscal em anexo.",
    "Toda segunda às 8h me manda o resumo da semana.",
]


def linhas_de_teste():
    from djorge.runtime.semantic import SemReq
    ops = ["cobrança", "entrega", "trabalho", "outro"]
    return ([SemReq("CHOICE", t, options=ops, ask="Do que se trata?") for t in FRASES_APP]
            + [SemReq("NOUL", t, options=("o texto fala de dinheiro",)) for t in FRASES_APP]
            + [SemReq("NOUL", t, options=("é um pedido para o app fazer algo agora",)) for t in FRASES_APP[::2]])


def _feeds(eng, reqs, lote=8):
    """Entradas do grafo (ids, roles, mask) a partir das linhas serializadas (calibração / sensibilidade)."""
    rows = [row for r in reqs for row in eng._rows(r)]
    out = []
    for i in range(0, len(rows), lote):
        ids, roles, mask = eng.model.collate(rows[i:i + lote], "cpu")
        f = lambda x: x.cpu().numpy() if hasattr(x, "cpu") else x
        ids, roles, mask = f(ids), f(roles), f(mask)
        out.append({"ids": ids.astype("int64"), "roles": roles.astype("int64"), "mask": mask.astype(bool)})
    return out


def _vmap(feeds, vmap):
    import numpy as np
    out = []
    for fd in feeds:
        x = fd["ids"].copy()
        pos = x >= 0
        x[pos] = vmap[x[pos]]
        out.append(dict(fd, ids=x))
    return out


def sensibilidade(fp32, feeds, n_blocos, reduce_range=True):
    """Erro de saída do encoder (média |ΔX|) quantizando UM bloco de camadas por vez: os mais sensíveis ficam
    fora do int8 no modo sens. Devolve [(erro, [nós])] do mais sensível para o menos."""
    import numpy as np
    import onnx
    import onnxruntime as ort
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from djorge.runtime.onnx_backend import session_options
    m = onnx.load(fp32, load_external_data=False)
    bl = blocos(m, n_blocos)
    todos = [n for b in bl for n in b]
    ref_s = ort.InferenceSession(fp32, session_options(), providers=["CPUExecutionProvider"])
    ref = [ref_s.run(["X"], f)[0] for f in feeds]
    del ref_s
    tmp = fp32 + ".sens.onnx"
    out = []
    for b in bl:
        quantize_dynamic(fp32, tmp, weight_type=QuantType.QInt8, per_channel=True, reduce_range=reduce_range,
                         nodes_to_exclude=[n for n in todos if n not in b], op_types_to_quantize=["MatMul"],
                         use_external_data_format=False)
        s = ort.InferenceSession(tmp, session_options(), providers=["CPUExecutionProvider"])
        err = float(np.mean([np.abs(s.run(["X"], f)[0] - r).mean() for f, r in zip(feeds, ref)]))
        del s
        out.append((err, b))
    if os.path.exists(tmp):
        os.remove(tmp)
    return sorted(out, key=lambda x: -x[0])


def converter(artifact, out, progresso=None, ladder=LADDER, cjk_top=6000, paridade=None):
    """Artefato PyTorch -> pacote Local ONNX (int8 quando possível) em `out`, o MENOR que passa a paridade.
    Levanta RuntimeError se nenhum degrau passar."""
    p = progresso or (lambda f, etapa: None)
    artifact, out = str(artifact), str(out)
    par = dict(PARIDADE, **(paridade or {}))
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
    p(0.5, "Conferindo as respostas com o modelo original…")
    ref = SemanticEngine.from_artifact(artifact, device="cpu")
    reqs = []
    for r in linhas_de_teste():
        try:
            ref._rows(r); reqs.append(r)
        except ValueError:
            pass
    r0 = ref.run(reqs)
    import numpy as np
    vm = os.path.join(out, "onnx", "vocab_map.npy")
    feeds = _feeds(ref, reqs)
    if os.path.exists(vm):
        feeds = _vmap(feeds, np.load(vm))
    del ref
    report = {"rows": len(reqs), "ladder": [], "paridade": par, "lora_folded": info.get("lora_folded", 0)}

    def measure():
        eng = SemanticEngine.from_artifact(out, device="cpu", onnx="int8")
        eng.run(reqs[:2])
        t = time.time(); r1 = eng.run(reqs); dt = (time.time() - t) / max(1, len(reqs))
        agree = sum(x.get("answer") == y.get("answer") for x, y in zip(r0, r1)) / max(1, len(reqs))
        dp = sum(abs(float(x.get("probability") or 0) - float(y.get("probability") or 0))
                 for x, y in zip(r0, r1)) / max(1, len(reqs))
        return {"agreement": round(agree, 4), "mean_abs_prob_diff": round(dp, 4), "ms_per_row_cpu": round(dt * 1000, 2)}

    passes = lambda m: m["agreement"] >= par["agreement"] and m["mean_abs_prob_diff"] < par["mean_abs_prob_diff"]
    keep_best = os.path.join(out, "onnx", "encoder.best.onnx")
    chosen = None
    flat = _flat(ladder)
    sens_rank = None
    for gi, grupo in enumerate(_grupos(ladder)):
        aprovados = []
        for mode in grupo:
            i = flat.index(mode)
            p(0.55 + 0.4 * i / max(1, len(flat)), f"Quantizando ({mode}) e conferindo…")
            tentativas = [(mode, None)]
            if mode == "sens":
                if sens_rank is None:
                    nb = int(info.get("layers") or 8) + 2
                    sens_rank = sensibilidade(fp32, feeds, nb)
                    report["sensibilidade"] = [round(e, 6) for e, _ in sens_rank]
                tentativas = []
                for k in sorted({1, 2, max(1, len(sens_rank) // 8), max(1, len(sens_rank) // 4),
                                 max(1, len(sens_rank) // 2)}):
                    if k < len(sens_rank):
                        tentativas.append((f"sens{k}", [n for _, b in sens_rank[:k] for n in b]))
            for nome, excl in tentativas:
                try:
                    quantize(fp32, q, "sens" if excl is not None else mode, reduce_range=True,
                             inter=info.get("intermediate"), exclude=excl, calib=feeds)
                    m = measure()
                except Exception as e:                     # degrau que não roda neste ONNX Runtime: pula
                    log.warning("quantização %s falhou: %s", nome, e)
                    report["ladder"].append({"mode": nome, "erro": str(e)[:300]})
                    continue
                m["mode"], m["mb"] = nome, round(os.path.getsize(q) / 1e6, 2)
                m["passou"] = passes(m)
                report["ladder"].append(m)
                log.info("quantização %s: %s", nome, m)
                if m["passou"]:
                    aprovados.append(m)
                    if not chosen or m["mb"] < chosen["mb"]:
                        chosen = m
                        shutil.copy(q, keep_best)
                    if excl is not None:
                        break                               # sens: o menor k aprovado é o menor pacote
        if aprovados:
            break
    if chosen:
        os.replace(keep_best, q)
    for f in ("encoder.onnx", "encoder.onnx.data", "encoder.best.onnx"):
        fp = os.path.join(out, "onnx", f)
        if os.path.exists(fp):
            os.remove(fp)
    info["files"].pop("fp32", None)
    if not chosen:
        shutil.rmtree(out, ignore_errors=True)
        raise RuntimeError("a versão ONNX mudaria as respostas do modelo (teste de paridade): seguindo em PyTorch")
    info["quant"] = chosen["mode"]
    report["variants"] = {"int8": chosen}
    report["chosen"] = chosen["mode"]
    report["passed"] = True
    json.dump(info, open(os.path.join(out, "onnx", "encoder.json"), "w"), indent=1)
    json.dump({"name": "Djorge Origo Flash Local", "profiles": PROFILES, "default": "cpu", "converted_locally": True},
              open(os.path.join(out, "local_profiles.json"), "w"), indent=1)
    mp = os.path.join(out, "metadata", "djorge.json")
    meta_o = json.load(open(mp, encoding="utf-8"))
    meta_o.update(local_package=True, onnx=info, parity=report, vocab_report=vrep,
                  quantization=descrever_quant(chosen["mode"]), converted=time.strftime("%Y-%m-%d"))
    json.dump(meta_o, open(mp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    report.update(size_mb=round(_size(out) / 1e6, 2), source_mb=round(before / 1e6, 1),
                  minutes=round((time.time() - t0) / 60, 1))
    return report


def descrever_quant(mode: str) -> str:
    m = str(mode or "")
    if m in ("all", "all_fr"):
        return "int8 completo (CPU)"
    if m == "wo8":
        return "int8 nos pesos (CPU)"
    if m == "static":
        return "int8 estático calibrado (CPU)"
    if m.startswith("sens"):
        return f"int8 menos {m[4:] or '?'} blocos sensíveis (fp16) (CPU)"
    if m in ("mlp", "attn"):
        return f"int8 só {'MLP' if m == 'mlp' else 'atenção'} + fp16 (CPU)"
    if m == "embed":
        return "int8 só vocabulário + fp16 (CPU)"
    return "fp32 (CPU)" if m == "none" else (m or "int8 (CPU)")


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
