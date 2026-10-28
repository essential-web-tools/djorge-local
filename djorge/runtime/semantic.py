"""Semantic engine: the production inference path through the Qwen-derived model.

Responsibilities: serialize requests, batch rows, ONE forward per batch chunk,
read out spans / decisions, apply calibration, report provenance + timings.
Model weights are loaded once per process; nothing about a request persists
after it returns (no conversational state, no persistent KV cache).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import torch

from ..errors import DjorgeError
from ..model import lora
from ..model.serialize import Serializer
from ..model.spans import decode
from ..model.djorge_model import DjorgeModel
from .calibration import CalibrationTable, sigmoid, softmax
from .result import Match, MatchSet, choice_confidence, norm_entropy, score_confidence
from .structure import negation_in_scope, proper_noun_guard, quantity_before


@dataclass
class SemReq:
    kind: str                      # resolve | answer | CHOICE | SCORE | NOUL | RANK | SET
    source: str
    mode: Optional[str] = None     # CLASS | ENTITY
    target: str = ""
    definition: Optional[str] = None
    options: Sequence[str] = ()
    descriptions: Sequence[str] = ()
    ask: str = ""
    threshold: float = 0.5


class SemanticEngine:
    def __init__(self, model: DjorgeModel, tokenizer, calibration: Optional[CalibrationTable] = None,
                 metadata: Optional[dict] = None, device: str = "cpu", max_tokens: int = 512, stride: int = 384,
                 batch_size: int = 16, prune: float = 0.05, option_permutations: int = 1):
        self.model = model.to(device).eval()
        self.tok = tokenizer
        self.cal = calibration or CalibrationTable()
        self.meta = metadata or {}
        self.device = device
        self.ser = Serializer(tokenizer, max_tokens, stride)
        self.batch_size, self.prune, self.perms = batch_size, prune, option_permutations
        self.model_id = self.meta.get("model_id", "djorge-qwen")
        self.lock = threading.Lock()
        self.stats = {"forward_calls": 0, "rows": 0, "tokens": 0}

    # ---------------------------------------------------------------- loading
    @classmethod
    def from_artifact(cls, path: str, device: str = "auto", dtype: str = "auto", quant: str = None, **kw) -> "SemanticEngine":
        from ..model.backbone import load_backbone, pick_device, tiny_qwen3
        meta = json.load(open(os.path.join(path, "metadata", "djorge.json")))
        mc = meta["model_config"]
        want_onnx = kw.pop("onnx", None) or os.environ.get("DJORGE_ONNX", "")
        if want_onnx and want_onnx != "0":                  # ONNX Runtime encoder (local package)
            from .onnx_backend import OnnxEncoder, build_model_without_backbone, onnx_file_for
            f = onnx_file_for(path, "fp16" if want_onnx == "fp16" else ("fp32" if want_onnx == "fp32" else "int8"))
            if f:
                from transformers import AutoTokenizer
                tdir = next((os.path.join(path, d) for d in ("tokenizer", "backbone") if
                             os.path.exists(os.path.join(path, d, "tokenizer_config.json"))), None)
                tok = AutoTokenizer.from_pretrained(tdir)
                model = build_model_without_backbone(path, meta)
                prov = [p for p in os.environ.get("DJORGE_ONNX_PROVIDERS", "").split(",") if p] or None
                enc = OnnxEncoder(f, prov, int(os.environ.get("DJORGE_THREADS", "0") or 0))
                model.encode = enc
                meta["onnx"] = {"file": os.path.relpath(f, path), "provider": enc.provider}
                for k in ("linear_int8", "prune_vocab", "embed_int8"):
                    kw.pop(k, None)
                ctp = os.path.join(path, "calibration", "certainty.json")
                if os.path.exists(ctp):
                    meta["certainty"] = json.load(open(ctp))
                cp = os.path.join(path, "calibration", "calibration.json")
                cal = CalibrationTable.from_json(open(cp).read()) if os.path.exists(cp) else CalibrationTable()
                eng = cls(model, tok, cal, meta, device="cpu", max_tokens=meta.get("max_tokens", 512), **kw)
                eng.artifact_path = path
                from .refcal import Conformal
                eng.conformal = Conformal.load(path)
                return eng
        device = pick_device(device)
        if meta.get("backbone_kind") == "tiny":
            from transformers import PreTrainedTokenizerFast
            bb = tiny_qwen3(**meta["tiny_config"])
            if meta.get("keep_layers"):
                from ..model.backbone import truncate_layers
                truncate_layers(bb, meta["keep_layers"])
            own = os.path.join(path, "backbone", "model.safetensors")
            if meta.get("standalone") and os.path.exists(own):     # tiny model trained with --stage full
                from safetensors.torch import load_file
                if meta.get("tiny_keep_layers"):
                    from ..model.backbone import truncate_layers as _trunc
                    _trunc(bb, meta["tiny_keep_layers"])
                bb.load_state_dict({k: v.float() for k, v in load_file(own).items()}, strict=True)
            tok = PreTrainedTokenizerFast.from_pretrained(os.path.join(path, "tokenizer"))
        else:
            src = meta["backbone"]
            if os.path.isdir(os.path.join(path, src)):          # serving bundle ships its own (truncated) backbone
                src = os.path.join(path, src)
            bb, tok, _ = load_backbone(src, device=device, dtype=dtype, revision=meta.get("revision"),
                                       keep_layers=meta.get("keep_layers"), quant=quant)
        model = DjorgeModel(bb, d=mc["d"], adapter_layers=mc["adapter_layers"], heads=mc["heads"],
                          lora_r=mc["lora_r"], lora_alpha=mc["lora_alpha"], use_lora=mc["use_lora"],
                          lora_targets=mc.get("lora_targets"), rslora=mc.get("rslora", False))
        model.load_parts(path)
        _li8, _pv, _ei8 = kw.pop("linear_int8", False), kw.pop("prune_vocab", False), kw.pop("embed_int8", False)
        if (os.environ.get("DJORGE_LINEAR_INT8", "0") == "1" or _li8) and device == "cpu":
            # CPU serving: int8 weights for every Linear of the backbone (dynamic activation quantization).
            # ~4x less weight memory vs fp32 and faster matmuls on AVX2/AVX512-VNNI and ARM dot-product CPUs.
            import torch.nn as _nn
            model.backbone.float()
            # per-CHANNEL weight scales (one per output row): far smaller error than one scale per matrix, which hurt
            # the fully-trained Origo layers. DJORGE_INT8_KEEP_TOP=N keeps the N top layers in fp32 (most sensitive).
            from torch.ao.quantization import per_channel_dynamic_qconfig
            keep = int(os.environ.get("DJORGE_INT8_KEEP_TOP", "0") or 0)
            layers = list(model.backbone.layers)
            targets = layers[:len(layers) - keep] if keep > 0 else layers
            scope = os.environ.get("DJORGE_INT8_SCOPE", "all")       # all | mlp (attention stays fp32: less error)
            for lyr in targets:
                part = getattr(lyr, "mlp", lyr) if scope == "mlp" else lyr
                torch.ao.quantization.quantize_dynamic(part, {_nn.Linear: per_channel_dynamic_qconfig}, inplace=True)
            meta["linear_int8"] = {"per_channel": True, "scope": scope, "fp32_top_layers": keep}
        kp = os.path.join(path, "metadata", "vocab_keep.json")
        if (os.environ.get("DJORGE_PRUNE_VOCAB", "0") == "1" or _pv) and os.path.exists(kp):
            # drop token rows the model never meets (see tools/prune_vocab.py); lossless on texts made of kept tokens
            from ..model.compress import prune_vocab
            meta["vocab_pruning"] = prune_vocab(model.backbone, json.load(open(kp))["keep"])
        if os.environ.get("DJORGE_EMBED_INT8", "0") == "1" or _ei8:
            from ..model.compress import compress_embeddings
            meta["compression"] = compress_embeddings(model.backbone)
        ctp = os.path.join(path, "calibration", "certainty.json")
        if os.path.exists(ctp):
            meta["certainty"] = json.load(open(ctp))
        cp = os.path.join(path, "calibration", "calibration.json")
        cal = CalibrationTable.from_json(open(cp).read()) if os.path.exists(cp) else CalibrationTable()
        eng = cls(model, tok, cal, meta, device=device, max_tokens=meta.get("max_tokens", 512), **kw)
        eng.artifact_path = path
        from .refcal import Conformal
        eng.conformal = Conformal.load(path)
        return eng

    def ensure_adapter(self, spec: dict):
        """Load a tenant adapter once (by name). spec = {name, path, alpha_over_r}."""
        if spec["name"] in getattr(self, "_loaded_adapters", set()):
            return
        from safetensors.torch import load_file
        with self.lock:
            lora.load_adapter(self.model.backbone, spec["name"], load_file(spec["path"]), spec["alpha_over_r"])
            self._loaded_adapters = getattr(self, "_loaded_adapters", set()) | {spec["name"]}

    # ------------------------------------------------------------- execution
    def _rows(self, r: SemReq):
        if r.kind == "resolve":
            return self.ser.rows("RESOLVE", r.source, mode=r.mode, target=r.target, definition=r.definition)
        if r.kind == "answer":
            return self.ser.rows("ANSWER", r.source, ask=r.target)
        texts = [f"{o}: {d}" if d else o for o, d in zip(r.options, r.descriptions or [""] * len(r.options))]
        if r.kind == "NOUL":
            return self.ser.rows("NOUL", r.source, ask=r.options[0])
        return self.ser.rows(r.kind, r.source, ask=r.ask, options=texts)

    @torch.inference_mode()
    def run(self, reqs: List[SemReq], tenant_adapter: Optional[str] = None, timings: Optional[dict] = None):
        t = timings if timings is not None else {}
        t0 = time.perf_counter()
        plan = []                                          # (req_idx, perm, row)
        for qi, r in enumerate(reqs):
            perms = [list(range(len(r.options)))]
            if r.kind in ("CHOICE", "SCORE", "RANK", "SET") and self.perms > 1 and r.kind != "SCORE":
                k = len(r.options)
                perms = [[(i + s) % k for i in range(k)] for s in range(min(self.perms, k))]
            for perm in perms:
                rr = r if perm == perms[0] and perm == sorted(perm) else SemReq(
                    r.kind, r.source, options=[r.options[i] for i in perm],
                    descriptions=[(r.descriptions or [""] * len(r.options))[i] for i in perm], ask=r.ask)
                for row in self._rows(rr):
                    plan.append((qi, perm, row))
        t["tokenize_ms"] = t.get("tokenize_ms", 0) + (time.perf_counter() - t0) * 1e3
        outs = [None] * len(plan)
        t1 = time.perf_counter()
        with self.lock:
            names = ["djorge"] + ([tenant_adapter] if tenant_adapter else [])
            lora.set_active(self.model.backbone, names)
            try:
                order = sorted(range(len(plan)), key=lambda i: len(plan[i][2].ids))   # length bucketing
                for c in range(0, len(order), self.batch_size):
                    idx = order[c:c + self.batch_size]
                    rows = [plan[i][2] for i in idx]
                    ids, roles, mask = self.model.collate(rows, self.device)
                    X = self.model.encode(ids, roles, mask)
                    self.stats["forward_calls"] += 1
                    self.stats["rows"] += len(rows)
                    self.stats["tokens"] += int(mask.sum())
                    tl = self.model.token_logits(X, rows)
                    for j, i in enumerate(idx):
                        outs[i] = self._readout(X, j, rows[j], tl[j], reqs[plan[i][0]].kind)
            finally:
                lora.set_active(self.model.backbone, ["djorge"])
        t["forward_ms"] = t.get("forward_ms", 0) + (time.perf_counter() - t1) * 1e3
        t2 = time.perf_counter()
        results = [self._finish(qi, r, [(plan[i][1], plan[i][2], outs[i]) for i in range(len(plan)) if plan[i][0] == qi])
                   for qi, r in enumerate(reqs)]
        t["decision_ms"] = t.get("decision_ms", 0) + (time.perf_counter() - t2) * 1e3
        return results

    def _readout(self, X, j, row, tl, kind):
        m = self.model
        if kind in ("resolve", "answer"):
            return tl.float().cpu().numpy()
        if kind == "NOUL":
            return float(m.binary_logit(X, j, row))
        if kind == "SCORE":
            return m.ordinal_logprobs(X, j, row).float().cpu().numpy()
        return m.slot_logits(X, j, row).float().cpu().numpy()

    def _finish(self, qi, r: SemReq, parts):
        if r.kind in ("resolve", "answer"):
            return self._spans(r, parts)
        k = len(r.options)
        cal = self.cal
        if r.kind == "NOUL":
            z = float(np.mean([o for _, _, o in parts])) if len(parts) == 1 else \
                max((o for _, _, o in parts), key=lambda z: abs(z))   # windowed: most decisive window
            p = cal.get("NOUL", 2).transform_binary(z)
            ans = p >= 0.5
            return {"type": "distribution", "decision": "NOUL", "answer": ans, "p_true": round(p, 6),
                    "probability": round(p if ans else 1 - p, 6), "raw_score": round(z, 6),
                    "raw_probability": round(sigmoid(z) if ans else 1 - sigmoid(z), 6),
                    "confidence": round(abs(2 * p - 1), 6)}
        # unpermute and average over option permutations; windowed -> most confident window
        per_window: Dict = {}
        for perm, row, z in parts:
            un = np.empty(k)
            un[perm] = z
            per_window.setdefault(row.window, []).append(un)
        zs = [np.mean(v, 0) for v in per_window.values()]
        if r.kind == "SCORE":
            cand = [cal.get("SCORE", k).transform_logits(z) for z in zs]
            i = int(np.argmax([score_confidence(p) for p in cand]))
            p, raw = cand[i], softmax(zs[i])
            a = int(np.argmax(p))
            return {"type": "ordinal", "decision": "SCORE", "answer": a, "label": r.options[a],
                    "expected": round(float(sum(i * x for i, x in enumerate(p))), 4),
                    "distribution": {o: round(float(x), 6) for o, x in zip(r.options, p)},
                    "raw_distribution": {o: round(float(x), 6) for o, x in zip(r.options, raw)},
                    "probability": round(float(p[a]), 6), "confidence": round(score_confidence(p), 6),
                    "uncertainty": round(norm_entropy(p), 6)}
        if r.kind == "SET":
            z = np.max(np.stack(zs), 0)
            c = cal.get("SET", 2)
            p = [c.transform_binary(float(v)) for v in z]
            sel = [o for o, x in zip(r.options, p) if x >= 0.5]
            return {"type": "set", "decision": "SET", "selected": sel,
                    "probabilities": {o: round(x, 6) for o, x in zip(r.options, p)},
                    "probability": round(float(np.prod([x if x >= .5 else 1 - x for x in p])), 6)}
        if r.kind == "RANK":
            z = zs[0] / getattr(cal.get("RANK", k), "t", 1.0)
            order = list(np.argsort(-z, kind="stable"))
            w = np.exp(z - z.max())
            pf = w / w.sum()
            p_order, rem = 1.0, list(order)
            for i in order:
                p_order *= w[i] / sum(w[j] for j in rem)
                rem.remove(i)
            return {"type": "ranking", "decision": "RANK", "ranking": [r.options[i] for i in order],
                    "p_first": {r.options[i]: round(float(pf[i]), 6) for i in order},
                    "probability": round(float(p_order), 6), "uncertainty": round(norm_entropy(pf), 6)}
        cand = [cal.get("CHOICE", k).transform_logits(z) for z in zs]
        i = int(np.argmax([choice_confidence(p) for p in cand]))
        p, raw = cand[i], softmax(zs[i])
        a = int(np.argmax(p))
        out = {"type": "distribution", "decision": "CHOICE", "answer": r.options[a],
               "distribution": {o: round(float(x), 6) for o, x in zip(r.options, p)},
               "raw_distribution": {o: round(float(x), 6) for o, x in zip(r.options, raw)},
               "probability": round(float(p[a]), 6), "raw_probability": round(float(raw[a]), 6),
               "confidence": round(choice_confidence(p), 6), "uncertainty": round(norm_entropy(p), 6)}
        conf = getattr(self, "conformal", None)
        if conf is not None and conf.table:                 # calibrated set with coverage guarantee (refcal.py)
            ps = conf.prediction_set("CHOICE", out["distribution"])
            if ps is not None:
                out["prediction_set"] = ps
        return out

    def _spans(self, r: SemReq, parts) -> MatchSet:
        best: Dict[tuple, float] = {}
        pol: Dict[tuple, tuple] = {}
        for _, row, tl in parts:
            for s, e, z in decode(r.source, row.src_offsets, tl[:, 0].tolist(), tl[:, 1].tolist(), self.prune):
                if z > best.get((s, e), -1e9):
                    best[(s, e)] = z
                    idx = [i for i, (a, b) in enumerate(row.src_offsets) if b > a and a < e and s < b]
                    pol[(s, e)] = (float(np.mean([sigmoid(float(tl[i, 2])) for i in idx])) if idx else 0.0,
                                   float(np.mean([sigmoid(float(tl[i, 3])) for i in idx])) if idx else 0.0)
        spans = sorted(best.items())
        kept = []                                   # resolve window overlaps: keep higher score
        for (s, e), z in spans:
            if kept and s < kept[-1][1]:
                if z > kept[-1][2]:
                    kept[-1] = (s, e, z)
                continue
            kept.append((s, e, z))
        mode = r.mode or "ANSWER"
        c = self.cal.get(mode, 2)
        acc, rej = [], []
        for s, e, z in kept:
            p = c.transform_binary(z)
            if p < self.prune:
                continue
            attrs = {}
            if mode != "ANSWER":
                p_neg, p_nf = pol.get((s, e), (0.0, 0.0))
                label = "negated" if p_neg >= 0.5 and p_neg >= p_nf else ("nonfactual" if p_nf >= 0.5 else "asserted")
                scope_guard = False
                if label == "negated" and not negation_in_scope(r.source, s):
                    label, p_neg, scope_guard = "asserted", 0.0, True     # negation cannot cross a clause boundary
                q = quantity_before(r.source, s)
                attrs = {"polarity": label, "p_asserted": round(max(0.0, 1 - max(p_neg, p_nf)), 6),
                         "p_negated": round(p_neg, 6), "p_nonfactual": round(p_nf, 6),
                         "quantity": q if q is not None else 1.0, "quantity_explicit": q is not None}
                if scope_guard:
                    attrs["polarity_rule"] = "negation_scope_guard"
            if mode == "CLASS" and proper_noun_guard(r.source, s, e, r.target):
                p = p * 0.5                                  # brand/name use of a common word ("A Apple anunciou")
                attrs["proper_noun_guard"] = True
            m = Match(r.source[s:e], s, e, p, "semantic", mode, r.target, raw_score=round(z, 6), attributes=attrs)
            (acc if p >= r.threshold else rej).append(m)
        return MatchSet(mode, r.target, "semantic", acc, rej, threshold=r.threshold, model=self.model_id)
