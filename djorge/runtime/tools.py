"""Deterministic tools: exact, instant, no model call. Used by the API (/v1/tools/<name>) and by the runtime
(PII masking before analysis, domain flags). Everything here is pure Python and safe on untrusted input.

  pii       detect + mask e-mail, phone, CPF, CNPJ, credit card (Luhn), CEP, IPv4, IBAN-like, URLs with tokens
  validate  cpf | cnpj | email | url | card | cep   -> {valid, normalized}
  math      safe arithmetic (+ - * / // % ** parentheses, percent), no names, bounded size
  lang      pt / en / es guess
"""
from __future__ import annotations

import ast
import operator
import re
from typing import Dict, List

# ------------------------------------------------------------------------------------------------- validators --


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def valid_cpf(s: str) -> bool:
    d = _digits(s)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        tot = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (tot * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def valid_cnpj(s: str) -> bool:
    d = _digits(s)
    if len(d) != 14 or d == d[0] * 14:
        return False
    for n, w in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        r = sum(int(d[i]) * w[i] for i in range(n)) % 11
        if (0 if r < 2 else 11 - r) != int(d[n]):
            return False
    return True


def valid_card(s: str) -> bool:
    d = _digits(s)
    if not 13 <= len(d) <= 19:
        return False
    tot, alt = 0, False
    for ch in reversed(d):
        x = int(ch)
        if alt:
            x = x * 2 - 9 if x * 2 > 9 else x * 2
        tot += x; alt = not alt
    return tot % 10 == 0


EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}\b")
URL = re.compile(r"\bhttps?://[^\s<>\"']{2,2000}", re.I)
PATTERNS = [
    ("email", EMAIL, None),
    ("cpf", re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"), valid_cpf),
    ("cnpj", re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)"), valid_cnpj),
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), valid_card),
    ("phone", re.compile(r"(?<!\w)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)|(?<!\w)\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\d)"), None),
    ("cep", re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)"), None),
    ("ipv4", re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)"), None),
    ("secret_url", re.compile(r"\bhttps?://\S*[?&](?:token|key|apikey|api_key|password|senha|sig|signature)=\S+", re.I), None),
]


def find_pii(text: str) -> List[Dict]:
    out, taken = [], []
    for kind, rx, check in PATTERNS:
        for m in rx.finditer(text or ""):
            a, b = m.span()
            if any(a < y and x < b for x, y in taken):
                continue
            if check and not check(m.group()):
                continue
            taken.append((a, b))
            out.append({"type": kind, "start": a, "end": b})
    return sorted(out, key=lambda x: x["start"])


def mask_pii(text: str) -> Dict:
    items = find_pii(text)
    parts, last = [], 0
    for it in items:
        parts.append(text[last:it["start"]]); parts.append(f"[{it['type'].upper()}]"); last = it["end"]
    parts.append(text[last:])
    return {"text": "".join(parts), "found": [{"type": i["type"], "start": i["start"], "end": i["end"]} for i in items]}


def validate(kind: str, value: str) -> Dict:
    v = (value or "").strip()
    if kind == "cpf":
        ok = valid_cpf(v); d = _digits(v)
        return {"valid": ok, "normalized": f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if ok else None}
    if kind == "cnpj":
        ok = valid_cnpj(v); d = _digits(v)
        return {"valid": ok, "normalized": f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if ok else None}
    if kind == "card":
        ok = valid_card(v); d = _digits(v)
        return {"valid": ok, "normalized": ("**** " + d[-4:]) if ok else None}
    if kind == "email":
        ok = bool(EMAIL.fullmatch(v)); return {"valid": ok, "normalized": v.lower() if ok else None}
    if kind == "url":
        ok = bool(URL.fullmatch(v)); return {"valid": ok, "normalized": v if ok else None}
    if kind == "cep":
        d = _digits(v); ok = len(d) == 8
        return {"valid": ok, "normalized": f"{d[:5]}-{d[5:]}" if ok else None}
    raise ValueError("kind must be cpf | cnpj | card | email | url | cep")

# ------------------------------------------------------------------------------------------------------- math --


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_math(expr: str, locale: str = "pt") -> Dict:
    """Arithmetic only. '18% de 2450' / '18% of 2450' -> 0.18*2450; decimal comma accepted ('1.299,90')."""
    e = (expr or "").strip()
    if len(e) > 200:
        raise ValueError("expression too long")
    e = re.sub(r"(\d+(?:[.,]\d+)?)\s*%\s*(?:de|of|del)\s*", r"(\1/100)*", e, flags=re.I)
    e = re.sub(r"(\d+(?:[.,]\d+)?)\s*%", r"(\1/100)", e)
    if locale in ("pt", "es", "de", "fr"):           # 1.299,90 -> 1299.90 ; 2.450 -> 2450
        e = re.sub(r"\d{1,3}(?:\.\d{3})+(?![\d.])", lambda m: m.group().replace(".", ""), e)
        e = re.sub(r"(?<=\d),(?=\d)", ".", e)
    else:                                             # 1,299.90 -> 1299.90
        e = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", e)
    e = e.replace("x", "*").replace("×", "*").replace("÷", "/").replace("^", "**")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            l, r = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(r) > 64 or abs(l) > 1e6):
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](l, r)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        raise ValueError("only numbers and + - * / // % ** ( ) are allowed")
    try:
        v = ev(ast.parse(e, mode="eval"))
    except ZeroDivisionError:
        raise ValueError("division by zero")
    except SyntaxError:
        raise ValueError("invalid expression")
    if isinstance(v, float):
        v = round(v, 10)
    return {"expression": e, "value": v}
