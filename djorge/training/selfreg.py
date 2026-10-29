"""Self-regulated training helpers (DOFL): per-group scores and losses, used by the trainer to
  * balance what it trains on (groups falling behind the anchor model get sampled more), and
  * choose the checkpoint on the "escolha" part: fewest groups below the anchor first, then the best mean.
A group is meta.group (see djorge.training.flash_data.group_of)."""
from __future__ import annotations

import json
import random
from collections import defaultdict
from typing import Dict, List

import numpy as np
import torch

from .flash_data import group_of


def load_groups(path: str, per_group: int, fits, seed: int = 0) -> Dict[str, List[dict]]:
    by = defaultdict(list)
    for l in open(path, encoding="utf-8"):
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("task") in ("CHOICE", "NOUL", "SCORE") and r.get("label") is None:
            continue
        if r.get("known") is not None or not fits(r):
            continue
        r.setdefault("meta", {}).setdefault("lang", "und")
        by[group_of(r)].append(r)
    rng = random.Random(seed)
    out = {}
    for g, rs in by.items():
        rng.shuffle(rs)
        if len(rs) >= 8:
            out[g] = rs[:per_group]
    return out


def group_scores(engine, groups: Dict[str, List[dict]]) -> Dict[str, float]:
    """Accuracy (decisions) or span F1 (spans) per group, through the real inference path."""
    from .evaluate import decision_metrics, span_metrics
    out = {}
    for g, rs in groups.items():
        sp = [r for r in rs if r["task"] in ("resolve",)]
        dec = [r for r in rs if r["task"] in ("CHOICE", "NOUL", "SCORE")]
        vals = []
        if sp:
            vals.append(span_metrics(engine, sp)["f1"])
        if dec:
            dm = decision_metrics(engine, dec)
            vals += [v.get("accuracy") for v in dm.values() if v.get("accuracy") is not None]
        if vals:
            out[g] = float(np.mean(vals))
    return out


@torch.no_grad()
def group_losses(model, ser, groups: Dict[str, List[dict]], dev, build_rows, record_loss, soft_w=0.5, bs=16):
    """Mean training loss per group (no gradient), on any device - cheap enough to run every few hundred steps."""
    was = model.training
    model.eval()
    out = {}
    for g, rs in groups.items():
        tot, n = 0.0, 0
        for k in range(0, len(rs), bs):
            batch = rs[k:k + bs]
            built = [build_rows(ser, r, random.Random(k + j)) for j, r in enumerate(batch)]
            rows = [b[0][0] for b in built]
            ids, roles, mask = model.collate(rows, dev)
            X = model.encode(ids, roles, mask)
            tls = model.token_logits(X, rows)
            for i, r in enumerate(batch):
                x = record_loss(model, X, i, rows[i], r, built[i][1], tls[i], dev, soft_w)
                if x is not None and torch.isfinite(x):
                    tot += float(x); n += 1
        if n:
            out[g] = tot / n
    model.train(was)
    return out


def balance_weights(base: Dict[str, float], student: Dict[str, float], anchor: Dict[str, float], k: float = 2.0,
                    lo: float = 0.5, hi: float = 3.0) -> Dict[str, float]:
    """Groups whose loss is above the anchor's get more weight (exp(k * gap)), within [lo, hi] x their base share."""
    w = {}
    for g, b in base.items():
        gap = student.get(g, 0.0) - anchor.get(g, student.get(g, 0.0))
        w[g] = b * float(np.clip(np.exp(k * gap), lo, hi))
    s = sum(w.values()) or 1.0
    return {g: v / s for g, v in w.items()}
