"""Normalized result model.

INTERNAL result = rich, machine-readable source of truth (spans, probabilities,
engine, mode, provenance, versions). SURFACE result = a renderer over it
(`render()`), e.g. "1. Arthur".

Probability semantics (documented in docs/LANGUAGE.md):
  deterministic match   1.0  = exact under the mode's defined rules (not a belief)
  semantic match        calibrated P(span is a mention of the target | source, query)
  COUNT                 P(count == value) under span independence (Poisson-binomial)
  GET                   probability of the selected match
  EXISTS                1 - prod(1 - p_i)
  decisions             calibrated distribution over the supplied options
confidence = distribution-shape statistic; uncertainty = normalized entropy.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class Match:
    text: str
    start: int
    end: int
    probability: float
    engine: str
    mode: str
    target: str
    raw_score: Optional[float] = None       # uncalibrated logit (semantic only)
    index: int = 0
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        if self.raw_score is None:
            d.pop("raw_score")
        if not self.attributes:
            d.pop("attributes")
        d["probability"] = round(self.probability, 6)
        return d


@dataclass
class MatchSet:
    mode: str
    target: str
    engine: str
    matches: List[Match]
    candidates: List[Match] = field(default_factory=list)   # below acceptance threshold
    threshold: Optional[float] = None
    model: Optional[str] = None
    resolved_mode: Optional[str] = None                      # AUTO -> ID | CLASS | TYPE

    def __post_init__(self):
        for i, m in enumerate(self.matches, 1):
            m.index = i

    def all_scored(self) -> List[Match]:
        return sorted(self.matches + self.candidates, key=lambda m: m.start)

    def to_dict(self, diagnostics=False):
        d = {"type": "match_set", "mode": self.mode, "target": self.target, "engine": self.engine,
             "matches": [m.to_dict() for m in self.matches]}
        if self.resolved_mode and self.resolved_mode != self.mode:
            d["resolved_mode"] = self.resolved_mode
        if self.threshold is not None:
            d["threshold"] = self.threshold
        if self.model:
            d["model"] = self.model
        if diagnostics:
            d["candidates"] = [m.to_dict() for m in self.candidates]
        return d


def poisson_binomial(ps):
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for k, v in enumerate(dist):
            nxt[k] += v * (1 - p)
            nxt[k + 1] += v * p
        dist = nxt
    return dist


def choice_confidence(p):
    n = len(p)
    return 1.0 if n < 2 else float(min(1, max(0, (max(p) - 1 / n) / (1 - 1 / n))))


def score_confidence(p):
    n = len(p)
    if n < 2:
        return 1.0
    m = int(np.argmax(p))
    spread = sum(x * abs(i - m) for i, x in enumerate(p))
    even = sum(abs(i - (n - 1) / 2) for i in range(n)) / n
    return float(max(0.0, 1 - spread / even))


def norm_entropy(p):
    n = len(p)
    if n < 2:
        return 0.0
    return float(-sum(x * math.log(x) for x in p if x > 0) / math.log(n))


def _r(x, k=6):
    return None if x is None else round(float(x), k)


def render(res: dict) -> str:
    t = res.get("type")
    if res.get("abstain"):
        return f"(abstained: p={res.get('probability')})"
    if t == "report":
        return "\n".join(f"{m['index']}. {m['text']}" for m in res["matches"]) or "(no matches)"
    if t == "match_set":
        return ", ".join(m["text"] for m in res["matches"]) or "(no matches)"
    if t == "match":
        return "(no match)" if res["value"] is None else str(res["value"])
    if t in ("integer", "boolean", "number"):
        return str(res["value"]).lower() if t == "boolean" else str(res["value"])
    if t == "distribution":
        p = res.get("probability")
        return f"{res['answer']} ({p:.2f})" if isinstance(p, (int, float)) else f"{res['answer']} (rule)"
    if t == "ordinal":
        return f"{res['answer']} ({res['label']}, E={res['expected']:.2f})"
    if t == "ranking":
        return " > ".join(res["ranking"])
    if t == "set":
        return ", ".join(res["selected"]) or "(none)"
    return str(res.get("value"))


def asserted_view(ms: MatchSet) -> tuple:
    """Split a semantic MatchSet into asserted mentions and excluded (negated / non-factual) ones.
    Candidate probabilities become P(mention) x P(asserted)."""
    import copy
    keep, excluded, cands = [], [], []
    for m in ms.matches:
        pol = m.attributes.get("polarity", "asserted")
        (keep if pol == "asserted" else excluded).append(m)
    for m in ms.candidates + excluded:
        m2 = copy.copy(m)
        m2.probability = m.probability * m.attributes.get("p_asserted", 1.0)
        cands.append(m2)
    out = MatchSet(ms.mode, ms.target, ms.engine, keep, cands, ms.threshold, ms.model, ms.resolved_mode)
    return out, excluded


def _uncertain_items(ms: "MatchSet", thr) -> list:
    """Items whose own probability is doubtful: accepted but below the abstention threshold, or rejected but close
    to the acceptance line. P(whole answer right) is a product that shrinks with every extra item even when each item
    is certain, so COUNT / TOTAL abstain on a doubtful ITEM, not on the product."""
    return ([m.text for m in ms.matches if m.probability < (thr or 0)] +
            [m.text for m in ms.candidates if m.probability > 1 - (thr or 1)])


def operation_result(op: str, ms: MatchSet, at: Optional[int], abstain_thr: Optional[float], diagnostics=False,
                     mentions: bool = False) -> dict:
    """COUNT / REPORT / GET / EXISTS / TOTAL / MATCH over one normalized MatchSet.
    Semantic operations act on ASSERTED mentions unless MENTIONS is given (MATCH shows everything)."""
    excluded = []
    if op != "MATCH" and ms.engine == "semantic" and not mentions and ms.mode not in ("ANSWER",):
        ms, excluded = asserted_view(ms)
    base = {"operation": op, "mode": ms.mode, "target": ms.target, "engine": ms.engine}
    if ms.resolved_mode and ms.resolved_mode != ms.mode:
        base["resolved_mode"] = ms.resolved_mode
    if ms.model:
        base["model"] = ms.model
    det = ms.engine == "deterministic"
    if op == "MATCH":
        return {**ms.to_dict(diagnostics), "operation": "MATCH", "probability": None}
    if excluded:
        base["excluded"] = [{"text": m.text, "start": m.start, "end": m.end, "polarity": m.attributes.get("polarity")}
                            for m in excluded]
    if op == "TOTAL":
        if det:
            vals = [m.attributes.get("value") for m in ms.matches if m.attributes.get("value") is not None]
            return {**base, "type": "number", "value": sum(vals), "probability": 1.0, "matches": [m.to_dict() for m in ms.matches]}
        total = sum(m.attributes.get("quantity", 1.0) for m in ms.matches)
        p = float(np.prod([m.probability for m in ms.matches]) * np.prod([1 - m.probability for m in ms.candidates]))
        r = {**base, "type": "number", "value": int(total) if float(total).is_integer() else total, "probability": _r(p),
             "terms": [{"text": m.text, "quantity": m.attributes.get("quantity", 1.0),
                        "explicit": m.attributes.get("quantity_explicit", False)} for m in ms.matches],
             "matches": [m.to_dict() for m in ms.matches]}
        uncertain = _uncertain_items(ms, abstain_thr)
        r["count_policy"] = "per_item"
        r["uncertain_items"] = uncertain
        if abstain_thr is not None and uncertain:
            r.update(abstain=True, proposed_value=r["value"], value=None, abstain_threshold=abstain_thr)
        else:
            r["abstain"] = False
        return r
    if op == "REPORT":
        r = {**base, "type": "report", "matches": [m.to_dict() for m in ms.matches],
             "count": len(ms.matches)}
        if diagnostics:
            r["candidates"] = [m.to_dict() for m in ms.candidates]
        return r
    scored = ms.all_scored()
    ps = [m.probability for m in scored]
    if op == "COUNT":
        n = len(ms.matches)
        if det:
            return {**base, "type": "integer", "value": n, "probability": 1.0, "matches": [m.to_dict() for m in ms.matches]}
        dist = poisson_binomial(ps)
        p = dist[n] if n < len(dist) else 0.0
        r = {**base, "type": "integer", "value": n, "probability": _r(p), "expected": _r(sum(ps), 4),
             "distribution": {str(k): _r(v) for k, v in enumerate(dist) if v > 1e-4},
             "confidence": _r(choice_confidence(dist)), "uncertainty": _r(norm_entropy(dist)),
             "matches": [m.to_dict() for m in ms.matches]}
        # Per-item policy: P(count exactly right) shrinks with every extra item even when each one is
        # certain, so large counts would always abstain. Abstain only if some item is itself uncertain.
        uncertain = _uncertain_items(ms, abstain_thr)
        r["count_policy"] = "per_item"
        r["uncertain_items"] = uncertain
        if abstain_thr is not None and uncertain:
            r.update(abstain=True, proposed_value=r["value"], value=None, abstain_threshold=abstain_thr)
        else:
            r["abstain"] = False
        return r
    if op == "EXISTS":
        if det:
            return {**base, "type": "boolean", "value": bool(ms.matches), "probability": 1.0,
                    "matches": [m.to_dict() for m in ms.matches]}
        p_any = 1 - float(np.prod([1 - x for x in ps])) if ps else 0.0
        val = p_any >= 0.5
        r = {**base, "type": "boolean", "value": val, "p_true": _r(p_any),
             "probability": _r(p_any if val else 1 - p_any), "matches": [m.to_dict() for m in ms.matches]}
        return _abstain(r, abstain_thr)
    if op == "GET":
        # GET semantics: with AT n -> the n-th accepted match in source order;
        # otherwise the highest-probability accepted match, ties -> earliest.
        # Deterministic matches all have p=1, so GET ID == first occurrence.
        sel = None
        if ms.matches:
            if at is not None:
                idx = at - 1 if at > 0 else len(ms.matches) + at
                sel = ms.matches[idx] if 0 <= idx < len(ms.matches) else None
            else:
                sel = max(ms.matches, key=lambda m: (m.probability, -m.start))
        r = {**base, "type": "match", "value": sel.text if sel else None,
             "match": sel.to_dict() if sel else None,
             "probability": 1.0 if det else (_r(sel.probability) if sel else None)}
        if sel is None:
            r["status"] = "NO_MATCH"
        return r if det or sel is None else _abstain(r, abstain_thr)
    raise ValueError(op)


def _abstain(r, thr):
    if thr is not None and r.get("probability") is not None and r["probability"] < thr:
        r["abstain"] = True
        r["proposed_value"] = r["value"]
        r["value"] = None
        r["abstain_threshold"] = thr
    else:
        r["abstain"] = False
    return r
