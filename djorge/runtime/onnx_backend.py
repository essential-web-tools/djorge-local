"""ONNX encoder for local use (Origo Flash Local): the whole Qwen + adapter forward as one ONNX graph, run by
ONNX Runtime. The small decision heads run in numpy (`NumpyDecision`), so a local ONNX package needs NO PyTorch:
torch is imported only to EXPORT (convert an artifact), never to run.

Why: on ordinary CPUs ONNX Runtime with int8 weights is faster and uses far less memory than PyTorch fp32, and
the same graph runs on any GPU through DirectML (Windows), CoreML (macOS), CUDA, or the WebGPU provider.

    export_encoder(artifact, out_dir)          -> out_dir/onnx/encoder.onnx (fp32) + encoder.int8.onnx
    load_onnx_engine(artifact, onnx_file, providers)   (used by SemanticEngine.from_artifact when asked)

The export is validated against PyTorch on real rows before use (max |diff| and decision agreement): an export
that changes answers is never shipped.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

import numpy as np

PREFERRED = ["WebGpuExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider",
             "CUDAExecutionProvider", "CPUExecutionProvider"]


def _encoder_graph(m):
    """Export-friendly copy of DjorgeModel.encode (no boolean indexing). torch only here (export)."""
    import torch
    import torch.nn as nn

    class _EncoderGraph(nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, ids, roles, mask):
            m = self.m
            emb = m.backbone.get_input_embeddings()
            base = emb(ids.clamp(min=0))
            n_sp = m.special.shape[0]
            sp_idx = (-ids - 1).clamp(min=0, max=n_sp - 1)
            sp_vec = m.special[sp_idx].to(base.dtype)
            base = torch.where((ids < 0).unsqueeze(-1), sp_vec, base)
            out = m.backbone(inputs_embeds=base, attention_mask=mask.long(), output_hidden_states=True, use_cache=False)
            hs = out.hidden_states
            w = torch.softmax(m.mix, 0)
            h = sum(w[i] * hs[l].float() for i, l in enumerate(m.mix_layers))
            x = m.proj(h) + m.role(roles)
            x = m.adapter(x, src_key_padding_mask=~mask)
            return m.ln(x)
    return _EncoderGraph(m)


def trim_embeddings(model, keep_ids):
    """Keep only the token rows in keep_ids; every dropped token shares ONE fallback row (their mean), so any text can
    still be read. Returns the id map (old id -> new row) the runtime applies before the graph."""
    import torch
    import torch.nn as nn
    emb = model.backbone.get_input_embeddings()
    W = emb.weight.data.float()
    V = W.shape[0]
    keep = sorted({int(i) for i in keep_ids if 0 <= int(i) < V})
    mask = torch.ones(V, dtype=torch.bool)
    mask[keep] = False
    fb = W[mask].mean(0, keepdim=True) if bool(mask.any()) else torch.zeros(1, W.shape[1])
    new = nn.Embedding.from_pretrained(torch.cat([W[keep], fb], 0), freeze=True)
    model.backbone.set_input_embeddings(new)
    vmap = np.full(V, len(keep), dtype=np.int32)
    vmap[keep] = np.arange(len(keep))
    return vmap


def merge_lora(model) -> int:
    """Folds every ACTIVE LoRA adapter into its base Linear (W + B@A*scale, in float32) and removes the wrapper.
    Same function, but each projection is ONE MatMul in the graph instead of three: faster, smaller, and int8
    quantization no longer has to round the tiny low-rank matrices (where most of its error came from)."""
    import torch
    import torch.nn as nn
    from ..model.lora import LoRALinear
    n = 0
    with torch.no_grad():
        for parent in list(model.modules()):
            for name, child in list(parent.named_children()):
                if isinstance(child, LoRALinear):
                    base = child.base
                    lin = nn.Linear(base.in_features, base.out_features, bias=base.bias is not None)
                    W = base.weight.detach().float().clone()
                    for k in child.active:
                        if k in child.A:
                            W += (child.B[k].float() @ child.A[k].float()) * child.scale[k]
                    lin.weight.data.copy_(W)
                    if base.bias is not None:
                        lin.bias.data.copy_(base.bias.detach().float())
                    setattr(parent, name, lin)
                    n += 1
    return n


def export_encoder(artifact: str, out_dir: str, opset: int = 17, int8: bool = True, fp16: bool = False,
                   keep_ids=None, reduce_range: bool = True, fold_lora: bool = True) -> dict:
    import torch
    from .semantic import SemanticEngine
    eng = SemanticEngine.from_artifact(artifact, device="cpu", dtype="float32")
    model = eng.model.eval().float()
    folded = merge_lora(model.backbone) if fold_lora else 0
    os.makedirs(os.path.join(out_dir, "onnx"), exist_ok=True)
    vocab = None
    if keep_ids is not None:
        vmap = trim_embeddings(model, keep_ids)
        np.save(os.path.join(out_dir, "onnx", "vocab_map.npy"), vmap)
        vocab = {"total": int(len(vmap)), "kept": int(vmap.max())}
    g = _encoder_graph(model).eval()
    fp32 = os.path.join(out_dir, "onnx", "encoder.onnx")
    ids = torch.tensor([[11, 12, -1, 13, 14, 15, 16], [21, -2, 22, 23, 0, 0, 0]], dtype=torch.long)  # B=2, T=7:
    if vocab:
        ids = torch.where(ids >= 0, ids.clamp(max=vocab["kept"]), ids)
    roles = torch.tensor([[0, 0, 1, 1, 2, 2, 3], [0, 1, 1, 2, 0, 0, 0]], dtype=torch.long)      # sizes 1 would be
    mask = ids != 0                                                                             # specialised away
    try:
        torch.backends.mha.set_fastpath_enabled(False)
    except Exception:
        pass
    from torch.export import Dim
    B, T = Dim("B", min=1, max=64), Dim("T", min=2, max=4096)
    with torch.no_grad():
        prog = torch.onnx.export(g, (ids, roles, mask), input_names=["ids", "roles", "mask"], output_names=["X"],
                                 dynamic_shapes={"ids": {0: B, 1: T}, "roles": {0: B, 1: T}, "mask": {0: B, 1: T}},
                                 opset_version=max(opset, 18), dynamo=True)
        prog.optimize()
        prog.save(fp32, external_data=True)                 # weights beside the graph (> 2 GB protobuf limit)
    files = {"fp32": "onnx/encoder.onnx"}
    if int8:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        q = os.path.join(out_dir, "onnx", "encoder.int8.onnx")
        # per-channel int8 weights (MatMul + the embedding Gather); reduce_range = 7-bit weights, which avoids the
        # saturation of AVX2 CPUs without VNNI (ONNX Runtime docs)
        quantize_dynamic(fp32, q, weight_type=QuantType.QInt8, per_channel=True, reduce_range=reduce_range,
                         use_external_data_format=False)
        files["int8"] = "onnx/encoder.int8.onnx"
    if fp16:
        import onnx
        from onnxconverter_common import float16
        m16 = float16.convert_float_to_float16(onnx.load(fp32), keep_io_types=True, disable_shape_infer=True)
        onnx.save(m16, os.path.join(out_dir, "onnx", "encoder.fp16.onnx"), save_as_external_data=True,
                  location="encoder.fp16.onnx.data")
        files["fp16"] = "onnx/encoder.fp16.onnx"
    info = {"files": files, "hidden": model.cfg["hidden"], "layers": model.cfg["layers"], "d": model.cfg["d"],
            "opset": max(opset, 18), "reduce_range": bool(reduce_range),
            "intermediate": int(getattr(model.backbone.config, "intermediate_size", 0) or 0), "lora_folded": folded}
    if vocab:
        info["vocab"] = vocab
    json.dump(info, open(os.path.join(out_dir, "onnx", "encoder.json"), "w"), indent=1)
    return info


def session_options(threads: int = 0, arena: Optional[bool] = None):
    """Light ONNX Runtime session for a desktop app: graph optimisation ALL, intra-op = `threads` (0 = ORT
    default), ONE inter-op thread (the encoder graph is a chain: parallel branches gain nothing), optional
    no-spinning (DJORGE_ORT_SPIN=0: less idle CPU, ~10 % slower), and the memory arena off by default
    (DJORGE_ORT_ARENA=1 turns it back on): the arena keeps the largest batch's buffers forever."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.inter_op_num_threads = 1
    if threads:
        so.intra_op_num_threads = int(threads)
    if arena is None:
        arena = os.environ.get("DJORGE_ORT_ARENA", "0") == "1"
    so.enable_cpu_mem_arena = bool(arena)
    so.enable_mem_pattern = bool(arena)
    if os.environ.get("DJORGE_ORT_SPIN", "1") == "0":       # medido: sem spinning ~10 % mais lento por linha
        for k in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
            try:
                so.add_session_config_entry(k, "0")
            except Exception:
                pass
    return so


