"""AGG - quantitative aggregation over the mentions of a class/entity (complements ID / CLASS / TOTAL).

    AGG CLASS fruta FROM "Comprei 2 bananas, meia dúzia de maçãs e 1,5 kg de uva."

Division of labour (documented, testable):
  model          which spans are mentions of the target, and their polarity (asserted | negated | nonfactual)
  deterministic  the quantity attached to each mention (digits, Portuguese/English number words, groups such as
                 dúzia/par/dezena), its kind (count | group | mass | volume | length | money), ranges and
                 approximations, duplicate references ("2 bananas ... as bananas"), unit conversion and the sum.

AGG never sums every number in the text: a quantity is only taken when it is attached to a mention, and only
compatible quantities are added (counts with counts, grams with kilograms, never kilograms with units or money).
Everything that is not added is reported in `excluded` with the reason.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

UNITS = [   # (written forms, kind, factor to the base unit, base unit)
    (("mg", "miligrama", "miligramas"), "mass", 0.001, "g"),
    (("g", "gr", "grama", "gramas", "gram", "grams"), "mass", 1.0, "g"),
    (("kg", "quilo", "quilos", "kilo", "kilos", "quilograma", "quilogramas", "kilogram", "kilograms"), "mass", 1000.0, "g"),
    (("t", "tonelada", "toneladas", "ton", "tons"), "mass", 1e6, "g"),
    (("ml", "mililitro", "mililitros", "milliliter", "milliliters"), "volume", 1.0, "ml"),
    (("l", "litro", "litros", "liter", "liters", "litre", "litres"), "volume", 1000.0, "ml"),
    (("mm", "milímetro", "milímetros", "milimetro", "milimetros"), "length", 0.001, "m"),
    (("cm", "centímetro", "centímetros", "centimetro", "centimetros"), "length", 0.01, "m"),
    (("m", "metro", "metros", "meter", "meters"), "length", 1.0, "m"),
    (("km", "quilômetro", "quilômetros", "quilometro", "quilometros", "kilometer", "kilometers"), "length", 1000.0, "m"),
]
UNIT_OF = {w: (kind, f, base) for forms, kind, f, base in UNITS for w in forms}
GROUPS = {"dúzia": 12, "duzia": 12, "dúzias": 12, "duzias": 12, "par": 2, "pares": 2, "dezena": 10, "dezenas": 10,
          "centena": 100, "centenas": 100, "dozen": 12, "dozens": 12, "pair": 2, "pairs": 2}
CURRENCY = {"r$": "BRL", "reais": "BRL", "real": "BRL", "us$": "USD", "$": "USD", "dólares": "USD", "dolares": "USD",
            "dólar": "USD", "dolar": "USD", "€": "EUR", "euros": "EUR", "euro": "EUR"}
UNITS_PT = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
            "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14,
            "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
            "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100,
            "cento": 100, "duzentos": 200, "duzentas": 200, "trezentos": 300, "trezentas": 300, "quatrocentos": 400,
            "quinhentos": 500, "seiscentos": 600, "setecentos": 700, "oitocentos": 800, "novecentos": 900,
            "meio": 0.5, "meia": 0.5,
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100, "half": 0.5}
SCALES = {"mil": 1000, "thousand": 1000, "milhão": 1e6, "milhao": 1e6, "milhões": 1e6, "milhoes": 1e6, "million": 1e6}
ZERO = {"zero", "nenhum", "nenhuma", "no", "none"}
APPROX = re.compile(r"\b(cerca de|aproximadamente|uns|umas|quase|mais ou menos|por volta de|about|around|approximately|roughly)\s*$", re.I)
ATLEAST = re.compile(r"\b(mais de|pelo menos|no m[ií]nimo|more than|at least)\s*$", re.I)
ATMOST = re.compile(r"\b(menos de|at[eé]|no m[aá]ximo|less than|at most|up to)\s*$", re.I)
DEFINITE = {"o", "a", "os", "as", "esse", "essa", "esses", "essas", "este", "esta", "estes", "estas", "aquele",
            "aquela", "aqueles", "aquelas", "the", "those", "these", "dele", "dela"}
INDEFINITE_ONE = {"um", "uma", "a", "an"}
NUM = r"\d+(?:[.,]\d+)?"


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


def _num(tok: str) -> Optional[float]:
    t = _fold(tok)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):          # 1.500 -> 1500 (pt thousands)
        return float(t.replace(".", ""))
    if re.fullmatch(NUM, t):
        return float(t.replace(",", "."))
    return None


def _money(a: str) -> float:
    """'1.299,90' -> 1299.9 ; '5' -> 5 ; '12,50' -> 12.5 ; '1,299.90' -> 1299.9"""
    a = a.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", a):
        return float(a.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", a):
        return float(a.replace(",", ""))
    return float(a.replace(",", "."))


def parse_words(words: List[str]) -> Optional[float]:
    """Portuguese/English number words: 'vinte e cinco' -> 25, 'mil e quinhentos' -> 1500, 'duas' -> 2."""
    total, cur, seen = 0.0, 0.0, False
    for w in words:
        w = _fold(w)
        if w in ("e", "and"):
            continue
        if w in UNITS_PT:
            cur += UNITS_PT[w]; seen = True
        elif w in SCALES:
            cur = (cur or 1) * SCALES[w]; total += cur; cur = 0.0; seen = True
        elif w in ZERO:
            return 0.0
        else:
            return None
    return (total + cur) if seen else None


def _tokens_before(source: str, start: int, max_chars: int = 60) -> List[Tuple[str, int]]:
    left0 = max(0, start - max_chars)
    left = source[left0:start]
    return [(m.group(), left0 + m.start()) for m in re.finditer(r"R\$|US\$|€|\$|\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?|[^\W\d_]+", left)]


def quantity_for(source: str, start: int) -> dict:
    """Quantity attached to the mention starting at `start` (looks left, stops at the first non-quantity word)."""
    toks = _tokens_before(source, start)
    i = len(toks)
    q = {"value": None, "kind": "count", "unit": "unidade", "explicit": False, "group": None, "range": None,
         "approx": False, "bound": None, "currency": None, "span": None, "definite": False}
    # money spent ON the item: "R$ 5 em bananas" / "5 reais em bananas" -> money, never a count
    amt = r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)"
    mm = re.search(r"(R\$|US\$|€|\$)\s*" + amt + r"\s*(?:em|in|de|of)?\s*$|"
                   + amt + r"\s*(reais|real|d[oó]lares|d[oó]lar|euros?)\s*(?:em|in|de|of)?\s*$",
                   source[max(0, start - 40):start], re.I)
    if mm:
        cur = CURRENCY.get(_fold(mm.group(1) or mm.group(4) or ""), "BRL")
        q.update(value=_money(mm.group(2) or mm.group(3)), kind="money", unit=cur, currency=cur, explicit=True,
                 span=(max(0, start - 40) + mm.start(), start))
        return q
    # optional connector: "de" (2 kg de maçã / meia dúzia de ovos)
    j = i
    if j and _fold(toks[j - 1][0]) in ("de", "of", "em", "in", "do", "da"):
        j -= 1
    unit = group = None
    if j and _fold(toks[j - 1][0]) in UNIT_OF:
        unit = _fold(toks[j - 1][0]); j -= 1
    elif j and _fold(toks[j - 1][0]) in GROUPS:
        group = _fold(toks[j - 1][0]); j -= 1
    # the number itself: digits, or a run of number words
    k = j
    val = None
    if k and _num(toks[k - 1][0]) is not None:
        val = _num(toks[k - 1][0]); k -= 1
    else:
        while k and (_fold(toks[k - 1][0]) in UNITS_PT or _fold(toks[k - 1][0]) in SCALES
                     or _fold(toks[k - 1][0]) in ZERO or (_fold(toks[k - 1][0]) in ("e", "and") and k - 1 > 0
                                                         and (_fold(toks[k - 2][0]) in UNITS_PT
                                                              or _fold(toks[k - 2][0]) in SCALES))):
            k -= 1
        if k < j:
            val = parse_words([t for t, _ in toks[k:j]])
    if val is None and group:                               # "dúzia de ovos" without a number = one dozen
        val, k = 1.0, j
    if val is None and unit is None and group is None:
        if toks and _fold(toks[-1][0]) in DEFINITE:
            q["definite"] = True
        return q
    if val is None:
        return q
    # range: "entre 2 e 3 kg" / "2 a 3 kg" / "2-3 kg"
    seg = source[max(0, toks[k][1] - 20 if k < len(toks) else start - 20):start]
    rm = re.search(r"(?:entre\s+)?(" + NUM + r")\s*(?:e|a|-|–|to|and)\s*(" + NUM + r")\s*(?:[^\W\d_]+\s*)?(?:de\s*)?$", seg, re.I)
    if rm and _num(rm.group(2)) == val:
        lo = _num(rm.group(1))
        if lo is not None and lo < val:
            q["range"] = (lo, val)
    before = source[max(0, toks[k][1] - 30):toks[k][1]] if k < len(toks) else ""
    if APPROX.search(before):
        q["approx"] = True
    if ATLEAST.search(before):
        q["bound"] = "min"
    if ATMOST.search(before):
        q["bound"] = "max"
    q["explicit"] = True
    q["span"] = (toks[k][1] if k < len(toks) else start, start)
    if unit:
        kind, f, base = UNIT_OF[unit]
        q.update(value=val, kind=kind, unit=unit, factor=f, base=base)
    elif group:
        q.update(value=val * GROUPS[group], kind="count", unit="unidade", group=f"{val:g} × {group} ({GROUPS[group]})")
        if q["range"]:
            q["range"] = (q["range"][0] * GROUPS[group], q["range"][1] * GROUPS[group])
    else:
        q.update(value=val)
    return q


def _head(text: str) -> str:
    w = _fold(re.findall(r"[^\W\d_]+", text)[-1]) if re.findall(r"[^\W\d_]+", text) else _fold(text)
    for suf in ("ões", "ães", "ais", "éis", "eis", "óis", "res", "zes", "s"):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)]
    return w


def aggregate(source: str, matches: List[dict], abstain_thr: Optional[float] = None) -> dict:
    """matches: [{text, start, end, probability, polarity}] in source order. Returns the AGG result."""
    parts, excluded = [], []
    quantified_heads = set()
    for m in sorted(matches, key=lambda x: x["start"]):
        pol = m.get("polarity", "asserted")
        if pol in ("negated", "nonfactual"):
            excluded.append({"text": m["text"], "start": m["start"], "end": m["end"],
                             "reason": "negada" if pol == "negated" else "hipotética (não afirmada)"})
            continue
        q = quantity_for(source, m["start"])
        head = _head(m["text"])
        if not q["explicit"] and q.get("definite") and head in quantified_heads:
            excluded.append({"text": m["text"], "start": m["start"], "end": m["end"],
                             "reason": "referência repetida a itens já contados"})
            continue
        if not q["explicit"]:
            plural = _fold(m["text"]).endswith("s") and not re.search(r"\b(um|uma|a|an)\s+$", source[max(0, m["start"] - 6):m["start"]], re.I)
            if plural:
                excluded.append({"text": m["text"], "start": m["start"], "end": m["end"],
                                 "reason": "quantidade não informada (plural sem número)", "indeterminate": True})
                continue
            q["value"] = 1.0
        quantified_heads.add(head)
        parts.append({"text": m["text"], "start": m["start"], "end": m["end"], "probability": m.get("probability"),
                      "quantity": q["value"], "kind": q["kind"], "unit": q["unit"], "explicit": q["explicit"],
                      "group": q["group"], "range": list(q["range"]) if q["range"] else None, "approx": q["approx"],
                      "bound": q["bound"], "quantity_span": list(q["span"]) if q["span"] else None,
                      "base_value": q["value"] * q.get("factor", 1.0) if q["kind"] in ("mass", "volume", "length") else None,
                      "base_unit": q.get("base")})
    totals: Dict[str, dict] = {}
    for p in parts:
        key = p["kind"] if p["kind"] != "money" else f"money:{p['unit']}"
        t = totals.setdefault(key, {"value": 0.0, "lo": 0.0, "hi": 0.0, "approx": False, "bounded": False, "items": 0,
                                    "unit": p["base_unit"] or p["unit"]})
        v = p["base_value"] if p["base_value"] is not None else p["quantity"]
        f = (p["base_value"] / p["quantity"]) if (p["base_value"] is not None and p["quantity"]) else 1.0
        lo, hi = (p["range"][0] * f, p["range"][1] * f) if p["range"] else (v, v)
        t["value"] += v; t["lo"] += lo; t["hi"] += hi; t["items"] += 1
        t["approx"] |= bool(p["approx"]); t["bounded"] |= bool(p["bound"])
    for t in totals.values():
        if t["lo"] == t["hi"]:
            t.pop("lo"); t.pop("hi")
        else:
            t["range"] = [t.pop("lo"), t.pop("hi")]
        if t["unit"] == "g" and t["value"] >= 1000:
            t["display"] = f"{t['value'] / 1000:g} kg"
        elif t["unit"] == "ml" and t["value"] >= 1000:
            t["display"] = f"{t['value'] / 1000:g} l"
        else:
            t["display"] = f"{t['value']:g} {t['unit']}"
        if float(t["value"]).is_integer():
            t["value"] = int(t["value"])
    primary = "count" if "count" in totals else (next(iter(totals)) if totals else None)
    probs = [p["probability"] for p in parts if isinstance(p.get("probability"), (int, float))]
    prob = 1.0
    for x in probs:
        prob *= x
    uncertain = [p["text"] for p in parts if abstain_thr is not None and isinstance(p.get("probability"), (int, float))
                 and p["probability"] < abstain_thr]
    indeterminate = any(e.get("indeterminate") for e in excluded)
    r = {"operation": "AGG", "type": "aggregate", "value": totals[primary]["value"] if primary else 0,
         "unit": totals[primary]["unit"] if primary else "unidade", "primary": primary, "totals": totals,
         "parts": parts, "excluded": excluded, "probability": round(prob, 6) if probs else None,
         "incompatible_kinds": len(totals) > 1, "complete": not indeterminate,
         "uncertain_items": uncertain, "abstain": False}
    if uncertain or indeterminate:
        r.update(abstain=True, proposed_value=r["value"], value=None,
                 abstain_reason="item incerto" if uncertain else "quantidade não informada em algum item")
        if abstain_thr is not None:
            r["abstain_threshold"] = abstain_thr
    return r


def aggregate_matchset(source: str, ms, abstain_thr: Optional[float] = None) -> dict:
    """Adapter from a runtime MatchSet (semantic or deterministic) to aggregate()."""
    from .structure import negation_in_scope
    ms_list = []
    for m in ms.matches:
        pol = (m.attributes or {}).get("polarity")
        if pol is None:                                 # deterministic matches carry no polarity: NegEx clause scope
            pol = "negated" if negation_in_scope(source, m.start) else "asserted"
        ms_list.append({"text": m.text, "start": m.start, "end": m.end, "probability": m.probability, "polarity": pol})
    r = aggregate(source, ms_list, abstain_thr)
    r.update({"mode": ms.resolved_mode or ms.mode, "target": ms.target, "engine": ms.engine})
    if ms.model:
        r["model"] = ms.model
    return r


# ---------------------------------------------------------------------------------------------------------------
# Anchored AGG (v0.7.1): quantities anchor the candidates, the model only judges class membership.
# Span resolution misses / over-accepts nouns; a yes/no membership judgement per candidate is an easier task.
_QTY_WORD = set(UNITS_PT) | set(SCALES) | set(GROUPS) | set(UNIT_OF) | {"meia", "meio"}
_STOP = {"de", "do", "da", "dos", "das", "e", "em", "a", "o", "as", "os", "um", "uma", "mas", "ou", "com", "para",
         "por", "que", "não", "nao", "só", "so", "também", "tambem", "no", "na", "nos", "nas", "of", "and", "the"}


def anchored_nouns(source: str) -> List[Tuple[int, int, str]]:
    """Noun right after a quantity expression: '2 bananas', 'duas dúzias de ovos', '1 kg de maçã', 'R$ 5 em uvas'."""
    toks = [(m.group(), m.start(), m.end()) for m in
            re.finditer(r"R\$|US\$|€|\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?|[^\W\d_]+", source)]
    out, seen = [], set()
    for i, (t, s, e) in enumerate(toks):
        f = _fold(t)
        if not (_num(t) is not None or f in _QTY_WORD or t in ("R$", "US$", "€")):
            continue
        j = i + 1
        while j < len(toks) and (_num(toks[j][0]) is not None or _fold(toks[j][0]) in _QTY_WORD
                                 or _fold(toks[j][0]) in ("de", "of", "em", "in", "e", ",")):
            if _fold(toks[j][0]) == "e" and j + 1 < len(toks) and not (
                    _num(toks[j + 1][0]) is not None or _fold(toks[j + 1][0]) in _QTY_WORD):
                break
            j += 1
        if j >= len(toks):
            continue
        w, ws, we = toks[j]
        if _fold(w) in _STOP or _num(w) is not None or ws in seen:
            continue
        if re.search(r"[.;:!?\n]", source[e:ws]):          # quantity and noun in different sentences
            continue
        seen.add(ws)
        out.append((ws, we, w))
    return out


def verify_class(engine, source: str, ms, target: str, accept: float = 0.5, how: str = "noul"):
    """Candidates = model spans + anchored nouns; keep the ones the model says belong to `target` (one batched pass).
    Returns a new MatchSet; never adds a candidate the membership head rejects."""
    from .semantic import SemReq
    from .result import Match, MatchSet
    cands = [(m.start, m.end, m.text, m) for m in ms.matches]
    for s, e, w in anchored_nouns(source):
        if not any(s < c[1] and c[0] < e for c in cands):
            cands.append((s, e, w, None))
    if not cands:
        return ms
    if how == "add":                                   # keep the model's spans; only add anchored nouns it missed
        cands = [c for c in cands if c[3] is not None] + [c for c in cands if c[3] is None]
    if how == "choice":
        reqs = [SemReq("CHOICE", w, options=(target, "outra coisa"), ask=f'"{w}" é um(a) {target}?')
                for _, _, w, _ in cands]
    else:
        reqs = [SemReq("NOUL", source, options=(f'No texto, "{w}" é um(a) {target}.',)) for _, _, w, _ in cands]
    outs = engine.run(reqs)
    keep = []
    for (s, e, w, m), o in zip(cands, outs):
        if how == "choice":
            p = float((o.get("distribution") or {}).get(target, 0.0))
        else:
            p = float(o.get("p_true", 0.0))
        if how == "add" and m is not None:
            p = max(p, 1.0)                              # model span kept as is
        if p < accept:
            continue
        attrs = dict(m.attributes) if m is not None else {}
        attrs["membership"] = round(p, 4)
        prob = min(p, m.probability) if m is not None else p
        keep.append(Match(text=w, start=s, end=e, probability=round(prob, 6), engine="semantic", mode=ms.mode,
                          target=target, attributes=attrs))
    keep.sort(key=lambda x: x.start)
    return MatchSet(mode=ms.mode, target=ms.target, engine=ms.engine, matches=keep, candidates=ms.candidates,
                    threshold=ms.threshold, model=ms.model, resolved_mode=ms.resolved_mode)


# ---------------------------------------------------------------------------------------------------------------
# AGG / AGID as SELECTORS (v0.7.3). They select like CLASS / ID, and every selected mention also carries the
# quantity it refers to, its factor (dúzia = 12, kg = 1000 g, R$) and the variations that were matched.
#   AGG fruta   = CLASS fruta + quantity per mention      (semantic: the model decides what is a fruit)
#   AGID maçã   = ID maçã     + quantity per mention      (deterministic: maçã, maçãs, Maçã, MAÇÃS ...)
# Any operation works on them: TOTAL sums the quantities (per kind: count / mass / volume / money), COUNT counts
# the counted items, GET / REPORT / EXISTS / MATCH return the mentions with their quantities.

def _plural_pt(w: str) -> List[str]:
    f = w
    if f.endswith("ão"):
        return [f[:-2] + "ões", f[:-2] + "ães", f[:-2] + "ãos"]
    if f.endswith(("al", "el", "ol", "ul")):
        return [f[:-1] + "is", f[:-2] + "éis" if f.endswith("el") else f[:-1] + "is"]
    if f.endswith("il"):
        return [f[:-2] + "is", f[:-1] + "s"]
    if f.endswith("m"):
        return [f[:-1] + "ns"]
    if f.endswith(("r", "z", "s")):
        return [f + "es"]
    return [f + "s"]


def _singular_pt(w: str) -> List[str]:
    f, out = w, []
    for suf, rep in (("ões", "ão"), ("ães", "ão"), ("ãos", "ão"), ("éis", "el"), ("ais", "al"), ("óis", "ol"),
                     ("uis", "ul"), ("ns", "m"), ("res", "r"), ("zes", "z"), ("ses", "s"), ("is", "il"), ("s", "")):
        if f.endswith(suf) and len(f) > len(suf) + 1:
            out.append(f[: -len(suf)] + rep)
    return out


def variations(target: str) -> List[str]:
    """Inflections of the target (each word): singular / plural (pt + en), no duplicates, target first."""
    words_ = target.strip().split()
    if not words_:
        return []
    forms = []
    for w in words_:
        lw = w.casefold()
        forms.append([lw] + _plural_pt(lw) + _singular_pt(lw))
    out = [target.strip()]
    if len(forms) == 1:
        out += forms[0]
    else:                                   # "nota fiscal" -> "notas fiscais": inflect every word together
        k = max(len(f) for f in forms)
        for i in range(1, k):
            out.append(" ".join(f[i] if i < len(f) else f[0] for f in forms))
    seen, uniq = set(), []
    for x in out:
        if x and _fold(x) not in seen:
            seen.add(_fold(x)); uniq.append(x)
    return uniq


def resolve_agid(source: str, target: str, exact: bool = False, aliases=()):
    from . import deterministic as D
    from .result import MatchSet
    alts = list(dict.fromkeys(variations(target)[1:] + [v for a in aliases for v in variations(a)]))
    ms = D.resolve_id(source, target, exact, alts if not exact else list(aliases))
    for m in ms.matches:
        m.attributes["variation"] = m.text
    return MatchSet("AGID", target, "deterministic", ms.matches, ms.candidates)


def enrich(source: str, ms, mode: str, abstain_thr: Optional[float] = None):
    """Attach quantity / factor / polarity to every mention. Returns (selected MatchSet, aggregate dict)."""
    from .result import Match, MatchSet
    from .structure import negation_in_scope
    rows = []
    for m in ms.matches:
        pol = (m.attributes or {}).get("polarity")
        if pol is None:
            pol = "negated" if negation_in_scope(source, m.start) else "asserted"
            m.attributes["polarity"] = pol
        rows.append({"text": m.text, "start": m.start, "end": m.end, "probability": m.probability, "polarity": pol})
    ag = aggregate(source, rows, abstain_thr)
    by = {(p["start"], p["end"]): p for p in ag["parts"]}
    ex = {(e["start"], e["end"]): e for e in ag["excluded"]}
    counted = []
    for m in ms.matches:
        k = (m.start, m.end)
        if k in by:
            p = by[k]
            m.attributes.update({"quantity": p["quantity"], "unit": p["unit"], "kind": p["kind"],
                                 "quantity_explicit": p["explicit"], "counted": True})
            for f in ("group", "range", "approx", "bound", "base_value", "base_unit", "quantity_span"):
                if p.get(f) not in (None, False):
                    m.attributes[f] = p[f]
            counted.append(m)
        elif k in ex:
            m.attributes.update({"counted": False, "reason": ex[k]["reason"]})
    sel = MatchSet(mode, ms.target, ms.engine, ms.matches, ms.candidates, ms.threshold, ms.model,
                   resolved_mode=ms.resolved_mode or ms.mode)
    cnt = MatchSet(mode, ms.target, ms.engine, counted, ms.candidates, ms.threshold, ms.model,
                   resolved_mode=ms.resolved_mode or ms.mode)
    return sel, cnt, ag


def selector_result(op: str, source: str, ms, mode: str, at, abstain_thr, diagnostics=False, mentions=False):
    from .result import operation_result
    sel, cnt, ag = enrich(source, ms, mode, abstain_thr)
    summary = {k: ag[k] for k in ("value", "unit", "primary", "totals", "excluded", "complete", "abstain")
               if k in ag}
    if op == "TOTAL":
        r = dict(ag)
        r.update({"operation": "TOTAL", "mode": mode, "target": ms.target, "engine": ms.engine,
                  "matches": [m.to_dict() for m in sel.matches]})
        if ms.model:
            r["model"] = ms.model
        return r
    if op in ("COUNT", "GET", "EXISTS"):
        r = operation_result(op, cnt, at, abstain_thr, diagnostics, True)
    else:                                               # MATCH / REPORT: every mention, with counted / reason
        r = operation_result(op, sel, at, abstain_thr, diagnostics, True)
    r["mode"] = mode
    r["aggregate"] = summary
    return r
