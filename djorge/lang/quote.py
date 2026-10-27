"""Safe construction of DJORGE queries from untrusted text.

The safest way to send user text is to NOT put it in the query at all: pass it as a named source
(`sources={"text": raw}`) and write `FROM text`. Quotes, backslashes, newlines and any other character in the
text then have no effect on parsing. `quote()` is for the remaining literals that must live in the query
(option labels, descriptions, the ASK question).

    from djorge.lang.quote import quote, classify_query
    q = classify_query(["cobrança", "suporte técnico"], ask='Qual "time" deve atender?')
    z.execute(q, sources={"text": 'Cliente disse: "fui cobrado 2x"'})
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, Mapping, Optional, Sequence, Union

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


def clean_text(s: str, max_chars: Optional[int] = None) -> str:
    """NFC-normalise, drop NUL / control / bidi-override / zero-width characters (used to hide instructions or
    spoof text), keep newlines and tabs."""
    if not isinstance(s, str):
        raise TypeError("text must be a string")
    s = _CTRL.sub("", unicodedata.normalize("NFC", s)).replace("\r\n", "\n").replace("\r", "\n")
    return s[:max_chars] if max_chars else s


def quote(s: str) -> str:
    """DJORGE string literal for ANY text: the result always parses back to exactly clean_text(s)."""
    s = clean_text(s)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


def options_literal(labels: Union[Sequence[str], Mapping[str, str]]) -> str:
    if isinstance(labels, Mapping):
        return "{" + ", ".join(f"{quote(k)}: {quote(v)}" for k, v in labels.items()) + "}"
    return "[" + ", ".join(quote(x) for x in labels) + "]"


def classify_query(labels: Union[Sequence[str], Mapping[str, str]], ask: str = "", source_name: str = "text",
                   multi: bool = False, threshold: Optional[float] = None) -> str:
    if not NAME.match(source_name):
        raise ValueError("source_name must be an identifier")
    q = f"{'SET' if multi else 'CHOICE'} {options_literal(labels)} FROM {source_name}"
    if ask:
        q += f" ASK {quote(ask)}"
    if threshold is not None:
        q += f" THRESHOLD {float(threshold):g}"
    return q


def verify_query(proposition: str, source_name: str = "text") -> str:
    if not NAME.match(source_name):
        raise ValueError("source_name must be an identifier")
    return f"NOUL {quote(proposition)} FROM {source_name}"


def score_query(levels: Sequence[str], ask: str = "", source_name: str = "text") -> str:
    q = f"SCORE {options_literal(levels)} FROM {source_name}"
    return q + (f" ASK {quote(ask)}" if ask else "")
