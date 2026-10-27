"""Train an independent SPECIALIST LoRA (safety, web, code, or your own domain) on top of a trained Origo artifact.

    python -m djorge.training.specialist --artifact EXP/E3b --name code [--extra my_code.jsonl] [--steps 600]

* The Qwen backbone, the main Origo adapter and the decision core stay FROZEN; only the new LoRA (r=8) trains.
* Replay of general Origo data + a preservation loss keep general skills; the specialist is ACCEPTED only if it
  improves its own validation score and the general regression score drops by <= --tolerance.
* Saved as  artifact/specialists/<name>.safetensors  + .report.json, listed in artifact/specialists/registry.json.
* At inference a specialist is used ONLY when asked (`specialist="code"` in execute / the API); otherwise the base
  Origo runs unchanged. Each specialist is independent: add, retrain or delete one without touching the others.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time

import torch

from ..model import lora
from ..model.serialize import Serializer
from ..runtime.semantic import SemanticEngine, SemReq
from .data import generate
from .domains import BUILTIN, load_jsonl
from .evaluate import span_metrics
from .trainer import build_rows, preservation_loss, record_loss


def _score(engine, recs, adapter=None) -> float:
    res = [r for r in recs if r["task"] == "resolve"]
    ch = [r for r in recs if r["task"] == "CHOICE"]
    vals = []
    orig = engine.run
    if adapter:
        engine.run = lambda reqs, tenant_adapter=None, timings=None: orig(reqs, tenant_adapter=adapter, timings=timings)
    try:
        if res:
            vals.append(span_metrics(engine, res)["f1"])
        if ch:
            outs = engine.run([SemReq("CHOICE", r["source"], options=r["options"], ask=r.get("ask", ""),
                                      descriptions=r.get("descriptions")) for r in ch])
            vals.append(sum(o["answer"] == r["label"] for o, r in zip(outs, ch)) / len(ch))
    finally:
        engine.run = orig
    return sum(vals) / len(vals) if vals else 0.0


def train_specialist(artifact: str, name: str, extra: str = None, steps: int = 600, r: int = 8, lr: float = 1e-3,
                     tolerance: float = 0.03, seed: int = 0, device: str = "auto") -> dict:
    rng = random.Random(seed)
    recs = BUILTIN[name](seed=seed) if name in BUILTIN else []
    if extra:
        recs += load_jsonl(extra)
    tr = [x for x in recs if x["split"] == "train"]
    va = [x for x in recs if x["split"] == "validation"]
    if len(tr) < 20 or len(va) < 5:
        raise ValueError(f"specialist {name!r}: too little data ({len(tr)} train / {len(va)} validation). "
                         f"Pass --extra with labelled examples.")
    if name == "safety" and len({x.get("label") for x in tr}) < 3:
        raise ValueError("safety specialist needs labelled examples of the unsafe categories: pass --extra safety.jsonl")
    eng = SemanticEngine.from_artifact(artifact, device=device)
    ser0 = Serializer(eng.tok, eng.ser.max_tokens)

    def fits(x):
        try:
            build_rows(ser0, x, random.Random(0)); return True
        except ValueError:
            return False
    tr, va = [x for x in tr if fits(x)], [x for x in va if fits(x)]
    if len(tr) < 20 or len(va) < 5:
        raise ValueError(f"specialist {name!r}: too little data that fits the {eng.ser.max_tokens}-token window")
    reg = [x for x in generate(400, 100, 0, 0, 0, seed=seed + 7) if x["split"] == "test" and x["task"] == "resolve"][:80]
    replay = [x for x in generate(800, 200, 300, 300, 0, seed=seed + 11) if x["split"] == "train"]
    base_t, base_g = _score(eng, va), _score(eng, reg)
    full = f"specialist:{name}"
    m = eng.model
    lora.inject(m.backbone, full, r=r, alpha=2 * r)
    params = lora.adapter_params(m.backbone, full)
    opt = torch.optim.AdamW(params, lr=lr)
    ser = Serializer(eng.tok, eng.ser.max_tokens)
    lora.set_active(m.backbone, ["djorge", full])
    m.train()
    t0 = time.time()
    try:
        for step in range(1, steps + 1):
            batch = [rng.choice(tr) for _ in range(8)] + [rng.choice(replay) for _ in range(4)]
            built = [build_rows(ser, b, rng) for b in batch]
            rows = [b[0][0] for b in built]
            ids, roles, mask = m.collate(rows, eng.device)
            X = m.encode(ids, roles, mask)
            tls = m.token_logits(X, rows)
            loss = torch.stack([record_loss(m, X, i, rows[i], b, built[i][1], tls[i], eng.device)
                                for i, b in enumerate(batch)]).mean()
            loss = loss + 0.1 * preservation_loss(m, eng.tok, [b["source"] for b in batch[:6]], eng.device)
            opt.zero_grad(); loss.backward(); opt.step()
            if step % 100 == 0:
                print(f"  {name} step {step} loss {loss.item():.4f}", flush=True)
    finally:
        m.eval()
        lora.set_active(m.backbone, ["djorge"])
    eng._loaded_adapters = getattr(eng, "_loaded_adapters", set()) | {full}
    new_t, new_g = _score(eng, va, full), _score(eng, reg, full)
    accepted = new_t > base_t and (base_g - new_g) <= tolerance
    d = os.path.join(artifact, "specialists")
    os.makedirs(d, exist_ok=True)
    report = {"name": name, "adapter": full, "r": r, "steps": steps, "train": len(tr), "validation": len(va),
              "seconds": round(time.time() - t0, 1),
              "domain_score": {"base": round(base_t, 4), "specialist": round(new_t, 4)},
              "general_regression": {"base": round(base_g, 4), "specialist": round(new_g, 4), "tolerance": tolerance},
              "status": "accepted" if accepted else "rejected_no_gain_or_regression"}
    json.dump(report, open(os.path.join(d, f"{name}.report.json"), "w"), indent=1)
    reg_p = os.path.join(d, "registry.json")
    registry = json.load(open(reg_p)) if os.path.exists(reg_p) else {}
    if accepted:
        from safetensors.torch import save_file
        path = os.path.join(d, f"{name}.safetensors")
        save_file({k: v.contiguous() for k, v in lora.adapter_state(m.backbone, full).items()}, path)
        registry[name] = {"name": full, "file": f"{name}.safetensors", "alpha_over_r": 2.0, "report": report}
    else:
        registry.pop(name, None)
    json.dump(registry, open(reg_p, "w"), indent=1)
    del eng
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--name", required=True, help="safety | web | code | any name (needs --extra)")
    ap.add_argument("--extra", default=None, help="your labelled JSONL (see djorge.training.domains.load_jsonl)")
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--r", type=int, default=8)
    ap.add_argument("--device", default="auto")
    a = ap.parse_args()
    print(json.dumps(train_specialist(a.artifact, a.name, a.extra, a.steps, a.r, device=a.device), indent=1))
