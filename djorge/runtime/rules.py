"""Composite decisions: several conditions, several actions, explicit priority, dependencies and conflicts.

The MODEL answers one question per condition, all at once (multi-label SET head, one independent calibrated
probability per condition). Everything after that is DETERMINISTIC and auditable:

    rules = [
      {"id": "amor",    "when": {"label": "amor", "description": "fala de sentimentos ou relacionamento"},
       "action": "operacao_x", "priority": 1},
      {"id": "suporte", "when": {"label": "suporte técnico", "description": "pede ajuda com um problema técnico"},
       "action": "operacao_y", "priority": 2},
      {"id": "ambos",   "when": {"all": ["amor", "suporte"]}, "action": "operacao_xy", "priority": 3,
       "excludes": ["amor", "suporte"]},
      {"id": "nenhum",  "when": {"not": {"any": ["amor", "suporte"]}}, "action": "triagem", "priority": 0},
    ]

Condition forms:  {"label": str, "description"?: str, "min_p"?: float}   a question for the model
                  "rule_id" / {"rule": id}                                  true when that rule fired
                  {"all": [...]} | {"any": [...]} | {"not": cond}
Rule fields:      id, when, action, priority (higher wins conflicts), depends_on [ids], excludes [ids]
Resolution:       1) evaluate conditions  2) drop rules whose dependencies did not fire
                  3) conflicts: a fired rule with `excludes` removes the listed rules when its priority is higher;
                     equal priority -> both kept and the conflict is reported as `needs_clarification`
Nothing is executed here: the output is the list of actions to perform, with the trace that justifies each one.
"""
from __future__ import annotations

from typing import Dict, List, Optional

MAX_RULES, MAX_LABELS, MAX_DEPTH = 64, 32, 6


class RuleError(ValueError):
    pass


def _labels(cond, out: Dict[str, str], depth=0):
    if depth > MAX_DEPTH:
        raise RuleError("condition nested too deeply")
    if isinstance(cond, str):
        return
    if not isinstance(cond, dict):
        raise RuleError("a condition must be an object or a rule id")
    if "label" in cond:
        lab = cond["label"]
        if not isinstance(lab, str) or not lab.strip():
            raise RuleError("'label' must be a non-empty string")
        out.setdefault(lab.strip(), str(cond.get("description") or "").strip())
    elif "all" in cond or "any" in cond:
        items = cond.get("all", cond.get("any"))
        if not isinstance(items, list) or not items:
            raise RuleError("'all'/'any' need a non-empty list")
        for c in items:
            _labels(c, out, depth + 1)
    elif "not" in cond:
        _labels(cond["not"], out, depth + 1)
    elif "rule" in cond:
        pass
    else:
        raise RuleError(f"unknown condition {sorted(cond)}")


def validate(rules: List[dict]) -> Dict[str, str]:
    """Checks the rule set and returns {label: description} for the model."""
    if not isinstance(rules, list) or not rules:
        raise RuleError("'rules' must be a non-empty list")
    if len(rules) > MAX_RULES:
        raise RuleError(f"at most {MAX_RULES} rules")
    ids = set()
    for r in rules:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or "when" not in r:
            raise RuleError("every rule needs 'id' (string) and 'when'")
        if r["id"] in ids:
            raise RuleError(f"duplicate rule id {r['id']!r}")
        ids.add(r["id"])
        if not isinstance(r.get("action", ""), str):
            raise RuleError("'action' must be a string")
    for r in rules:
        for k in ("depends_on", "excludes"):
            for d in r.get(k, []) or []:
                if d not in ids:
                    raise RuleError(f"rule {r['id']!r}: {k} references unknown rule {d!r}")
    labels: Dict[str, str] = {}
    for r in rules:
        _labels(r["when"], labels)
    if len(labels) > MAX_LABELS:
        raise RuleError(f"at most {MAX_LABELS} distinct labels")
    _order(rules)                                         # raises on cycles
    return labels


def _order(rules):
    """Topological order over rule references (depends_on + rule conditions); raises on cycles."""
    deps = {r["id"]: set(r.get("depends_on", []) or []) | _rule_refs(r["when"]) for r in rules}
    order, temp, done = [], set(), set()

    def visit(n):
        if n in done:
            return
        if n in temp:
            raise RuleError(f"cycle between rules involving {n!r}")
        temp.add(n)
        for d in deps.get(n, ()):
            if d in deps:
                visit(d)
        temp.discard(n); done.add(n); order.append(n)
    for r in rules:
        visit(r["id"])
    return order


