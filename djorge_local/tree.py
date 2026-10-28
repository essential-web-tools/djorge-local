"""Decision trees: each event walks a tree of decisions; each answer chooses the next decisions (or actions).

Node kinds (one key decides the kind):
  choice:  [opções]  (+ ask)          -> CHOICE  ; branches by option
  check:   "afirmação"                -> NOUL    ; branches true / false
  score:   [escala]  (+ ask)          -> SCORE   ; branches by label or ">= 2" on the index
  multi:   [rótulos] (+ ask)          -> SET     ; every selected label's branch runs (several follow-ups)
  select:  "TOTAL AGID limão"         -> any selector (FROM text is added); branches like ">= 10", "== 0"
  query:   "<DSL completo>"           -> raw DJORGE query (use FROM text)
  if:      {contains_any, contains_all, regex, source_in, app_in, sender_has, seguranca_in}   -> branches true / false
  action:  notify | log | write_file | webhook | command | stop   (see actions.py)

Common keys: model (origo | flash), min_probability (below it -> branch 'abstain'), branches {answer: next},
then (next for actions), next can be a node id or a list of ids (all run, in order).
Every event is checked first (security.py): {seguranca} = seguro|spam|golpe|phishing|malware, {seguranca_p}.
Text placeholders in strings: {text} {title} {source} {app} {sender} {time} {<node>.answer} {<node>.probability}
{<node>.value}; {text:80} truncates. Placeholders are plain substitution (no code is evaluated).
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List

PH = re.compile(r"\{([a-zA-Z_][\w]*(?:\.[a-zA-Z_]\w*)?)(?::(\d{1,5}))?\}")
CMP = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:[.,]\d+)?)\s*$")


class TreeError(Exception):
    pass


def render(s: Any, ctx: Dict) -> Any:
    if not isinstance(s, str):
        return s

    def sub(m):
        key, n = m.group(1), m.group(2)
        if "." in key:
            node, f = key.split(".", 1)
            v = (ctx["results"].get(node) or {}).get(f, "")
        else:
            v = ctx["event"].get(key, "")
        v = "" if v is None else str(v)
        return v[: int(n)] + ("…" if n and len(v) > int(n) else "") if n else v
    return PH.sub(sub, s)


def validate(trees: List[dict]) -> List[dict]:
    names = set()
    for t in trees:
        if not t.get("name") or t["name"] in names:
            raise TreeError("cada árvore precisa de um 'name' único")
        names.add(t["name"])
        nodes = t.get("nodes") or {}
        if t.get("start") not in nodes:
            raise TreeError(f"{t['name']}: 'start' não existe em nodes")
        for nid, n in nodes.items():
            for nxt in _nexts(n):
                if nxt not in nodes:
                    raise TreeError(f"{t['name']}.{nid}: próximo nó inexistente '{nxt}'")
    return trees


def _nexts(n):
    out = []
    for v in list((n.get("branches") or {}).values()) + [n.get("then")]:
        if v is None:
            continue
        out += v if isinstance(v, list) else [v]
    return out


def matches(tree: dict, ev: dict) -> bool:
    w = tree.get("when") or {}
    if w.get("source_in") and ev.get("source") not in w["source_in"]:
        return False
    if w.get("app_in") and not any(a.casefold() in (ev.get("app") or "").casefold() for a in w["app_in"]):
        return False
    return _cond(w, ev) if any(k in w for k in ("contains_any", "contains_all", "regex", "sender_has",
                                                "seguranca_in")) else True


def _cond(c: dict, ev: dict) -> bool:
    t = f"{ev.get('title', '')}\n{ev.get('text', '')}".casefold()
    ok = True
    if c.get("contains_any"):
        ok &= any(x.casefold() in t for x in c["contains_any"])
    if c.get("contains_all"):
        ok &= all(x.casefold() in t for x in c["contains_all"])
    if c.get("regex"):
        if len(c["regex"]) > 300:
            raise TreeError("regex longa demais")
        ok &= re.search(c["regex"], t, re.I) is not None
    if c.get("sender_has"):
        ok &= any(x.casefold() in (ev.get("sender") or "").casefold() for x in c["sender_has"])
    if c.get("source_in"):
        ok &= ev.get("source") in c["source_in"]
    if c.get("seguranca_in"):                       # verdict of the automatic security check
        ok &= str(ev.get("seguranca") or "").casefold() in [str(x).casefold() for x in c["seguranca_in"]]
    if c.get("app_in"):
        ok &= any(a.casefold() in (ev.get("app") or "").casefold() for a in c["app_in"])
    return bool(ok)


def _quote(s):
    from djorge.lang.quote import quote
    return quote(s)


def build_query(n: dict) -> str:
    ask = f" ASK {_quote(n['ask'])}" if n.get("ask") else ""
    lst = lambda xs: "[" + ", ".join(_quote(x) for x in xs) + "]"
    if "choice" in n:
        return f"CHOICE {lst(n['choice'])} FROM text{ask}"
    if "check" in n:
        return f"NOUL {_quote(n['check'])} FROM text"
    if "score" in n:
        return f"SCORE {lst(n['score'])} FROM text{ask}"
    if "multi" in n:
        return f"SET {lst(n['multi'])} FROM text{ask}"
    if "select" in n:
        q = n["select"].strip()
        return q if re.search(r"\bFROM\b", q, re.I) else q + " FROM text"
    if "query" in n:
        return n["query"]
    raise TreeError("nó sem tipo")


def _branch(n: dict, res: dict) -> List[str]:
    br = {str(k).casefold(): v for k, v in (n.get("branches") or {}).items()}
    keys: List[str] = []
    if res.get("abstain"):
        keys = ["abstain"]
    elif "selected" in res:
        keys = [str(s).casefold() for s in res["selected"]] or ["none"]
    else:
        a = res.get("answer", res.get("value"))
        keys = [str(a).casefold()]
        if isinstance(res.get("answer"), int) and res.get("label") is not None:
            keys.insert(0, str(res["label"]).casefold())
        num = res.get("value") if isinstance(res.get("value"), (int, float)) else (
            res.get("answer") if isinstance(res.get("answer"), (int, float)) and not isinstance(res.get("answer"), bool)
            else None)
        if num is not None:
            for k in br:
                m = CMP.match(k)
                if m:
                    x = float(m.group(2).replace(",", "."))
                    if {">=": num >= x, "<=": num <= x, "==": num == x, "!=": num != x, ">": num > x,
                        "<": num < x}[m.group(1)]:
                        keys.append(k); break
    out = []
    for k in keys:
        if k in br:
            v = br[k]; out += v if isinstance(v, list) else [v]
    if not out and "default" in br and not (res.get("abstain") and "abstain" in br):
        v = br["default"]; out = v if isinstance(v, list) else [v]
    return out


class Runner:
    def __init__(self, cfg, models, actions):
        self.cfg, self.models, self.actions = cfg, models, actions

    def _decide(self, n: dict, ev: dict, ctx) -> dict:
        if "if" in n:
            return {"answer": _cond(n["if"], ev), "probability": 1.0, "engine": "regra"}
        model = n.get("model") or ([x for x in self.models.loaded() if x in self.models.decision_models()]
                                     or self.models.decision_models() or ["flash"])[0]
        z = self.models.get(model)
        q = render(build_query(n), ctx) if n.get("render_query") else build_query(n)
        r = z.execute(q, sources={"text": ev.get("text") or ""}, lang=self.cfg["language"])["result"]
        out = {k: r.get(k) for k in ("answer", "value", "label", "probability", "selected", "abstain", "grounding",
                                     "totals", "unit") if k in r}
        out["model"], out["query"] = model, q
        mp = n.get("min_probability")
        if mp is not None and isinstance(out.get("probability"), (int, float)) and out["probability"] < mp:
            out["abstain"] = True
        return out

    def run(self, tree: dict, ev: dict) -> dict:
        nodes = tree["nodes"]
        ctx = {"event": ev, "results": {}}
        trail, queue, steps = [], [tree["start"]], 0
        seen: Dict[str, int] = {}
        while queue:
            nid = queue.pop(0)
            steps += 1
            seen[nid] = seen.get(nid, 0) + 1
            if steps > self.cfg["max_steps"] or seen[nid] > 3:
                trail.append({"node": nid, "error": "limite de passos/ciclo atingido"}); break
            n = nodes[nid]
            t0 = time.perf_counter()
            if "action" in n:
                res = self.actions.run(n, ctx, tree["name"], nid)
                nxt = [] if n["action"] == "stop" else (n["then"] if isinstance(n.get("then"), list)
                                                        else ([n["then"]] if n.get("then") else []))
            else:
                try:
                    res = self._decide(n, ev, ctx)
                except Exception as e:
                    res = {"error": f"{type(e).__name__}: {e}", "abstain": True}
                ctx["results"][nid] = res
                nxt = _branch(n, res)
            res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            trail.append({"node": nid, **res, "next": nxt})
            if n.get("action") == "stop":
                break
            queue += nxt
        return {"tree": tree["name"], "trail": trail, "results": ctx["results"]}
