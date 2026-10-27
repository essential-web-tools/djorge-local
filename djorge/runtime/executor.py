"""Executor: parse -> plan -> resolve (deterministic inline, semantic batched) -> operations -> results."""
from __future__ import annotations

import os
import hashlib
import time
from typing import Any, Dict, Optional

from ..config import DjorgeConfig
from ..errors import DjorgeError
from ..lang import parse
from ..lang.ast import Decision, Query
from ..version import DSL_VERSION, RESULT_SCHEMA_VERSION, DJORGE_VERSION
from . import deterministic as D
from .context import EMPTY, ContextStore, Snapshot
from .planner import plan as make_plan
from .result import MatchSet, operation_result, render
from .safety import check_regex, check_size
from .semantic import SemanticEngine, SemReq
from . import structure as S
from . import grounding as ground


class Executor:
    def __init__(self, config: DjorgeConfig, semantic: Optional[SemanticEngine] = None,
                 contexts: Optional[ContextStore] = None):
        self.cfg, self.sem = config, semantic
        self.contexts = contexts
        self.counters = {"queries": 0, "deterministic_tasks": 0, "semantic_tasks": 0}

    def certainty_thresholds(self, certainty: float) -> Dict[str, float]:
        """Map a user target accuracy (e.g. 0.95) to per-mode abstention thresholds measured on the calibration
        split. Unreachable targets fail closed (threshold 1.01 -> always abstain), never open."""
        table = (self.sem.meta.get("certainty") if self.sem else None) or {}
        out = {}
        for mode, row in table.items():
            ts = sorted((float(t), v) for t, v in row.items())
            pick = next((v for t, v in ts if t >= certainty), None)
            out[mode] = 1.01 if pick is None else float(pick)
        return out

    def execute(self, query: str, source: Optional[str] = None, sources: Optional[Dict[str, str]] = None,
                tenant: Optional[str] = None, context_version: Optional[int] = None,
                diagnostics: bool = False, certainty: Optional[float] = None,
                specialist: Optional[dict] = None) -> Dict[str, Any]:
        T: Dict[str, float] = {}
        t_all = time.perf_counter()
        lim = self.cfg.limits
        check_size("query", query, lim.max_query_chars)
        t = time.perf_counter()
        prog = parse(query)
        T["parse_ms"] = (time.perf_counter() - t) * 1e3
        if len(prog.statements) > lim.max_statements:
            raise DjorgeError("RESOURCE_LIMIT", f"at most {lim.max_statements} statements per request")
        t = time.perf_counter()
        pl = make_plan(prog, lim.max_options)
        T["plan_ms"] = (time.perf_counter() - t) * 1e3
        ctx: Snapshot = self.contexts.get(tenant, context_version) if (tenant and self.contexts) else EMPTY
        env = dict(sources or {})
        if source is not None:
            env.setdefault("source", source)

        def bind(src) -> str:
            if src.literal is not None:
                txt = src.literal
            elif src.name in env:
                txt = env[src.name]
            else:
                raise DjorgeError("UNBOUND_SOURCE", f"source {src.name!r} was not provided",
                                {"provided": sorted(env)})
            if not isinstance(txt, str):
                raise DjorgeError("INVALID_REQUEST", f"source {src.name!r} must be a string")
            check_size("source", txt, lim.max_source_chars)
            return txt

        th = self.cfg.thresholds
        if certainty is None:
            certainty = getattr(self.cfg, "certainty", None)
        if certainty is not None:
            import copy
            th = copy.deepcopy(th)
            ct = self.certainty_thresholds(certainty)
            for mode in ("CLASS", "ENTITY", "ANSWER"):
                if mode in ct:
                    th.abstain[mode] = ct[mode]
            dec = [ct[k] for k in ("CHOICE", "SCORE", "NOUL") if k in ct]
            th.abstain["DECISION"] = max(dec) if dec else 1.01
            th.abstain["UNGROUNDED"] = max(th.abstain.get("UNGROUNDED", 0.9), th.abstain["DECISION"])
        resolved: Dict[str, Any] = {}
        sem_reqs, sem_keys = [], []
        t = time.perf_counter()
        for key, task in pl.tasks.items():
            node = task.node
            text = bind(node.source)
            if isinstance(node, Decision):
                try:                                             # clock tool: deadlines / date comparisons
                    from . import temporal as _tmp
                    tr = _tmp.decide(node, text, _tmp.now())
                except Exception:
                    self.counters["rule_errors"] = self.counters.get("rule_errors", 0) + 1
                    tr = None
                if tr:
                    resolved[key] = tr
                    self.counters["deterministic_tasks"] += 1
                    continue
                try:
                    extractive = node.kind == "CHOICE" and node.ask and ground.is_extractive(node, text)
                except Exception:
                    extractive = False
                if extractive:
                    try:                                         # rules are an accelerator: a rule bug must never
                        cmp = S.comparative(node.ask)            # block the answer -> fall back to the model
                        facts = S.entity_facts(text, ground.surfaces(node)) if cmp else {}
                        rr = ground.compare(node, cmp, facts) if (cmp and len(facts) >= 2) else ground.rule(node, text)
                    except Exception:
                        self.counters["rule_errors"] = self.counters.get("rule_errors", 0) + 1
                        rr = None
                    if rr:
                        resolved[key] = rr
                        self.counters["deterministic_tasks"] += 1
                        continue
                    sem_reqs.append(SemReq("answer", text, target=node.ask, threshold=th.accept.get("ANSWER", 0.5)))
                    sem_keys.append(key + ":evidence")
                sem_reqs.append(SemReq(node.kind, text, options=node.options, descriptions=node.descriptions,
                                       ask=node.ask))
                sem_keys.append(key)
                continue
            r = node.resolution
            mode = r.mode
            if mode == "AUTO":                                   # documented, deterministic-first rule
                if r.target.lower() in D.TYPE_NAMES:
                    mode = "TYPE"
                else:
                    ms = D.resolve_id(text, r.target, r.exact, ctx.aliases(r.target))
                    mode = "ID" if ms.matches else "CLASS"
                    if mode == "ID":
                        ms.mode, ms.resolved_mode = "AUTO", "ID"
                        resolved[key] = ms
                        continue
            if mode == "AGID":
                from . import agg as _agg
                resolved[key] = _agg.resolve_agid(text, r.target, r.exact, ctx.aliases(r.target))
            elif mode == "ID":
                resolved[key] = D.resolve_id(text, r.target, r.exact, ctx.aliases(r.target))
            elif mode == "TYPE":
                resolved[key] = D.resolve_type(text, r.target)
            elif mode == "REGEX":
                resolved[key] = D.resolve_regex(text, check_regex(r.target, lim.max_regex_chars), r.target)
            elif mode == "ANSWER":
                acc = node.threshold if node.threshold is not None else th.accept.get("ANSWER", 0.5)
                sem_reqs.append(SemReq("answer", text, target=r.target, threshold=acc))
                sem_keys.append(key)
            else:                                                # CLASS / ENTITY / AGG -> Qwen
                if mode == "AGG":
                    mode = "CLASS"                               # AGG selects exactly like CLASS (+ quantities)
                acc = node.threshold if node.threshold is not None else ctx.threshold(
                    mode, "accept", th.accept.get(mode, 0.5))
                sem_reqs.append(SemReq("resolve", text, mode=mode, target=r.target,
                                       definition=r.definition or ctx.class_def(r.target), threshold=acc))
                sem_keys.append(key)
                if r.mode == "AUTO":
                    resolved[key + ":auto"] = True
            if key in resolved:
                self.counters["deterministic_tasks"] += 1
                if r.mode == "AUTO":
                    resolved[key].mode, resolved[key].resolved_mode = "AUTO", mode
        T["deterministic_ms"] = (time.perf_counter() - t) * 1e3
        if sem_reqs:
            if self.sem is None:
                raise DjorgeError("MODEL_UNAVAILABLE", "this query needs the semantic engine (Qwen-derived model) "
                                "but no DJORGE artifact is loaded", {"hint": "Djorge(artifact=...) or DJORGE_ARTIFACT"})
            adapter = None
            if specialist:                                   # explicit per-request specialist LoRA
                self.sem.ensure_adapter(specialist)
                adapter = specialist["name"]
            elif ctx.active_adapter:
                self.sem.ensure_adapter(ctx.active_adapter)
                adapter = ctx.active_adapter["name"]
            outs = self.sem.run(sem_reqs, tenant_adapter=adapter, timings=T)
            self.counters["semantic_tasks"] += len(sem_reqs)
            for k, o in zip(sem_keys, outs):
                if isinstance(o, MatchSet) and resolved.pop(k + ":auto", False):
                    o.resolved_mode, o.mode = o.mode, "AUTO"
                resolved[k] = o
        results, surface = {}, {}
        for sp in pl.statements:
            out = resolved[sp.task_key]
            node = sp.node
            if isinstance(node, Query):
                mode = out.resolved_mode or out.mode
                thr = th.abstain.get(mode) if out.engine == "semantic" else None
                if out.engine == "semantic":
                    thr = ctx.threshold(mode, "abstain", thr)
                if node.resolution.mode in ("AGG", "AGID"):   # selectors with quantities (djorge/runtime/agg.py)
                    from . import agg as _agg
                    how = os.environ.get("DJORGE_AGG_ANCHOR", "0")     # 0 (default) | noul | choice | add
                    how = {"1": "noul"}.get(how, how)
                    if node.resolution.mode == "AGG" and out.engine == "semantic" and self.sem is not None \
                            and how != "0":
                        out = _agg.verify_class(self.sem, bind(node.source), out, out.target, how=how)
                    res = _agg.selector_result(node.operation, bind(node.source), out, node.resolution.mode,
                                               node.at, thr, diagnostics, node.mentions)
                else:
                    res = operation_result(node.operation, out, node.at, thr, diagnostics, node.mentions)
            elif isinstance(out, dict) and out.get("grounding") in ("deterministic_compare", "rule_recency", "rule_anchor", "temporal_rule"):
                res = dict(out)
            else:
                res = dict(out)
                if node.kind == "CHOICE" and (sp.task_key + ":evidence") in resolved:
                    try:
                        res = ground.verify_choice(node, res, resolved[sp.task_key + ":evidence"],
                                                   th.abstain.get("UNGROUNDED", 0.9), bind(node.source))
                    except Exception:                        # verification bug -> keep the calibrated classifier
                        self.counters["rule_errors"] = self.counters.get("rule_errors", 0) + 1
                        res["grounding"] = "classifier"
                if node.kind == "CHOICE" and "grounding" not in res:
                    res["grounding"] = "classifier"                      # classification: labels are not in the text
                thr = node.threshold if node.threshold is not None else (
                    th.abstain.get("DECISION") or (th.abstain.get("CLASSIFY") if res.get("grounding") == "classifier" else None))
                p_ = res.get("probability")
                if thr and isinstance(p_, (int, float)) and p_ < thr and not res.get("abstain"):
                    res["abstain"], res["proposed_value"], res["abstain_threshold"] = True, res.get("answer"), thr
                res.setdefault("abstain", False)
                res.setdefault("engine", "semantic")
                res["model"] = self.sem.model_id if self.sem else None
            results[sp.name] = res
            surface[sp.name] = render(res)
        T["total_ms"] = (time.perf_counter() - t_all) * 1e3
        self.counters["queries"] += 1
        meta = {"timings": {k: round(v, 3) for k, v in T.items()},
                "versions": {"djorge": DJORGE_VERSION, "dsl": DSL_VERSION, "result_schema": RESULT_SCHEMA_VERSION,
                             "model": (self.sem.meta.get("versions") if self.sem else None),
                             "context": ({"tenant": ctx.tenant, "version": ctx.version} if tenant else None)},
                "plan": pl.explain(),
                "semantic_forward_calls": (self.sem.stats["forward_calls"] if self.sem else 0)}
        if len(results) == 1 and "result" in results:
            return {"result": results["result"], "surface": surface["result"], "meta": meta}
        return {"results": results, "surface": surface, "meta": meta}
