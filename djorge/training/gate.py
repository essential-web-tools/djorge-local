"""Non-degradation gate and weight mixing (WiSE-FT / sparse merge) for Origo versions.

    mix(base, new, alpha, dest, alpha_decision=None, density=1.0)
        (1-alpha)*base + alpha*new on every float tensor (backbone/ and decision/); alpha_decision may differ for the
        decision heads; density < 1 keeps only the largest |new-base| changes per tensor (TIES/DARE-style).
        alpha = 0 reproduces `base` exactly. Recalibrate the result before measuring it.

    compare(base_eval, new_eval, keys=("suite", "task"), rounds=2000)
        paired bootstrap per task on the SAME items: a task fails only if the 95% interval of
        (base_ok - new_ok) is entirely above 0 (a REAL drop, not 1-2 items of noise). Tasks with < min_n items are
        reported but not used to fail.
"""
from __future__ import annotations

import json
import os
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np


def mix(base: str, new: str, alpha: float, dest: str, alpha_decision: float = None, density: float = 1.0) -> str:
    import torch
    from safetensors.torch import load_file, save_file
    ad = alpha if alpha_decision is None else alpha_decision
    if Path(dest).exists():
        shutil.rmtree(dest)
    shutil.copytree(new, dest, ignore=shutil.ignore_patterns("checkpoint.pt*", "calibration"))
    g = torch.Generator().manual_seed(0)
    for p in Path(new).rglob("*.safetensors"):
        rel = p.relative_to(new)
        if not (Path(base) / rel).is_file():
            continue
        a, b = load_file(str(Path(base) / rel)), load_file(str(p))
        if set(a) != set(b):
            continue
        w = ad if str(rel).startswith("decision") else alpha
        out = {}
        for k, tb in b.items():
            ta = a[k]
            if not (tb.is_floating_point() and ta.shape == tb.shape):
                out[k] = tb; continue
            d = tb.float() - ta.float()
            if density < 1.0 and d.numel() > 16:
                flat = d.abs().flatten()
                idx = torch.randint(0, flat.numel(), (min(flat.numel(), 1_000_000),), generator=g)
                cut = torch.quantile(flat[idx], 1 - density)
                d = torch.where(d.abs() >= cut, d, torch.zeros_like(d))
            out[k] = (ta.float() + w * d).to(tb.dtype)
        save_file(out, str(Path(dest) / rel))
    mp = Path(dest) / "metadata" / "djorge.json"
    meta = json.load(open(mp))
    meta["merge"] = {"base": Path(base).name, "new": Path(new).name, "alpha": alpha, "alpha_decision": ad,
                     "density": density}
    json.dump(meta, open(mp, "w"), indent=2)
    return dest


def compare(base_eval: dict, new_eval: dict, rounds: int = 2000, min_n: int = 20, seed: int = 0) -> dict:
    """Both evals must come from djorge.training.origo_eval on the same item ids."""
    rng = np.random.default_rng(seed)
    b = {(i["suite"], i["task"], i["id"]): i for i in base_eval["items"]}
    n = {(i["suite"], i["task"], i["id"]): i for i in new_eval["items"]}
    common = sorted(set(b) & set(n))
    by = defaultdict(list)
    for k in common:
        by[(k[0], k[1])].append(float(b[k]["ok"]) - float(n[k]["ok"]))   # > 0: the new version got it wrong
    rep, fails = {}, []
    for (suite, task), d in sorted(by.items()):
        d = np.array(d)
        if len(d) < min_n:
            rep[f"{suite}/{task}"] = {"n": len(d), "delta": round(-d.mean(), 4), "verdict": "poucos itens"}
            continue
        bs = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(rounds)])
        lo, hi = np.percentile(bs, 2.5), np.percentile(bs, 97.5)
        verdict = "piora real" if lo > 0 else ("melhora real" if hi < 0 else "empate (ruído)")
        rep[f"{suite}/{task}"] = {"n": len(d), "delta": round(-d.mean(), 4), "ci95": [round(-hi, 4), round(-lo, 4)],
                                  "verdict": verdict}
        if verdict == "piora real":
            fails.append(f"{suite}/{task}")
    gains = [k for k, v in rep.items() if v.get("verdict") == "melhora real"]
    return {"passed": not fails, "real_drops": fails, "real_gains": gains, "tasks": rep, "items_compared": len(common)}
