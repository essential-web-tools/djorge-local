"""Reference-adjusted calibration and calibrated prediction sets.

1) Contextual calibration (Zhao et al., 2021, "Calibrate Before Use"): the same question and options are asked about
   CONTENT-FREE reference inputs ("N/A", empty, a placeholder). Whatever the model prefers there is bias of the
   question/options, not evidence from the text. The answer distribution is divided by that reference distribution
   and renormalised. Useful for labels a caller defines on the fly (/v1/classify), where no calibration data exists.

2) Batch calibration (Zhou et al., ICLR 2024): the reference is the MEAN distribution over a batch of real inputs
   for the same question/options; no labels needed. Use when many texts are classified with the same labels.

3) Conformal prediction sets (Angelopoulos & Bates): from the calibration split, qhat is the
   ceil((n+1)(1-alpha))/n quantile of (1 - p_true). At inference the set {options with p >= 1 - qhat} contains the
   right answer with probability >= 1 - alpha (marginal guarantee, exchangeable data). An empty or multi-option
   set is an honest "unsure".
"""
from __future__ import annotations

import json
import math
import os
import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Sequence

import numpy as np

REFERENCE_INPUTS = ("N/A", "", "[texto]")


def _norm(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-9, None)
    return p / p.sum()


def correct(dist: Dict[str, float], reference: Dict[str, float]) -> Dict[str, float]:
    """p_corrected ∝ p / p_reference (diagonal correction of Zhao et al.)."""
    keys = list(dist)
    p = np.array([dist[k] for k in keys], float)
    q = np.array([max(reference.get(k, 1.0 / len(keys)), 1e-6) for k in keys], float)
    c = _norm(p / q)
    return {k: round(float(v), 6) for k, v in zip(keys, c)}


class ContextualCalibrator:
    """Caches the reference distribution per (question, options) so the extra forwards happen once."""

    def __init__(self, engine, max_items: int = 2048):
        self.eng = engine
        self.cache: "OrderedDict[str, Dict[str, float]]" = OrderedDict()
        self.max_items = max_items
        self.lock = threading.Lock()

    def reference(self, options: Sequence[str], descriptions: Sequence[str] = (), ask: str = "") -> Dict[str, float]:
        from .semantic import SemReq
        key = json.dumps([list(options), list(descriptions or []), ask], ensure_ascii=False)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        outs = self.eng.run([SemReq("CHOICE", ref, options=list(options), descriptions=list(descriptions or ()),
                                    ask=ask) for ref in REFERENCE_INPUTS])
        ref = {o: float(np.mean([out["distribution"][o] for out in outs])) for o in options}
        with self.lock:
            self.cache[key] = ref
            if len(self.cache) > self.max_items:
                self.cache.popitem(last=False)
        return ref

    def apply(self, result: dict, options, descriptions=(), ask="") -> dict:
        ref = self.reference(options, descriptions, ask)
        d = correct(result["distribution"], ref)
        best = max(d, key=d.get)
        out = dict(result, distribution=d, answer=best, probability=d[best],
                   reference_distribution={k: round(v, 6) for k, v in ref.items()}, calibration="contextual")
        return out


def batch_correct(dists: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Batch calibration: divide every distribution by the batch mean (same question/options for all)."""
    if not dists:
        return []
    keys = list(dists[0])
    mean = {k: float(np.mean([d[k] for d in dists])) for k in keys}
    return [correct(d, mean) for d in dists]


# ───────────────────────────── conformal ─────────────────────────────
def fit_conformal(pairs: Dict[str, List[float]], alpha: float = 0.05) -> Dict[str, float]:
    """pairs: {bucket: [p_true, ...]} from the calibration split -> {bucket: qhat}."""
    out = {}
    for k, ps in pairs.items():
        n = len(ps)
        if n < 30:
            continue
        s = np.sort(1.0 - np.array(ps))
        level = min(1.0, math.ceil((n + 1) * (1 - alpha)) / n)
        out[k] = round(float(np.quantile(s, level, method="higher")), 6)
    return out


class Conformal:
    def __init__(self, table: Optional[dict] = None):
        self.table = table or {}
        self.alpha = self.table.get("alpha")

    @classmethod
    def load(cls, artifact: str) -> "Conformal":
        p = os.path.join(artifact, "calibration", "conformal.json")
        return cls(json.load(open(p))) if os.path.exists(p) else cls()

    def qhat(self, kind: str, k: int) -> Optional[float]:
        from .calibration import bucket
        q = self.table.get("qhat", {})
        return q.get(f"{kind}/{bucket(k)}", q.get(f"{kind}/*"))

    def prediction_set(self, kind: str, dist: Dict[str, float]) -> Optional[dict]:
        q = self.qhat(kind, len(dist))
        if q is None:
            return None
        s = [o for o, p in sorted(dist.items(), key=lambda x: -x[1]) if p >= 1.0 - q]
        return {"options": s, "coverage": round(1 - self.alpha, 4) if self.alpha else None,
                "singleton": len(s) == 1}
