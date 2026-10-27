"""Evaluation: semantic quality, calibration, held-out generalization, and
REGRESSION of general semantics (base Qwen vs DJORGE-adapted backbone).

Regression protocol: identical linear probes trained on mean-pooled hidden
states of the base backbone (LoRA disabled) and the adapted backbone on the
`general` split (tasks never trained). A drop in probe accuracy measures lost
general semantic information.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Dict, List

import numpy as np
import torch

from ..model import lora
from ..runtime.calibration import aurc, brier, ece, nll
from ..runtime.semantic import SemanticEngine
from .calibrate import req_of


def span_metrics(engine, recs) -> dict:
    outs = engine.run([req_of(r) for r in recs])
    tp = fp = fn = 0
    count_ok = 0
    conf, corr, per_lang = [], [], defaultdict(lambda: [0, 0, 0])
    for r, ms in zip(recs, outs):
        gold = {tuple(s) for s in r["spans"]}
        pred = {(m.start, m.end) for m in ms.matches}
        a, b, c = len(pred & gold), len(pred - gold), len(gold - pred)
        tp, fp, fn = tp + a, fp + b, fn + c
        pl = per_lang[r["meta"]["lang"]]
        pl[0] += a; pl[1] += b; pl[2] += c
        count_ok += int(len(pred) == len(gold))
        for m in ms.all_scored():
            conf.append(m.probability); corr.append(int((m.start, m.end) in gold))
    f1 = lambda tp, fp, fn: 2 * tp / max(2 * tp + fp + fn, 1)
    return {"n": len(recs), "precision": round(tp / max(tp + fp, 1), 4), "recall": round(tp / max(tp + fn, 1), 4),
            "f1": round(f1(tp, fp, fn), 4), "count_accuracy": round(count_ok / max(len(recs), 1), 4),
            "span_ece": round(ece(conf, corr), 4) if conf else None,
            "f1_by_lang": {k: round(f1(*v), 4) for k, v in sorted(per_lang.items())}}


def decision_metrics(engine, recs) -> dict:
    out = {}
    by = defaultdict(list)
    for r in recs:
        by[r["task"]].append(r)
    for task, rs in by.items():
        res = engine.run([req_of(r) for r in rs])
        if task in ("CHOICE", "SCORE"):
            P = [[o["distribution"][x] for x in r["options"]] for r, o in zip(rs, res)]
            R = [[o["raw_distribution"][x] for x in r["options"]] for r, o in zip(rs, res)]
            y = [r["options"].index(r["label"]) if task == "CHOICE" else r["label"] for r in rs]
            ok = [int(np.argmax(p) == t) for p, t in zip(P, y)]
            out[task] = {"n": len(rs), "accuracy": round(float(np.mean(ok)), 4),
                         "nll_raw": round(nll(R, y), 4), "nll_cal": round(nll(P, y), 4),
                         "brier_cal": round(brier(P, y), 4),
                         "ece_raw": round(ece([max(p) for p in R], [int(np.argmax(p) == t) for p, t in zip(R, y)]), 4),
                         "ece_cal": round(ece([max(p) for p in P], ok), 4), "aurc": round(aurc([max(p) for p in P], ok), 4)}
        elif task == "NOUL":
            ok = [int(o["answer"] == bool(r["label"])) for r, o in zip(rs, res)]
            out[task] = {"n": len(rs), "accuracy": round(float(np.mean(ok)), 4),
                         "ece": round(ece([o["probability"] for o in res], ok), 4)}
        elif task == "SET":
            out[task] = {"n": len(rs), "exact_match": round(float(np.mean([set(o["selected"]) == set(r["label"]) for r, o in zip(rs, res)])), 4)}
        elif task == "RANK":
            out[task] = {"n": len(rs), "top1": round(float(np.mean([o["ranking"][0] == r["label"][0] for r, o in zip(rs, res)])), 4)}
    return out


@torch.no_grad()
def _pooled(engine, texts, adapted: bool):
    m, tok = engine.model, engine.tok
    feats = []
    ctx = lora.disabled(m.backbone) if not adapted else torch.no_grad()
    with ctx:
        for i in range(0, len(texts), 32):
            enc = tok(texts[i:i + 32], add_special_tokens=False, padding=True, return_tensors="pt")
            h = m.backbone(input_ids=enc["input_ids"].to(engine.device),
                           attention_mask=enc["attention_mask"].to(engine.device), use_cache=False).last_hidden_state
            msk = enc["attention_mask"].unsqueeze(-1).to(h.device).float()
            feats.append(((h.float() * msk).sum(1) / msk.sum(1)).cpu())
    return torch.cat(feats)


def _probe(X, y, seed=0):
    g = torch.Generator().manual_seed(seed)
    idx = torch.randperm(len(y), generator=g)
    cut = int(0.7 * len(y))
    tr, te = idx[:cut], idx[cut:]
    mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-6
    Xn = (X - mu) / sd
    W = torch.zeros(X.shape[1], int(y.max()) + 1, requires_grad=True)
    b = torch.zeros(int(y.max()) + 1, requires_grad=True)
    opt = torch.optim.LBFGS([W, b], max_iter=200)

    def closure():
        opt.zero_grad()
        l = torch.nn.functional.cross_entropy(Xn[tr] @ W + b, y[tr]) + 1e-3 * (W ** 2).sum()
        l.backward()
        return l
    opt.step(closure)
    return float(((Xn[te] @ W + b).argmax(1) == y[te]).float().mean())


def regression(engine, recs) -> dict:
    gen = [r for r in recs if r["split"] == "general"]
    if not gen or not engine.model.cfg["use_lora"]:
        return {"note": "no LoRA (backbone unchanged) - regression is zero by construction" if gen else "no general split"}
    texts, y = [r["source"] for r in gen], torch.tensor([r["label"] for r in gen])
    Xb, Xa = _pooled(engine, texts, False), _pooled(engine, texts, True)
    base, adapted = _probe(Xb, y), _probe(Xa, y)
    cos = torch.nn.functional.cosine_similarity(Xb, Xa, dim=1)
    Sb = torch.nn.functional.normalize(Xb - Xb.mean(0), dim=1); Sa = torch.nn.functional.normalize(Xa - Xa.mean(0), dim=1)
    rsa = float(torch.corrcoef(torch.stack([(Sb @ Sb.T).flatten(), (Sa @ Sa.T).flatten()]))[0, 1])
    return {"probe": "sentiment (multilingual, never trained)", "n": len(gen), "base_accuracy": round(base, 4),
            "adapted_accuracy": round(adapted, 4), "delta": round(adapted - base, 4),
            "hidden_cosine_base_vs_adapted": round(float(cos.mean()), 4),
            "similarity_structure_correlation": round(rsa, 4),
            "note": "probe saturates on this synthetic set; drift metrics are the informative signal at this scale"}


def answer_metrics(engine, recs) -> dict:
    """Grounded QA: exact match of the top evidence span, token F1, and confidence quality."""
    import re as _re
    outs = engine.run([req_of(r) for r in recs])
    em, f1s, conf, ok, fam = [], [], [], [], defaultdict(list)
    for r, ms in zip(recs, outs):
        gold = [r["source"][a:b] for a, b in r["spans"]]
        top = max(ms.all_scored(), key=lambda m: m.probability, default=None)
        pred = top.text if top else ""
        g = gold[0] if gold else ""
        e = int(pred.casefold() == g.casefold())
        pt, gt = _re.findall(r"\w+", pred.casefold()), _re.findall(r"\w+", g.casefold())
        common = sum(min(pt.count(w), gt.count(w)) for w in set(pt))
        f = 0.0 if not common else 2 * common / (len(pt) + len(gt))
        em.append(e); f1s.append(f); fam[r["meta"].get("family", "?")].append(e)
        if top:
            conf.append(top.probability); ok.append(e)
    return {"n": len(recs), "exact_match": round(float(np.mean(em)), 4), "token_f1": round(float(np.mean(f1s)), 4),
            "ece": round(ece(conf, ok), 4) if conf else None,
            "high_conf_error_rate": round(float(np.mean([1 - o for c, o in zip(conf, ok) if c >= 0.9])), 4) if any(c >= 0.9 for c in conf) else None,
            "em_by_family": {k: round(float(np.mean(v)), 4) for k, v in sorted(fam.items())}}


def evaluate(path: str) -> dict:
    eng = SemanticEngine.from_artifact(path, device="cpu")
    recs = [json.loads(l) for l in open(os.path.join(path, "dataset.jsonl"))]
    rep = {}
    for split in ("test", "test_unseen_member", "test_unseen_class"):
        rs = [r for r in recs if r["split"] == split and r["task"] == "resolve"]
        for mode in ("CLASS", "ENTITY"):
            sub = [r for r in rs if r["mode"] == mode]
            if sub:
                rep[f"{split}/{mode}"] = span_metrics(eng, sub)
    hard = [r for r in recs if r["split"] == "test" and r["meta"].get("difficulty") == "hard_context"]
    if hard:
        rep["test/hard_context"] = span_metrics(eng, hard)
    for split in ("test", "calibration"):
        rs = [r for r in recs if r["split"] == split and r["task"] == "answer"]
        if rs:
            rep[f"{split}/ANSWER"] = answer_metrics(eng, rs)
    neg = [r for r in recs if r["split"] == "test" and r["task"] == "resolve" and "negated" in (r.get("polarity") or [])]
    if neg:
        outs = eng.run([req_of(r) for r in neg])
        hit = tot = 0
        for r, ms in zip(neg, outs):
            gold = {tuple(s): p for s, p in zip(r["spans"], r["polarity"])}
            for m in ms.matches:
                if (m.start, m.end) in gold:
                    tot += 1; hit += int(m.attributes.get("polarity") == gold[(m.start, m.end)])
        rep["test/polarity_accuracy_on_negated_sentences"] = round(hit / max(tot, 1), 4)
    rep["decisions"] = decision_metrics(eng, [r for r in recs if r["split"] == "test" and r["task"] != "resolve"])
    rep["regression"] = regression(eng, recs)
    return rep


if __name__ == "__main__":
    r = evaluate(sys.argv[1])
    json.dump(r, open(os.path.join(sys.argv[1], "metadata", "eval.json"), "w"), indent=2)
    print(json.dumps(r, indent=1))