class OnnxEncoder:
    def __init__(self, path: str, providers: Optional[List[str]] = None, threads: int = 0, arena: Optional[bool] = None):
        import onnxruntime as ort
        avail = ort.get_available_providers()
        prov = [p for p in (providers or PREFERRED) if p in avail] or ["CPUExecutionProvider"]
        so = session_options(threads, arena)
        self.threads = threads
        self.sess = ort.InferenceSession(path, so, providers=prov)
        self.provider = self.sess.get_providers()[0]
        mp = os.path.join(os.path.dirname(path), "vocab_map.npy")      # trimmed vocabulary: old id -> kept row
        if os.path.exists(mp):
            self.vmap = np.load(mp)
        else:
            self.vmap = None

    def __call__(self, ids, roles, mask):
        """numpy in -> numpy out (torch-free path); torch tensors in -> torch tensor out (old heads)."""
        is_t = hasattr(ids, "cpu") and not isinstance(ids, np.ndarray)
        x = ids.cpu().numpy() if is_t else np.asarray(ids)
        r = roles.cpu().numpy() if is_t else np.asarray(roles)
        mk = mask.cpu().numpy() if is_t else np.asarray(mask)
        if self.vmap is not None:
            x = x.copy()
            pos = x >= 0
            x[pos] = self.vmap[x[pos]]
        out = self.sess.run(["X"], {"ids": x.astype(np.int64), "roles": r.astype(np.int64), "mask": mk.astype(bool)})[0]
        if is_t:
            import torch
            return torch.from_numpy(out)
        return out


