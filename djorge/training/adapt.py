"""Explicit, user-triggered adaptation from user-owned typed records.

    store records (explicit) -> validate -> dedupe -> contradictions -> sufficiency
        -> choose mechanism:
             definitions / aliases / thresholds only   => structured context (no training)
             >= MIN_EXAMPLES labelled examples         => tenant LoRA (r=4) on the shared backbone
        -> deterministic train/val split -> train tenant adapter (core frozen, replay of
           general DJORGE data + preservation loss against forgetting)
        -> evaluate base vs adapted on (1) tenant val, (2) general regression set
        -> ACCEPT only if tenant metric improves and regression drop <= tolerance
        -> versioned adapter file + new context version pointing to it
Never triggered by execute(); ordinary queries are never stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from typing import Dict, List

import torch

from ..errors import DjorgeError
from ..model import lora
from ..model.serialize import Serializer
from ..runtime.context import ContextStore, Snapshot
from ..runtime.semantic import SemanticEngine, SemReq
from . import losses as L
from .data import generate
from .evaluate import span_metrics
from .trainer import build_rows, preservation_loss, record_loss

MIN_EXAMPLES = 20
MIN_PER_TARGET = 5


def _to_training(r: dict):
    if r["type"] == "extraction_example":
        return {"task": "resolve", "mode": r["mode"], "target": r["target"], "source": r["input"],
                "spans": r["spans"], "meta": {"lang": r.get("metadata", {}).get("lang", "?")}}
    return {"task": "CHOICE", "source": r["input"], "options": r["options"], "label": r["target"],
            "descriptions": [""] * len(r["options"]), "ask": r.get("ask", ""), "meta": {"lang": "?"}}


def analyze(snap: Snapshot) -> dict:
    recs = list(snap.data["records"].values())
    structured = [r for r in recs if r["type"] in ("class_definition", "alias", "threshold")]
    examples = [r for r in recs if r["type"] in ("extraction_example", "classification_example")]
    seen, dup, groups = {}, 0, {}
    contradictions = []
    for r in examples:
        key = (r["type"], r["input"].strip().casefold(), r.get("mode"), r["target"] if r["type"] == "extraction_example" else tuple(r["options"]))
        val = json.dumps(r["spans"] if r["type"] == "extraction_example" else r["target"])
        if key in seen:
            if seen[key] != val:
                contradictions.append({"input": r["input"][:80], "values": [seen[key], val]})
            else:
                dup += 1
            continue
        seen[key] = val
        g = r["target"] if r["type"] == "extraction_example" else "choice:" + r["target"]
        groups[g] = groups.get(g, 0) + 1
    n = len(seen)
    warnings = [f"target {g!r} has only {c} examples (< {MIN_PER_TARGET})" for g, c in groups.items() if c < MIN_PER_TARGET]
    if n == 0:
        mech = "structured_context"
    elif n < MIN_EXAMPLES or contradictions:
        mech = "rejected"
    else:
        mech = "tenant_lora"
    reasons = []
    if contradictions:
        reasons.append(f"{len(contradictions)} contradictory examples")
    if 0 < n < MIN_EXAMPLES:
        reasons.append(f"only {n} unique examples (< {MIN_EXAMPLES}); definitions/aliases still apply as structured context")
    return {"mechanism": mech, "structured_records": len(structured), "unique_examples": n, "duplicates": dup,
            "contradictions": contradictions, "warnings": warnings, "reasons": reasons, "per_target": groups}


def adapt(engine: SemanticEngine, store: ContextStore, tenant: str, steps: int = 150, r: int = 4,
          lr: float = 1e-3, tolerance: float = 0.03, seed: int = 0) -> dict:
    snap = store.get(tenant)
    plan = analyze(snap)
    if plan["mechanism"] == "structured_context":
        return {**plan, "status": "no_training_needed"}
    if plan["mechanism"] == "rejected":
        raise DjorgeError("ADAPTATION_REJECTED", "; ".join(plan["reasons"]), plan)
    ex = [r_ for r_ in snap.data["records"].values() if r_["type"] in ("extraction_example", "classification_example")]
    ex.sort(key=lambda x: hashlib.sha1(x["id"].encode()).hexdigest())     # deterministic split
    cut = max(1, int(0.8 * len(ex)))
    tr, va = [_to_training(x) for x in ex[:cut]], [_to_training(x) for x in ex[cut:]]
    reg = [x for x in generate(400, 100, 0, 0, 0, seed=seed + 7) if x["split"] == "test" and x["task"] == "resolve"][:80]
    replay = [x for x in generate(800, 200, 300, 300, 0, seed=seed + 11) if x["split"] == "train"]

    def score(rs):
        res = [x for x in rs if x["task"] == "resolve"]
        f1 = span_metrics(engine, res)["f1"] if res else None
        ch = [x for x in rs if x["task"] == "CHOICE"]
        acc = None
        if ch:
            outs = engine.run([SemReq("CHOICE", x["source"], options=x["options"], ask=x.get("ask", "")) for x in ch])
            acc = sum(o["answer"] == x["label"] for o, x in zip(outs, ch)) / len(ch)
        vals = [v for v in (f1, acc) if v is not None]
        return sum(vals) / len(vals)

    base_t, base_g = score(va), score(reg)
    version = snap.version + 1
    name = f"tenant:{tenant}:v{version}"
    m = engine.model
    lora.inject(m.backbone, name, r=r, alpha=2 * r)
    params = lora.adapter_params(m.backbone, name)
    opt = torch.optim.AdamW(params, lr=lr)
    ser = Serializer(engine.tok, engine.ser.max_tokens)
    rng = random.Random(seed)
    lora.set_active(m.backbone, ["djorge", name])
    m.train()
    t0 = time.time()
    try:
        for step in range(steps):
            batch = [rng.choice(tr) for _ in range(6)] + [rng.choice(replay) for _ in range(6)]
            built = [build_rows(ser, b, rng) for b in batch]
            rows = [b[0][0] for b in built]
            ids, roles, mask = m.collate(rows, engine.device)
            X = m.encode(ids, roles, mask)
            tls = m.token_logits(X, rows)
            loss = torch.stack([record_loss(m, X, i, rows[i], b, built[i][1], tls[i], engine.device)
                                for i, b in enumerate(batch)]).mean()
            loss = loss + 0.1 * preservation_loss(m, engine.tok, [b["source"] for b in batch[:6]], engine.device)
            opt.zero_grad(); loss.backward(); opt.step()
    finally:
        m.eval()
    # evaluate with the tenant adapter active
    orig = engine.run

    def run_with(reqs, tenant_adapter=None, timings=None):
        return orig(reqs, tenant_adapter=name, timings=timings)
    engine.run = run_with
    try:
        new_t, new_g = score(va), score(reg)
    finally:
        engine.run = orig
        lora.set_active(m.backbone, ["djorge"])
    accepted = new_t > base_t and (base_g - new_g) <= tolerance
    report = {**plan, "adapter": name, "train_examples": len(tr), "val_examples": len(va), "steps": steps,
              "seconds": round(time.time() - t0, 1), "tenant_metric": {"base": round(base_t, 4), "adapted": round(new_t, 4)},
              "general_regression": {"base": round(base_g, 4), "adapted": round(new_g, 4), "tolerance": tolerance},
              "status": "accepted" if accepted else "rejected_no_gain_or_regression"}
    d = os.path.join(store.root, tenant, "adapters")
    os.makedirs(d, exist_ok=True)
    from safetensors.torch import save_file
    path = os.path.join(d, f"v{version}.safetensors")
    save_file({k: v.contiguous() for k, v in lora.adapter_state(m.backbone, name).items()}, path)
    json.dump(report, open(os.path.join(d, f"v{version}.report.json"), "w"), indent=2)
    engine._loaded_adapters = getattr(engine, "_loaded_adapters", set()) | {name}
    if accepted:
        store.set_active_adapter(tenant, {"name": name, "path": path, "alpha_over_r": 2.0})
    return report
