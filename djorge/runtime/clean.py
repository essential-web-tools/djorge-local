"""Text cleaning for the Djorge Origo Flash Local - ONE function used in training, in the app and in the browser
extension, so the model always reads text prepared the same way.

What it does, and why:
  * removes characters that have no legitimate use inside a message but hide content from people: zero-width,
    bidirectional controls, C0 controls, Unicode TAG characters (U+E0000-E007F, "ASCII smuggling") and the
    variation-selector supplement (U+E0100-E01EF, data hidden in emoji); the COUNT becomes a signal;
  * NFKC normalisation (full-width letters, ligatures, compatibility forms);
  * looks at every WORD: a word written in ONE alphabet stays exactly as it is (Russian stays Russian, Greek stays
    Greek); only a word that MIXES alphabets (Latin with Cyrillic/Greek look-alikes, "pаypal" with a Cyrillic "а") has
    the intruding letters folded into the word's dominant alphabet - the classic disguise (Unicode TS #39,
    mixed-script confusables);
  * internet domains get extra care: a host whose labels are entirely made of look-alike letters ("аррlе.com" in
    Cyrillic) or that arrives in punycode (xn--) is reported with how it reads ("parece apple.com");
  * the original wording is otherwise untouched, and a short trailer tells the model what was found:
        ⟦aviso: 2 palavras com letras disfarçadas · 3 caracteres ocultos · domínio аррlе.com parece apple.com⟧

    clean(text)  -> CleanResult(text, signals)      text = cleaned text + trailer (when something was found)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List

_HIDDEN = re.compile("[​-‏⁠-⁤﻿­᠎؜‪-‮⁦-⁩"
                     "\U000e0000-\U000e007f\U000e0100-\U000e01ef]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_VS = re.compile("[︀-︎]")                 # text variation selectors; U+FE0F (emoji style) is kept
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")
_HOST = re.compile(r"(?i)(?<![\w@.-])((?:https?://)?(?:[\w-]{1,63}\.)+(?:[a-z]{2,24}|xn--[a-z0-9-]{2,59}|[^\W\d_]{2,24}))(?![\w-])")

# look-alike letters: Cyrillic / Greek -> Latin (and the reverse for words that are mostly Cyrillic)
TO_LATIN = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ԁ": "d",
            "ѕ": "s", "ɡ": "g", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "А": "A", "В": "B", "Е": "E", "К": "K",
            "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
            "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
            "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
            "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X"}
TO_CYRILLIC = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х", "i": "і", "A": "А", "B": "В",
               "E": "Е", "K": "К", "M": "М", "H": "Н", "O": "О", "P": "Р", "C": "С", "T": "Т", "X": "Х"}


def script_of(ch: str) -> str:
    """Coarse alphabet of a letter: LATIN, CYRILLIC, GREEK, CJK, ... ('' for non-letters)."""
    if not ch.isalpha():
        return ""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "OTHER"
    for s in ("LATIN", "CYRILLIC", "GREEK", "ARABIC", "HEBREW", "HANGUL", "HIRAGANA", "KATAKANA", "THAI",
              "DEVANAGARI", "ARMENIAN", "GEORGIAN"):
        if name.startswith(s) or f" {s} " in name:
            return s
    if name.startswith("CJK"):
        return "CJK"
    return "OTHER"


@dataclass
class CleanResult:
    text: str
    signals: Dict[str, object] = field(default_factory=dict)

    @property
    def suspicious(self) -> bool:
        return bool(self.signals)


def _fold_word(w: str) -> (str, bool):
    """A word mixing Latin with Cyrillic/Greek look-alikes -> its dominant alphabet. Single-alphabet words untouched."""
    scripts = [script_of(c) for c in w if c.isalpha()]
    kinds = {s for s in scripts if s}
    if len(kinds) < 2 or not ({"CYRILLIC", "GREEK"} & kinds) or "LATIN" not in kinds:
        return w, False
    lat = scripts.count("LATIN")
    if lat >= len(scripts) - lat:                      # mostly Latin: intruders are the disguise
        out = "".join(TO_LATIN.get(c, c) if script_of(c) in ("CYRILLIC", "GREEK") else c for c in w)
    else:                                              # mostly Cyrillic: Latin letters slipped in
        out = "".join(TO_CYRILLIC.get(c, c) if script_of(c) == "LATIN" else c for c in w)
    return out, out != w


def _host_reading(host: str):
    """-> (how the host reads, reason) when it is disguised, else None."""
    h = host.lower()
    h = re.sub(r"^https?://", "", h)
    shown = h
    if "xn--" in h:
        try:
            shown = ".".join(lab.encode("ascii").decode("idna") if lab.startswith("xn--") else lab for lab in h.split("."))
        except (UnicodeError, ValueError):
            return None
    labels = shown.split(".")
    disguised = False
    for lab in labels[:-1] or labels:
        sc = {script_of(c) for c in lab if c.isalpha()} - {""}
        if sc and sc <= {"CYRILLIC", "GREEK"} and all(c in TO_LATIN or not c.isalpha() for c in lab):
            disguised = True                           # whole label made of look-alikes
        elif len(sc) > 1 and "LATIN" in sc:
            disguised = True                           # mixed alphabets inside one label
    if not disguised and "xn--" not in h:
        return None
    reads = "".join(TO_LATIN.get(c, c) for c in shown)
    if reads == h and "xn--" not in h:
        return None
    return reads, ("punycode" if "xn--" in h else "letras disfarçadas")


def clean(text: str, trailer: bool = True) -> CleanResult:
    s = str(text or "")
    sig: Dict[str, object] = {}
    hidden = len(_HIDDEN.findall(s)) + len(_VS.findall(s))
    s = _HIDDEN.sub("", s)
    s = _VS.sub("", s)
    ctrl = len(_CTRL.findall(s))
    s = _CTRL.sub(" ", s)
    s = unicodedata.normalize("NFKC", s)
    hosts: List[str] = []
    for m in _HOST.finditer(s):
        r = _host_reading(m.group(1))
        if r:
            hosts.append(f"{m.group(1)} parece {r[0]}")
    mixed = 0

    def fix(m):
        nonlocal mixed
        w, changed = _fold_word(m.group(0))
        mixed += changed
        return w
    s = _WORD.sub(fix, s)
    if hidden:
        sig["ocultos"] = hidden
    if ctrl:
        sig["controle"] = ctrl
    if mixed:
        sig["disfarcadas"] = mixed
    if hosts:
        sig["dominios"] = hosts[:3]
    if trailer and sig:
        parts = []
        if mixed:
            parts.append(f"{mixed} palavra{'s' if mixed > 1 else ''} com letras disfarçadas")
        if hidden:
            parts.append(f"{hidden} caractere{'s' if hidden > 1 else ''} oculto{'s' if hidden > 1 else ''}")
        for h in hosts[:3]:
            parts.append(f"domínio {h}")
        s = s.rstrip() + " ⟦aviso: " + " · ".join(parts) + "⟧"
    return CleanResult(s, sig)


def clean_text(text: str) -> str:
    return clean(text).text


def is_identity(text: str) -> bool:
    """True when cleaning would not change the text (safe for records with character offsets)."""
    return clean(text, trailer=False).text == str(text or "")