def _stub_backbone(hidden: int, layers: int):
    """Stands in for the Qwen backbone when the ONNX encoder does the forward (old torch heads)."""
    import torch.nn as nn
    from types import SimpleNamespace

    class _StubBackbone(nn.Module):
        def __init__(self):
            super().__init__()
            self.config = SimpleNamespace(hidden_size=hidden, num_hidden_layers=layers)
            self.emb = nn.Embedding(2, hidden)
            self.layers = nn.ModuleList()

        def get_input_embeddings(self):
            return self.emb
    return _StubBackbone()


def onnx_file_for(path: str, prefer: str = "int8") -> Optional[str]:
    info_p = os.path.join(path, "onnx", "encoder.json")
    if not os.path.exists(info_p):
        return None
    files = json.load(open(info_p, encoding="utf-8"))["files"]
    for k in (prefer, "int8", "fp16", "fp32"):
        if k in files and os.path.exists(os.path.join(path, files[k])):
            return os.path.join(path, files[k])
    return None


def build_model_without_backbone(path: str, meta: dict):
    """DjorgeModel with a stub backbone (no Qwen weights in memory) + its trained decision parts."""
    from ..model.djorge_model import DjorgeModel
    info = json.load(open(os.path.join(path, "onnx", "encoder.json"), encoding="utf-8"))
    mc = meta["model_config"]
    model = DjorgeModel(_stub_backbone(info["hidden"], info["layers"]), d=mc["d"], adapter_layers=mc["adapter_layers"],
                        heads=mc["heads"], lora_r=mc["lora_r"], lora_alpha=mc["lora_alpha"], use_lora=False,
                        lora_targets=mc.get("lora_targets"), rslora=mc.get("rslora", False))
    from safetensors.torch import load_file
    sd = {k: v for k, v in load_file(os.path.join(path, "decision", "core.safetensors")).items()
          if not k.startswith("backbone.")}
    model.load_state_dict(sd, strict=False)
    return model.eval().float()


# ------------------------------------------------------------------ torch-free decision heads (numpy) --
def _erf(x):
    """erf with |error| < 1.2e-7 (Numerical Recipes erfc), vectorised; numpy has no erf of its own."""
    z = np.abs(x)
    t = 1.0 / (1.0 + 0.5 * z)
    r = t * np.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (
        -0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))))
    return np.where(x >= 0, 1.0 - r, r - 1.0)


def _gelu(x):
    return 0.5 * x * (1.0 + _erf(x / np.sqrt(2.0)))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


class _NpParam:
    def __init__(self, n):
        self._n = int(n)

    def numel(self):
        return self._n


class _NpBackbone:
    """What identity.describe() and the engine read from a backbone; no weights (they live in the ONNX graph)."""

    def __init__(self, info: dict, meta: dict):
        from types import SimpleNamespace
        tc = meta.get("tiny_config") or {}
        self.config = SimpleNamespace(hidden_size=info.get("hidden"), num_hidden_layers=info.get("layers"),
                                      num_attention_heads=tc.get("heads") or meta.get("heads") or 0,
                                      vocab_size=(info.get("vocab") or {}).get("total") or meta.get("vocab_size") or 0)
        self.layers = []

    def parameters(self):
        return iter(())

    def named_buffers(self):
        return iter(())


