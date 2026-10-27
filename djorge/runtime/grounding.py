"""CHOICE as plan -> execute -> verify (replaces direct option-similarity scoring).

Order of evidence, strongest first:
  1. deterministic comparison  - question has a comparative cue (taller, mais velho, ...) and the
                                 source states a number for >= 2 option entities -> argmax/argmin
  2. grounded evidence         - ANSWER resolution extracts evidence spans for the question; each
                                 option is scored by how well the evidence supports it
                                 (numeric equality or content-token coverage F1, x span probability)
  3. classifier fallback       - option scorer; reported with grounded=false and a strict abstention
                                 threshold, because nothing in the source was found to support it
"""
from __future__ import annotations

from .result import choice_confidence
import re
from .structure import anchor_choice, fold, recency_choice, role_bindings, support, transfer_choice

YESNO = {"yes", "no", "sim", "não", "nao", "sí", "si", "maybe", "unknown", "talvez", "não se sabe", "oui", "non", "ja", "nein"}
MARGIN = 1.3          # best support must exceed the runner-up by this ratio, else abstain

GROUND_MIN = 0.35


def surfaces(node):
    """Text to look for in the source for each option: the description when the label is only a key
    ({"a": "John"} -> "John"), otherwise the label itself."""
    return [d.strip() if d and d.strip() else o for o, d in zip(node.options, node.descriptions or [""] * len(node.options))]


def _present(surface: str, text_folded: str) -> bool:
    w = fold(surface).strip()
    if len(w) < 2:
        return False
    return re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text_folded) is not None


def is_extractive(node, source: str) -> bool:
    """CHOICE whose options are things MENTIONED in the source (who/which/when among entities, dates, names) ->
    answer must be grounded in evidence. Otherwise it is CLASSIFICATION (labels like 'billing', 'urgent',
    'positive' that the text does not literally contain) -> the calibrated classifier decides directly,
    in one forward pass, with the normal decision threshold."""
    tf = fold(source or "")
    hits = sum(1 for o, sf in zip(node.options, surfaces(node)) if _present(o, tf) or (sf != o and _present(sf, tf)))
    return hits >= 2


def _label(node, surface):
    return dict(zip(surfaces(node), node.options)).get(surface, surface)


def compare(node, cmp, facts):
    facts = {_label(node, k): v for k, v in facts.items()}
    direction, _ = cmp
    pick = (max if direction == "max" else min)(facts, key=facts.get)
    vals = sorted(facts.values())
    tie = len(vals) >= 2 and vals[-1] == vals[-2] if direction == "max" else len(vals) >= 2 and vals[0] == vals[1]
    answer = pick
    if tie:
        same = [o for o, sf in zip(node.options, surfaces(node)) if o not in facts and any(w in sf.casefold() for w in ("same", "igua", "mesm"))]
        answer = same[0] if same else pick
    return {"type": "distribution", "decision": "CHOICE", "answer": answer, "probability": 1.0, "grounded": True,
            "grounding": "deterministic_compare", "engine": "deterministic",
            "evidence": {"facts": facts, "operation": f"arg{direction}"},
            "distribution": {o: (1.0 if o == answer else 0.0) for o in node.options}}


def rule(node, source):
    """Deterministic grounding rules evaluated before the model (see docs/DIAGNOSIS.md)."""
    sf = surfaces(node)
    r = recency_choice(source, node.ask, sf)
    if r:
        r["option"] = _label(node, r["option"])
        return {"type": "distribution", "decision": "CHOICE", "answer": r["option"], "probability": None,
                "grounded": True, "grounding": "rule_recency", "engine": "deterministic", "evidence": r,
                "distribution": {o: (1.0 if o == r["option"] else 0.0) for o in node.options}}
    r = transfer_choice(source, node.ask, sf)
    if r:
        r["option"] = _label(node, r["option"])
        return {"type": "distribution", "decision": "CHOICE", "answer": r["option"], "probability": None,
                "grounded": True, "grounding": "rule_transfer", "engine": "deterministic", "evidence": r,
                "distribution": {o: (1.0 if o == r["option"] else 0.0) for o in node.options}}
    r = anchor_choice(source, node.ask, sf)
    if r:
        r["option"] = _label(node, r["option"])
        return {"type": "distribution", "decision": "CHOICE", "answer": r["option"], "probability": None,
                "grounded": True, "grounding": "rule_anchor", "engine": "deterministic", "evidence": r,
                "distribution": {o: (1.0 if o == r["option"] else 0.0) for o in node.options}}
    return None


def verify_choice(node, clf: dict, evidence_ms, ungrounded_abstain: float, source: str = "") -> dict:
    ev = [(m.text, m.probability) for m in evidence_ms.all_scored()]
    binds = role_bindings(source) if source else {}
    for t, p in list(ev):                                  # evidence "the courier" -> bound name "Omar"
        key = fold(re.sub(r"^(?:the|o|a|el|la)\s+", "", t.strip(), flags=re.I))
        if key in binds:
            ev.append((binds[key], p))
    scores = {o: support(sf, ev) for o, sf in zip(node.options, surfaces(node))}
    best = max(node.options, key=lambda o: (scores[o][0], clf["distribution"].get(o, 0.0)))
    s_best = scores[best][0]
    res = dict(clf)
    res["classifier_answer"] = clf["answer"]
    res["classifier_distribution"] = clf["distribution"]
    res["evidence"] = [{"text": m.text, "start": m.start, "end": m.end, "probability": round(m.probability, 6)}
                       for m in evidence_ms.all_scored()]
    runner = max((scores[o][0] for o in node.options if o != best), default=0.0)
    if s_best >= GROUND_MIN and runner >= GROUND_MIN and s_best < MARGIN * runner:
        res.update(answer=best, probability=round(s_best, 6), grounded=True, grounding="evidence_ambiguous",
                   support={o: round(v, 6) for o, (v, _) in scores.items()}, abstain=True, proposed_value=best)
        return res
    if s_best >= GROUND_MIN:
        tot = sum(v for v, _ in scores.values()) or 1.0
        res.update(answer=best, probability=round(s_best, 6), grounded=True, grounding="evidence",
                   support={o: round(v, 6) for o, (v, _) in scores.items()},
                   distribution={o: round(v / tot, 6) for o, (v, _) in scores.items()},
                   supporting_evidence=scores[best][1],
                   agrees_with_classifier=best == clf["answer"])
        res["confidence"] = round(choice_confidence(list(res["distribution"].values())), 6)
        res["abstain"] = False
    else:
        yesno = all(o.strip().casefold().rstrip(".") in YESNO for o in node.options)
        # no evidence span: answer only when the calibrated classifier is very sure (yes/no and entity choices alike);
        # the old "always abstain for non-yes/no" rule withheld obvious answers
        thr = ungrounded_abstain if yesno else max(ungrounded_abstain, 0.9)
        res.update(grounded=False, grounding="classifier_only",
                   abstain=clf.get("probability", 0.0) < thr, abstain_threshold=thr)
        if res["abstain"]:
            res["proposed_value"] = res["answer"]
    return res
