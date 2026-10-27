"""Span decoding: token scores -> word-snapped mention candidates.

Decoding is word-level so spans never cut a word ("maçãs", not "maç"):
  1. map each word (Unicode word regex; CJK split at token boundaries) to the
     source tokens overlapping it; word score = mean membership logit,
     word begin = begin logit of its first token
  2. adjacent candidate words separated only by whitespace/hyphen merge into one
     mention unless the next word has begin-probability > 0.5
  3. mention raw score = mean word logit (calibrated later)
"""
from __future__ import annotations

import math
import re
from typing import List, Sequence, Tuple

_WORD = re.compile(r"[^\W_]+(?:['\u2019\-][^\W_]+)*", re.UNICODE)
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _sig(x):
    return 1 / (1 + math.exp(-x)) if x > -60 else 0.0


def decode(source: str, offsets: Sequence[Tuple[int, int]], member: Sequence[float], begin: Sequence[float],
           min_p: float = 0.05) -> List[Tuple[int, int, float]]:
    units = []
    for m in _WORD.finditer(source):
        a, b = m.start(), m.end()
        toks = [i for i, (s, e) in enumerate(offsets) if e > s and s < b and a < e]
        if not toks:
            continue
        if _CJK.search(m.group()) and len(toks) > 1:          # no spaces: token = unit
            for i in toks:
                s, e = max(offsets[i][0], a), min(offsets[i][1], b)
                if e > s:
                    units.append((s, e, member[i], begin[i]))
        else:
            units.append((a, b, sum(member[i] for i in toks) / len(toks), begin[toks[0]]))
    out, cur = [], None
    for s, e, z, bz in units:
        if _sig(z) < min_p:
            if cur:
                out.append(cur); cur = None
            continue
        gap = source[cur[1]:s] if cur else ""
        if cur and gap.strip(" -") == "" and _sig(bz) <= 0.5:
            cur = (cur[0], e, cur[2] + [z])
        else:
            if cur:
                out.append(cur)
            cur = (s, e, [z])
    if cur:
        out.append(cur)
    return [(s, e, sum(zs) / len(zs)) for s, e, zs in out]
