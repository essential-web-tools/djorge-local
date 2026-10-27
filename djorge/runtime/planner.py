"""Type checker + planner.

Plan steps:
  1. type-check every statement against the registry (mode/operation/modifiers)
  2. bind sources; identical (mode, target, options, source) resolutions are
     deduplicated -> COUNT/REPORT/GET CLASS Fruit on one source = ONE resolution
  3. classify engines: deterministic tasks run inline, semantic tasks are
     collected into one batch for the semantic engine (one batched forward)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..errors import DjorgeError
from ..lang.ast import Decision, Program, Query, Resolution
from . import registry as REG
from .deterministic import TYPE_NAMES


@dataclass
class Task:
    key: str
    kind: str                     # resolution | decision
    engine: str                   # deterministic | semantic | dynamic
    node: object                  # Resolution or Decision
    source_name: str              # literal key or bound name


@dataclass
class StatementPlan:
    name: str
    node: object
    out_type: str
    engine: str
    task_key: str


@dataclass
class Plan:
    statements: List[StatementPlan]
    tasks: Dict[str, Task]

    def explain(self) -> List[dict]:
        out = []
        for s in self.statements:
            t = self.tasks[s.task_key]
            out.append({"name": s.name, "type": s.out_type, "engine": s.engine, "task": s.task_key[:12],
                        "shared_with": [x.name for x in self.statements if x.task_key == s.task_key and x.name != s.name]})
        return out

    @property
    def semantic_tasks(self):
        return [t for t in self.tasks.values() if t.engine == "semantic"]


def _src_key(src) -> str:
    return ("lit:" + hashlib.sha1(src.literal.encode()).hexdigest()) if src.literal is not None else "var:" + src.name


def typecheck_query(q: Query):
    mode = q.resolution.mode
    if mode not in REG.MODES:
        raise DjorgeError("UNSUPPORTED_MODE", f"unknown mode {mode}", {"modes": sorted(REG.MODES)})
    if mode not in REG.SUPPORT.get(q.operation, set()):
        raise DjorgeError("UNSUPPORTED_MODE", f"unsupported combination {q.operation} + {mode}",
                        {"supported_modes": sorted(REG.SUPPORT.get(q.operation, set()))})
    spec = REG.MODES[mode]
    used = {k for k, v in (("AT", q.at), ("THRESHOLD", q.threshold), ("DEFINE", q.resolution.definition),
                           ("EXACT", q.resolution.exact or None), ("MENTIONS", q.mentions or None)) if v is not None}
    for m in used - spec.accepts:
        raise DjorgeError("TYPE_ERROR", f"{m} is not valid with {mode} (allowed: {sorted(spec.accepts) or 'none'})")
    if q.at is not None and q.operation != "GET":
        raise DjorgeError("TYPE_ERROR", f"AT selects one match and is only valid with GET, not {q.operation}")
    if mode == "TYPE" and q.resolution.target.lower() not in TYPE_NAMES:
        raise DjorgeError("TYPE_ERROR", f"unknown TYPE {q.resolution.target!r}", {"types": TYPE_NAMES})
    if mode in ("CLASS", "ENTITY", "ID") and not q.resolution.target.strip():
        raise DjorgeError("TYPE_ERROR", f"{mode} needs a non-empty target")


def typecheck_decision(d: Decision, max_options: int):
    if d.kind not in REG.DECISIONS:
        raise DjorgeError("UNSUPPORTED_OPERATION", f"unknown decision {d.kind}")
    n = len(d.options)
    need = 1 if d.kind in ("NOUL", "SET") else 2
    if n < need:
        raise DjorgeError("TYPE_ERROR", f"{d.kind} needs at least {need} options")
    if n > max_options:
        raise DjorgeError("RESOURCE_LIMIT", f"{d.kind} supports at most {max_options} options")
    if len(set(d.options)) != n:
        raise DjorgeError("TYPE_ERROR", f"{d.kind} options must be unique")


def plan(program: Program, max_options: int = 255) -> Plan:
    tasks: Dict[str, Task] = {}
    stmts: List[StatementPlan] = []
    for st in program.statements:
        e = st.expr
        if isinstance(e, Query):
            typecheck_query(e)
            r = e.resolution
            key = hashlib.sha1(repr(("R", r.mode, r.target, r.flags, r.definition, r.exact, e.threshold,
                                     _src_key(e.source))).encode()).hexdigest()
            engine = REG.engine_for(e.operation, r.mode)
            tasks.setdefault(key, Task(key, "resolution", engine, e, _src_key(e.source)))
            stmts.append(StatementPlan(st.name, e, REG.OPERATIONS[e.operation], engine, key))
        else:
            typecheck_decision(e, max_options)
            key = hashlib.sha1(repr(("D", e.kind, e.options, e.descriptions, e.ask, _src_key(e.source))).encode()).hexdigest()
            tasks.setdefault(key, Task(key, "decision", "semantic", e, _src_key(e.source)))
            stmts.append(StatementPlan(st.name, e, REG.DECISIONS[e.kind], "semantic", key))
    return Plan(stmts, tasks)
