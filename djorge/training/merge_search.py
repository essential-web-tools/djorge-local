"""Layer-wise merge search (DOFL): the best mix of several trained versions, chosen per GROUP OF LAYERS and per
BLOCK (attention, MLP, embeddings, decision heads), measured on the "escolha" part only (never on the checks).

    python -m djorge.training.merge_search --sources FLASH,FL1,FL_K,FL_REF1,FL_REF2 --escolha escolha.jsonl \
        --out MERGED [--trials 48] [--bands 3]

The first source is the ANCHOR (the model being replaced): the objective rewards the mean score per group and
penalises every group that falls below the anchor. Multi-fidelity: every candidate is screened on a few records per
group, the best quarter on more, the best three on the full escolha budget (Su & Geiping 2025, SMAC-style budgets).
Candidates: each source alone, the uniform mix, Dirichlet samples per block, then mutations of the best.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import time

import numpy as np
import torch
from safetensors.torch import load_file, save_file

from .selfreg import group_scores, load_groups

FILES = ("backbone/model.safetensors", "decision/core.safetensors")


def block_of(name: str, n_layers: int, bands: int, file: str) -> str:
    if file.startswith("decision"):
        return "heads"
    m = re.search(r"layers\.(\d+)\.", name)
    if not m:
        return "emb"                                       # embeddings + final norm
    band = min(bands - 1, int(m.group(1)) * bands // max(1, n_layers))
    kind = "mlp" if ".mlp." in name else "attn"            # attention + its layer norms
    return f"{kind}{band}"


class Merger:
    def __init__(self, sources, dev, bands=3):
        self.sources, self.dev = sources, dev
        self.sd = {f: [] for f in FILES}
        for s in sources:
            for f in FILES:
                self.sd[f].append({k: v.to(dev, torch.float16 if v.is_floating_point() else v.dtype)
                                   for k, v in load_file(os.path.join(s, f)).items()})
        keys = {f: set(self.sd[f][0]) for f in FILES}
        for f in FILES:
            for i, d in enumerate(self.sd[f][1:], 1):
                if set(d) != keys[f]:
                    raise ValueError(f"{sources[i]}: {f} has different tensors than {sources[0]}")
        n_layers = 1 + max((int(m.group(1)) for k in keys[FILES[0]] for m in [re.search(r"layers\.(\d+)\.", k)] if m),
                           default=0)
        self.block = {f: {k: block_of(k, n_layers, bands, f) for k in keys[f]} for f in FILES}
        self.blocks = sorted({b for f in FILES for b in self.block[f].values()})

    def merged(self, W):
        """W: {block: [weight per source]} -> {file: state_dict} (fp32 for the heads, fp16 for the backbone)."""
        out = {}
        for f in FILES:
            sd = {}
            for k, b in self.block[f].items():
                t0 = self.sd[f][0][k]
                if not t0.is_floating_point():
                    sd[k] = t0; continue
                w = W[b]
                acc = torch.zeros_like(t0, dtype=torch.float32)
                for j, wj in enumerate(w):
                    if wj:
                        acc.add_(self.sd[f][j][k].float(), alpha=float(wj))
                sd[k] = acc
            out[f] = sd
        return out


def _onehot(blocks, S, j):
    return {b: [1.0 if i == j else 0.0 for i in range(S)] for b in blocks}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", required=True, help="comma list; the FIRST is the anchor (model being replaced)")
    ap.add_argument("--escolha", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trials", type=int, default=48)
    ap.add_argument("--bands", type=int, default=3)
    ap.add_argument("--budgets", default="16,48,120", help="records per group at each fidelity")
    ap.add_argument("--penalty", type=float, default=2.0, help="weight of every point below the anchor")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    from ..model.backbone import pick_device
    from ..model.serialize import Serializer
    from ..runtime.semantic import SemanticEngine
    from .trainer import _fits
    rng, nrng = random.Random(a.seed), np.random.default_rng(a.seed)
    dev = pick_device(a.device)
    srcs = [s.strip() for s in a.sources.split(",") if s.strip() and os.path.isdir(s.strip())]
    assert srcs, "no source found"
    S = len(srcs)
    print(f"sources ({S}): " + ", ".join(os.path.basename(s.rstrip('/')) for s in srcs), flush=True)
    eng = SemanticEngine.from_artifact(srcs[0], device=dev)
    model = eng.model.eval()
    ser = Serializer(eng.tok, eng.max_tokens if hasattr(eng, "max_tokens") else 384)
    budgets = [int(x) for x in a.budgets.split(",")]
    full = load_groups(a.escolha, budgets[-1], lambda r: _fits(ser, r), seed=11)
    sub = lambda n: {g: v[:n] for g, v in full.items()}
    print("escolha groups:", {g: len(v) for g, v in full.items()}, flush=True)
    M = Merger(srcs, dev if dev.startswith("cuda") else "cpu", a.bands)
    print("blocks:", M.blocks, flush=True)
    bb_dtype = next(model.backbone.parameters()).dtype

    def score(W, n):
        sds = M.merged(W)
        model.backbone.load_state_dict({k: v.to(bb_dtype) for k, v in sds[FILES[0]].items()}, strict=False)
        model.load_state_dict({k: v for k, v in sds[FILES[1]].items()}, strict=False)
        return group_scores(eng, sub(n))

    anchor = {n: score(_onehot(M.blocks, S, 0), n) for n in budgets}

    def objective(sc, n):
        an = anchor[n]
        drops = sum(max(0.0, an.get(g, 0) - v) for g, v in sc.items())
        return float(np.mean(list(sc.values()))) - a.penalty * drops / max(1, len(sc))

    cands = [_onehot(M.blocks, S, j) for j in range(S)]
    cands.append({b: [1.0 / S] * S for b in M.blocks})
    while len(cands) < a.trials:
        cands.append({b: [float(x) for x in nrng.dirichlet([0.7] * S)] for b in M.blocks})
    t0 = time.time()
    res = []
    for i, W in enumerate(cands):
        sc = score(W, budgets[0])
        res.append((objective(sc, budgets[0]), i))
        if i % 8 == 0:
            print(f"  triagem {i + 1}/{len(cands)} · {time.time() - t0:.0f}s · melhor {max(res)[0]:.4f}", flush=True)
    res.sort(reverse=True)
    top = [cands[i] for _, i in res[:max(4, len(res) // 4)]]
    for _ in range(max(4, len(top) // 2)):                 # mutations of the best: move weight between sources
        W = {b: list(v) for b, v in rng.choice(top[:4]).items()}
        b = rng.choice(M.blocks)
        w = np.array(W[b]) + nrng.normal(0, 0.15, S)
        w = np.clip(w, 0, None); W[b] = [float(x) for x in (w / w.sum() if w.sum() > 0 else np.ones(S) / S)]
        top.append(W)
    mid = sorted(((objective(score(W, budgets[1]), budgets[1]), k) for k, W in enumerate(top)), reverse=True)
    final = [top[k] for _, k in mid[:3]]
    best, best_v, best_sc = None, -1e9, None
    for W in final:
        sc = score(W, budgets[-1])
        v = objective(sc, budgets[-1])
        if v > best_v:
            best, best_v, best_sc = W, v, sc
    drops = [g for g, v in best_sc.items() if v < anchor[budgets[-1]].get(g, 0) - 0.01]
    print(f"melhor fusão: objetivo {best_v:.4f} · abaixo do âncora: {drops or '—'}", flush=True)
    print("  matriz:", {b: [round(x, 2) for x in w] for b, w in best.items()}, flush=True)
    # write the merged artifact
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    shutil.copytree(srcs[0], a.out, ignore=shutil.ignore_patterns("checkpoint.pt*", "calibration", "dataset.jsonl"))
    sds = M.merged(best)
    save_file({k: v.to(torch.bfloat16).contiguous().cpu() for k, v in sds[FILES[0]].items()}, os.path.join(a.out, FILES[0]))
    save_file({k: v.float().contiguous().cpu() for k, v in sds[FILES[1]].items()}, os.path.join(a.out, FILES[1]))
    mp = os.path.join(a.out, "metadata", "djorge.json")
    meta = json.load(open(mp))
    meta["merge_search"] = {"sources": [os.path.basename(s.rstrip("/")) for s in srcs], "matrix": best,
                            "objective": best_v, "groups": best_sc, "anchor_groups": anchor[budgets[-1]], "below_anchor": drops}
    meta.pop("selected_on_dev", None)
    meta["selected_on_dev"] = {"merge_search": True}
    json.dump(meta, open(mp, "w"), indent=1)
    print("saved", a.out, flush=True)


if __name__ == "__main__":
    main()
