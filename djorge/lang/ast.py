"""DJORGE AST: explicit, serializable, engine-independent.

The two orthogonal dimensions are first-class nodes:
  Operation  - what happens to resolved information  (GET COUNT REPORT EXISTS MATCH)
  Resolution - how the target is found               (ID CLASS TYPE REGEX ENTITY AUTO)
Decisions (CHOICE SCORE NOUL RANK SET) are a separate node family.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple, Union

__all__ = ["Source", "Resolution", "Query", "Decision", "Statement", "Program"]


@dataclass(frozen=True)
class Source:
    literal: Optional[str] = None           # FROM "text"
    name: Optional[str] = None              # FROM source   (bound at execution time)


@dataclass(frozen=True)
class Resolution:
    mode: str                               # ID | CLASS | TYPE | REGEX | ENTITY | AUTO
    target: str
    flags: str = ""                         # regex flags
    definition: Optional[str] = None        # inline DEFINE "..." (CLASS/ENTITY)
    exact: bool = False                     # ID: case-sensitive substring identity


@dataclass(frozen=True)
class Query:
    operation: str                          # MATCH (bare resolution) | GET | COUNT | REPORT | EXISTS
    resolution: Resolution
    source: Source
    at: Optional[int] = None                # GET ... AT n  (1-based; negative from end)
    threshold: Optional[float] = None       # per-statement acceptance threshold override
    mentions: bool = False                  # MENTIONS: operate on all mentions (incl. negated / non-factual)


@dataclass(frozen=True)
class Decision:
    kind: str                               # CHOICE | SCORE | NOUL | RANK | SET
    options: Tuple[str, ...]                # labels (NOUL: the proposition is options[0])
    descriptions: Tuple[str, ...]           # parallel to options ("" when absent)
    source: Source
    ask: str = ""
    threshold: Optional[float] = None


@dataclass(frozen=True)
class Statement:
    name: str
    expr: Union[Query, Decision]


@dataclass(frozen=True)
class Program:
    statements: Tuple[Statement, ...]

    def to_dict(self) -> dict:
        out = []
        for s in self.statements:
            d = asdict(s.expr)
            d["node"] = type(s.expr).__name__
            out.append({"name": s.name, "expr": d})
        return {"statements": out}
