"""Deterministic resolution providers (no model, exact, microseconds).

ID     token-aligned identity: case-folded, NFKC, plural/possessive-tolerant
       (EXACT -> case-sensitive substring). Context aliases expand the identity.
TYPE   typed patterns: number word sentence line char letter email url money
       percent date time phone hashtag mention ip uuid
REGEX  user pattern after safety checks
"""
from __future__ import annotations

import re

from typing import List, Sequence

from . import text as T
from .result import Match, MatchSet

TYPE_NAMES = sorted(set(T.PATTERNS) | {"number", "word", "sentence", "line", "char", "letter"})


def _matches(spans, mode, target, attrs=None) -> List[Match]:
    out = []
    for s in spans:
        a = {"value": s.value} if s.value is not None else {}
        out.append(Match(s.text, s.start, s.end, 1.0, "deterministic", mode, target, attributes=a))
    return out


def resolve_id(source: str, target: str, exact: bool = False, aliases: Sequence[str] = ()) -> MatchSet:
    spans = list(T.find_id(source, target, exact))
    for a in aliases:
        spans += T.find_id(source, a, exact)
    seen, uniq = set(), []
    for s in sorted(spans, key=lambda s: (s.start, -s.end)):
        if any(a < s.end and s.start < b for a, b in seen):
            continue
        seen.add((s.start, s.end))
        uniq.append(s)
    return MatchSet("ID", target, "deterministic", _matches(uniq, "ID", target))


def resolve_type(source: str, target: str) -> MatchSet:
    ms = MatchSet("TYPE", target, "deterministic", _matches(T.find_kind(source, target.lower()), "TYPE", target))
    if target.lower() in ("money", "number", "percent"):   # numeric value for TOTAL (pt and en formats)
        from .agg import _money
        for m in ms.matches:
            if "value" in m.attributes:
                continue
            raw = re.sub(r"[^\d.,-]", "", m.text)
            try:
                m.attributes["value"] = _money(raw) if "," in raw or re.search(r"\.\d{3}\b", raw) else float(raw)
            except ValueError:
                pass
    if target.lower() in ("date", "time"):              # clock tool: ISO value, weekday, days from now, relation
        try:
            from . import temporal as TM
            ref = TM.now()
            for m in ms.matches:
                ds = TM.find_dates(source[max(0, m.start - 4):m.end + 12], ref)
                if ds:
                    rel = TM.relation(ds[0], ref)
                    m.attributes.update({k: rel[k] for k in ("date", "weekday", "days_from_now", "relation")
                                         if k in rel})
        except Exception:
            pass
    return ms


def resolve_regex(source: str, compiled, target: str) -> MatchSet:
    spans = [T.Span(m.group(), m.start(), m.end()) for m in compiled.finditer(source) if m.end() > m.start()]
    return MatchSet("REGEX", target, "deterministic", _matches(spans, "REGEX", target))