class NumpyDecision:
    """DjorgeModel's readouts (token/slot/binary/ordinal heads) in numpy + the ONNX encoder. Same maths as
    djorge/model/djorge_model.py, float32 weights, float64 arithmetic for the small heads."""
    is_numpy = True

    def __init__(self, weights: dict, encoder, info: dict, meta: dict):
        self.w = {k: np.asarray(v, dtype=np.float64) for k, v in weights.items()}
        self.encode = encoder
        self.backbone = _NpBackbone(info, meta)
        self.cfg = dict(meta.get("model_config") or {})

    # torch-module look-alikes used by SemanticEngine
    def to(self, *_a, **_k):
        return self

    def eval(self):
        return self

    def float(self):
        return self

    def named_parameters(self):
        return ((k, _NpParam(v.size)) for k, v in self.w.items())

    def _lin(self, name, x):
        return x @ self.w[name + ".weight"].T + self.w[name + ".bias"]

    def _mlp(self, name, x):
        return self._lin(name + ".2", _gelu(self._lin(name + ".0", x)))

    @staticmethod
    def collate(rows, device=None):
        T = max(len(r.ids) for r in rows)
        ids = np.zeros((len(rows), T), dtype=np.int64)
        roles = np.zeros((len(rows), T), dtype=np.int64)
        mask = np.zeros((len(rows), T), dtype=bool)
        for i, r in enumerate(rows):
            n = len(r.ids)
            ids[i, :n] = r.ids
            roles[i, :n] = r.roles
            mask[i, :n] = True
        return ids, roles, mask

    def token_logits(self, X, rows):
        out = []
        for i, r in enumerate(rows):
            e = X[i, r.end_idx].astype(np.float64)
            s = X[i, list(r.src_tok_idx)].astype(np.float64).reshape(-1, X.shape[-1])
            ee = np.broadcast_to(e, s.shape)
            out.append(self._mlp("tok_head", np.concatenate([s, ee, s * ee], -1)))
        return out

    def option_vectors(self, X, i, r):
        return np.stack([X[i, a:b].astype(np.float64).mean(0) for a, b in r.opt_spans])

    def slot_logits(self, X, i, r):
        o = self.option_vectors(X, i, r)
        e = np.broadcast_to(X[i, r.end_idx].astype(np.float64), o.shape)
        return self._mlp("slot_head", np.concatenate([o, e], -1))[:, 0]

    def binary_logit(self, X, i, r):
        return float(self._lin("bin_head", X[i, r.end_idx].astype(np.float64))[0])

    def ordinal_logprobs(self, X, i, r):
        o = self.option_vectors(X, i, r)
        e = X[i, r.end_idx].astype(np.float64)
        eta = self._lin("ord_eta", e)
        b0 = self._lin("ord_b0", o[0])
        if o.shape[0] > 2:
            z = self._mlp("ord_delta", np.concatenate([o[1:-1], o[2:]], -1))[:, 0]
            delta = np.logaddexp(0.0, z) + 1e-3
            b = np.concatenate([b0, b0 + np.cumsum(delta)])
        else:
            b = b0
        gt = _sigmoid(eta - b)
        cum = np.concatenate([[1.0], gt, [0.0]])
        p = np.clip(cum[:-1] - cum[1:], 1e-6, None)
        return np.log(p / p.sum())


_ST_DT = {"F64": np.float64, "F32": np.float32, "F16": np.float16, "I64": np.int64, "I32": np.int32,
          "I16": np.int16, "I8": np.int8, "U8": np.uint8, "BOOL": np.bool_}


def read_safetensors_numpy(fname: str) -> dict:
    """safetensors -> numpy without torch, including BF16 (numpy has no bfloat16: widened to float32)."""
    import struct
    with open(fname, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        head = json.loads(f.read(n))
        buf = f.read()
    out = {}
    for k, v in head.items():
        if k == "__metadata__":
            continue
        a, b = v["data_offsets"]
        raw = buf[a:b]
        if v["dtype"] == "BF16":
            arr = (np.frombuffer(raw, dtype=np.uint16).astype(np.uint32) << 16).view(np.float32)
        else:
            arr = np.frombuffer(raw, dtype=_ST_DT[v["dtype"]])
        out[k] = arr.reshape(v["shape"]).copy()
    return out


def load_numpy_model(path: str, meta: dict, encoder) -> NumpyDecision:
    """Decision heads from decision/core.safetensors read with safetensors.numpy (no torch)."""
    info = json.load(open(os.path.join(path, "onnx", "encoder.json"), encoding="utf-8"))
    sd = {k: v for k, v in read_safetensors_numpy(os.path.join(path, "decision", "core.safetensors")).items()
          if not k.startswith("backbone.")}
    return NumpyDecision(sd, encoder, info, meta)


def load_tokenizer(path: str):
    """Fast tokenizer of the package (transformers works without torch)."""
    tdir = next((os.path.join(path, d) for d in ("tokenizer", "backbone") if
                 os.path.exists(os.path.join(path, d, "tokenizer_config.json"))), None)
    try:
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained(tdir)
    except Exception:
        from transformers import PreTrainedTokenizerFast
        return PreTrainedTokenizerFast.from_pretrained(tdir)
