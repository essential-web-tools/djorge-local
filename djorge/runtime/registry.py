"""Capability registry: the single source of truth for which operation x mode
combinations exist and which engine executes them. /capabilities, the type
checker, the planner and the docs table are all generated from here.

Adding a resolution mode = one `register_mode(...)` call plus a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

OPERATIONS: Dict[str, str] = {          # operation -> output type
    "MATCH": "match_set", "GET": "match", "COUNT": "integer", "REPORT": "report", "EXISTS": "boolean",
    "TOTAL": "number",
}
DECISIONS: Dict[str, str] = {           # decision -> output type
    "CHOICE": "distribution", "SCORE": "ordinal", "NOUL": "distribution", "RANK": "ranking", "SET": "set",
}


@dataclass(frozen=True)
class ModeSpec:
    name: str
    engine: str          # deterministic | semantic | dynamic
    meaning: str
    accepts: frozenset   # modifiers allowed with this mode


MODES: Dict[str, ModeSpec] = {}
SUPPORT: Dict[str, set] = {op: set() for op in OPERATIONS}


def register_mode(name, engine, meaning, accepts=(), operations=tuple(OPERATIONS)):
    MODES[name] = ModeSpec(name, engine, meaning, frozenset(accepts))
    for op in operations:
        SUPPORT[op].add(name)


register_mode("ID", "deterministic", "exact / near-exact identity of the target string", {"EXACT", "AT"},
              operations=("MATCH", "GET", "COUNT", "REPORT", "EXISTS"))
register_mode("TYPE", "deterministic", "spans of a built-in data type (number, email, date, ...)", {"AT"},
              operations=("MATCH", "GET", "COUNT", "REPORT", "EXISTS", "TOTAL"))
register_mode("REGEX", "deterministic", "spans matching a (safety-checked) regular expression", {"AT"},
              operations=("MATCH", "GET", "COUNT", "REPORT", "EXISTS"))
register_mode("CLASS", "semantic", "mentions of the semantic concept named by the target (with polarity + quantity)",
              {"DEFINE", "THRESHOLD", "AT", "MENTIONS"})
register_mode("AGG", "semantic", "CLASS selector + the quantity of each mention (number, dúzia, kg, R$, ranges, "
              "approximations, negation, repeated references); TOTAL sums by kind", {"DEFINE", "THRESHOLD", "AT"})
register_mode("AGID", "deterministic", "ID selector (with singular/plural variations) + the quantity of each mention",
              {"EXACT", "AT"})
register_mode("ENTITY", "semantic", "named-entity mentions of the given type (Person, Location, ...)",
              {"DEFINE", "THRESHOLD", "AT", "MENTIONS"})
register_mode("ANSWER", "semantic", "evidence spans in the source that answer the target question (grounded extraction)",
              {"THRESHOLD", "AT"}, operations=("MATCH", "GET", "REPORT", "EXISTS", "COUNT"))
register_mode("AUTO", "dynamic",
              "TYPE if the target names a type; else ID if it occurs; else CLASS (the resolved mode is reported)",
              {"EXACT", "DEFINE", "THRESHOLD", "AT", "MENTIONS"},
              operations=("MATCH", "GET", "COUNT", "REPORT", "EXISTS", "TOTAL"))


def engine_for(op: str, mode: str) -> str:
    return MODES[mode].engine


def capabilities() -> dict:
    return {
        "operations": {op: sorted(SUPPORT[op]) for op in OPERATIONS},
        "operation_types": OPERATIONS,
        "modes": {m: {"engine": s.engine, "meaning": s.meaning, "modifiers": sorted(s.accepts)} for m, s in MODES.items()},
        "decisions": {d: {"engine": "semantic", "output": t} for d, t in DECISIONS.items()},
        "matrix": {op: {m: (MODES[m].engine if m in SUPPORT[op] else None) for m in MODES} for op in OPERATIONS},
    }


def matrix_markdown() -> str:
    modes = list(MODES)
    lines = ["| Operation | " + " | ".join(modes) + " |", "|" + "---|" * (len(modes) + 1)]
    for op in OPERATIONS:
        lines.append(f"| {op} | " + " | ".join(MODES[m].engine if m in SUPPORT[op] else "-" for m in modes) + " |")
    return "\n".join(lines)