def _rule_refs(cond) -> set:
    if isinstance(cond, str):
        return {cond}
    if not isinstance(cond, dict):
        return set()
    if "rule" in cond:
        return {cond["rule"]}
    if "all" in cond or "any" in cond:
        return set().union(*[_rule_refs(c) for c in cond.get("all", cond.get("any"))])
    if "not" in cond:
        return _rule_refs(cond["not"])
    return set()


def _eval(cond, probs: Dict[str, float], fired: Dict[str, bool], default_p: float, trace: list):
    if isinstance(cond, str) or (isinstance(cond, dict) and "rule" in cond):
        rid = cond if isinstance(cond, str) else cond["rule"]
        if rid in fired:
            return fired[rid]
        p = probs.get(rid)                               # a bare string may also be a label
        return p is not None and p >= default_p
    if "label" in cond:
        p = probs.get(cond["label"].strip(), 0.0)
        thr = float(cond.get("min_p", default_p))
        ok = p >= thr
        trace.append({"label": cond["label"], "probability": round(p, 4), "threshold": thr, "true": ok})
        return ok
    if "all" in cond:
        return all(_eval(c, probs, fired, default_p, trace) for c in cond["all"])
    if "any" in cond:
        return any(_eval(c, probs, fired, default_p, trace) for c in cond["any"])
    if "not" in cond:
        return not _eval(cond["not"], probs, fired, default_p, trace)
    return False


def decide(rules: List[dict], probs: Dict[str, float], default_p: float = 0.5) -> dict:
    """probs: {label: calibrated probability} from the SET head. Returns actions + full trace."""
    validate(rules)
    by_id = {r["id"]: r for r in rules}
    fired: Dict[str, bool] = {}
    traces: Dict[str, list] = {}
    for rid in _order(rules):
        r = by_id[rid]
        t: list = []
        ok = _eval(r["when"], probs, fired, default_p, t)
        miss = [d for d in r.get("depends_on", []) or [] if not fired.get(d)]
        if ok and miss:
            t.append({"dependency_not_met": miss}); ok = False
        fired[rid] = ok
        traces[rid] = t
    active = {rid for rid, v in fired.items() if v}
    removed, conflicts = {}, []
    for rid in sorted(active, key=lambda x: -int(by_id[x].get("priority", 0))):
        if rid in removed:
            continue
        for ex in by_id[rid].get("excludes", []) or []:
            if ex in active and ex not in removed:
                pa, pb = int(by_id[rid].get("priority", 0)), int(by_id[ex].get("priority", 0))
                if pa > pb:
                    removed[ex] = rid
                elif pa == pb:
                    conflicts.append({"rules": [rid, ex], "reason": "mesma prioridade"})
    final = [rid for rid in sorted(active, key=lambda x: (-int(by_id[x].get("priority", 0)), x)) if rid not in removed]
    return {
        "actions": [{"rule": rid, "action": by_id[rid].get("action"), "priority": int(by_id[rid].get("priority", 0))}
                    for rid in final],
        "fired": sorted(active), "overridden": [{"rule": k, "by": v} for k, v in removed.items()],
        "conflicts": conflicts, "needs_clarification": bool(conflicts),
        "labels": {k: round(v, 4) for k, v in probs.items()},
        "trace": {rid: traces[rid] for rid in by_id},
        "executed": False,                                 # decisions only: execution is the caller's job
    }


def route(entities: List[dict], probs: Dict[str, float], min_p: float = 0.5) -> dict:
    """Recipient suggestion from registered entities (context records of type 'entity').
    Suggests, never sends: delivery requires an explicitly configured integration and confirmation."""
    ranked = sorted(((e["name"], probs.get(e["name"], 0.0)) for e in entities), key=lambda x: -x[1])
    best = [n for n, p in ranked if p >= min_p]
    return {"suggested": best[:1], "ranking": [{"name": n, "probability": round(p, 4)} for n, p in ranked],
            "sent": False, "note": "sugestão de destinatário; nenhum envio é feito pelo Orego"}
