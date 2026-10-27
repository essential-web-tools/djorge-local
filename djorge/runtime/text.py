"""Deterministic text primitives.

Everything here is pure Python, allocation-light and exact. The neural model is
never invoked for anything this module can answer.

All extractors return `Span` objects carrying character offsets into the
original text, so every result is traceable to the input.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class Span:
    text: str
    start: int
    end: int
    value: Optional[float] = None        # numeric value for number-like spans
    kind: str = "span"                   # word | char | sentence | line | number | match | entity ...
    p: float = 1.0                       # probability the span is a true match (1.0 = deterministic)

    def to_dict(self) -> dict:
        d = {"text": self.text, "start": self.start, "end": self.end, "kind": self.kind}
        if self.value is not None:
            d["value"] = self.value
        if self.p != 1.0:
            d["p"] = round(self.p, 6)
        return d


# --------------------------------------------------------------------------- #
# Normalization (ID semantics = exact or near-exact identity)
# --------------------------------------------------------------------------- #

def fold(s: str) -> str:
    """Case-fold + NFKC. Used for exact-identity comparison."""
    return unicodedata.normalize("NFKC", s).casefold()


def norm_token(w: str) -> str:
    """Near-exact identity: case/width fold + possessive and regular English
    plural stripping. Deliberately conservative: it never maps two different
    lemmas together for common words (e.g. 'bus' stays 'bus')."""
    w = fold(w)
    for suf in ("'s", "\u2019s"):
        if w.endswith(suf):
            w = w[: -len(suf)]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and re.search(r"(ss|x|z|ch|sh)es$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


# --------------------------------------------------------------------------- #
# Segmenters
# --------------------------------------------------------------------------- #

WORD_RE = re.compile(r"[^\W_]+(?:['\u2019\-][^\W_]+)*", re.UNICODE)
_ABBREV = {"mr", "mrs", "ms", "dr", "dra", "prof", "profa", "sr", "sra", "srta", "jr", "st", "vs", "etc", "e.g", "i.e",
           "inc", "ltd", "no", "nº", "av", "p.ex", "ex", "pág", "pag", "z.b", "bzw", "usw", "ud", "uds", "sres", "mme", "mlle",
           "cia", "ltda", "s.a", "fig", "vol", "cap", "art"}
_SENT_END = re.compile(r"[.!?\u3002\uff01\uff1f\u061f\u06d4\u0964]+[\"'\u201d\u2019)\]\u300d\u300f]*")
_CJK_END = set("\u3002\uff01\uff1f")


def words(text: str) -> List[Span]:
    return [Span(m.group(), m.start(), m.end(), kind="word") for m in WORD_RE.finditer(text)]


def chars(text: str) -> List[Span]:
    return [Span(c, i, i + 1, kind="char") for i, c in enumerate(text)]


def letters(text: str) -> List[Span]:
    return [Span(c, i, i + 1, kind="char") for i, c in enumerate(text) if c.isalpha()]


def lines(text: str) -> List[Span]:
    out, pos = [], 0
    for raw in text.splitlines(keepends=True):
        content = raw.rstrip("\r\n")
        if content.strip():
            lead = len(content) - len(content.lstrip())
            out.append(Span(content.strip(), pos + lead, pos + lead + len(content.strip()), kind="line"))
        pos += len(raw)
    return out


def sentences(text: str) -> List[Span]:
    """Rule-based sentence splitter (terminal punctuation + abbreviation guard)."""
    out, start = [], 0
    for m in _SENT_END.finditer(text):
        end = m.end()
        before = text[start:m.start()].split()
        last = before[-1].lower().rstrip(".") if before else ""
        nxt = text[end:end + 2]
        if last in _ABBREV and m.group().startswith(".") and len(m.group()) == 1:
            continue
        if nxt and not nxt[0].isspace() and nxt[0] not in "\"'" and m.group()[0] not in _CJK_END:
            continue  # e.g. "3.14", "example.com" (CJK full stops need no following space)
        seg = text[start:end]
        if seg.strip():
            lead = len(seg) - len(seg.lstrip())
            out.append(Span(seg.strip(), start + lead, end, kind="sentence"))
        start = end
    tail = text[start:]
    if tail.strip():
        lead = len(tail) - len(tail.lstrip())
        out.append(Span(tail.strip(), start + lead, start + lead + len(tail.strip()), kind="sentence"))
    return out


# --------------------------------------------------------------------------- #
# Numbers (digits + English number words)
# --------------------------------------------------------------------------- #

# Locale-aware numerals: "1,80" (decimal comma), "1.200" / "1 200" (thousands), "47,50", "1,234.5", "3.14"
NUM_RE = re.compile(
    r"(?<![\w.,])[-+]?(?:\d{1,3}(?:[.,\u00a0\u202f ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?:[eE][-+]?\d+)?%?(?![\w])"
    r"|(?<![\w.,])[-+]?\d+(?:[.,]\d+)?(?=[a-zA-Z\u00b0%])"
)


def parse_number(raw: str):
    """Interpret separators by structure, not by locale guess:
    * both '.' and ',' present -> the LAST one is the decimal separator
    * one separator followed by exactly 3 digits (possibly repeated) -> thousands ('1.200', '12,000', '1 200')
    * otherwise the single separator is decimal ('1,80', '47,50', '3.14')"""
    s = raw.strip().rstrip("%").replace("\u00a0", " ").replace("\u202f", " ")
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    if " " in s:
        s = s.replace(" ", "")
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "." in s or "," in s:
        sep = "." if "." in s else ","
        parts = s.split(sep)
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3 and parts[0] not in ("0",)):
            s = s.replace(sep, "")
        else:
            s = s.replace(sep, ".")
    try:
        v = float(s)
    except ValueError:
        return None
    v *= sign
    return int(v) if v.is_integer() and "." not in s and "e" not in s.lower() else v


_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_NUMWORD_RE = re.compile(
    r"\b(?:(?:%s)(?:[\s\-]+(?:and[\s\-]+)?(?:%s))*)\b" % (
        "|".join(list(_UNITS) + list(_TENS) + list(_SCALES)),
        "|".join(list(_UNITS) + list(_TENS) + list(_SCALES))),
    re.IGNORECASE)


def _words_to_int(phrase: str) -> Optional[int]:
    total, current, seen = 0, 0, False
    for tok in re.split(r"[\s\-]+", phrase.lower()):
        if tok == "and":
            continue
        if tok in _UNITS:
            current += _UNITS[tok]
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok == "hundred":
            current = max(current, 1) * 100
        elif tok in _SCALES:
            total += max(current, 1) * _SCALES[tok]
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def numbers(text: str, words_too: bool = True) -> List[Span]:
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group()
        v = parse_number(raw)
        if v is None:
            continue
        out.append(Span(raw, m.start(), m.end(), value=v, kind="percent" if raw.endswith("%") else "number"))
    if words_too:
        taken = [(sp.start, sp.end) for sp in out]
        for m in _NUMWORD_RE.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            v = _words_to_int(m.group())
            if v is not None:
                out.append(Span(m.group(), m.start(), m.end(), value=v, kind="number"))
    out.sort(key=lambda sp: sp.start)
    return out


# --------------------------------------------------------------------------- #
# Typed patterns (kind:<name> selectors)
# --------------------------------------------------------------------------- #

PATTERNS = {
    "email": r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+",
    "url": r"https?://[^\s<>\"')\]]+|www\.[^\s<>\"')\]]+",
    "money": r"(?:[$\u20ac\u00a3\u00a5\u20b9]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|JPY|BRL|INR|dollars?|euros?|reais))",
    "percent": r"[-+]?\d+(?:\.\d+)?\s?%",
    "date": r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    "time": r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\s?(?:[AaPp][Mm])?\b",
    "phone": r"(?<!\w)\+?\d[\d\s().\-]{7,}\d(?!\w)",
    "hashtag": r"(?<!\w)#[^\W_][\w]*",
    "mention": r"(?<!\w)@[A-Za-z0-9_]{2,}",
    "ip": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "uuid": r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
}
_PATTERNS_C = {k: re.compile(v) for k, v in PATTERNS.items()}


def find_kind(text: str, kind: str) -> List[Span]:
    if kind in ("number", "numbers"):
        return numbers(text)
    if kind in ("word", "words"):
        return words(text)
    if kind in ("sentence", "sentences"):
        return sentences(text)
    if kind in ("char", "chars"):
        return chars(text)
    if kind in ("letter", "letters"):
        return letters(text)
    if kind in ("line", "lines"):
        return lines(text)
    pat = _PATTERNS_C.get(kind)
    if pat is None:
        raise KeyError(kind)
    return [Span(m.group(), m.start(), m.end(), kind=kind) for m in pat.finditer(text)]


# --------------------------------------------------------------------------- #
# ID matching (exact / near-exact identity, multi-word aware)
# --------------------------------------------------------------------------- #

def find_id(text: str, target: str, exact: bool = False) -> List[Span]:
    """Find occurrences of a specific identity.

    exact=False (default): token-aligned, case-folded, plural/possessive-normalized.
    exact=True: case-sensitive substring identity.
    Targets that are not word-like (e.g. 'A', '#', 'ab') fall back to
    case-folded substring search when they contain no word characters.
    """
    if not target:
        return []
    if exact:
        out, i = [], text.find(target)
        while i != -1:
            out.append(Span(target, i, i + len(target), kind="match"))
            i = text.find(target, i + 1)
        return out
    tgt_tokens = [norm_token(m.group()) for m in WORD_RE.finditer(target)]
    if not tgt_tokens:
        ft, out = fold(target), []
        ftext = fold(text)
        i = ftext.find(ft)
        while i != -1:
            out.append(Span(text[i:i + len(ft)], i, i + len(ft), kind="match"))
            i = ftext.find(ft, i + 1)
        return out
    toks = words(text)
    normed = [norm_token(t.text) for t in toks]
    n, out = len(tgt_tokens), []
    for i in range(len(toks) - n + 1):
        if normed[i:i + n] == tgt_tokens:
            a, b = toks[i].start, toks[i + n - 1].end
            out.append(Span(text[a:b], a, b, kind="match"))
    return out


def find_regex(text: str, pattern: str, flags: str = "") -> List[Span]:
    fl = 0
    for f in flags:
        fl |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}.get(f, 0)
    rx = re.compile(pattern, fl)
    return [Span(m.group(), m.start(), m.end(), kind="match") for m in rx.finditer(text) if m.end() > m.start()]


def filter_items(items: Iterable[Span], target: str, exact: bool = False) -> List[Span]:
    if exact:
        return [s for s in items if s.text == target]
    t = norm_token(target) if WORD_RE.fullmatch(target or "") else fold(target)
    return [s for s in items if (norm_token(s.text) if WORD_RE.fullmatch(s.text) else fold(s.text)) == t]


# --------------------------------------------------------------------------- #
# State serialization: JSON / dict / list -> canonical text
# --------------------------------------------------------------------------- #

def state_to_text(state) -> str:
    """Canonical, deterministic rendering of structured state as text.

    Dict keys become 'key: value' lines (nested with dotted paths), lists
    become indexed items. The same state always renders identically, which
    makes the representation cache hit reliably.
    """
    if state is None:
        return ""
    if isinstance(state, str):
        return state
    if isinstance(state, (int, float, bool)):
        return str(state)
    lines_: List[str] = []

    def walk(obj, prefix):
        if isinstance(obj, dict):
            for k in obj:
                walk(obj[k], f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(obj, (list, tuple)):
            if all(isinstance(x, (str, int, float, bool)) or x is None for x in obj):
                lines_.append(f"{prefix}: " + ", ".join("" if x is None else str(x) for x in obj))
            else:
                for i, x in enumerate(obj):
                    walk(x, f"{prefix}[{i}]")
        else:
            lines_.append(f"{prefix}: {'' if obj is None else obj}" if prefix else str(obj))

    walk(state, "")
    return "\n".join(lines_)
