"""Temporal tool: local date/time as a deterministic tool the decision runtime uses automatically.

The model never guesses "today". The clock comes from the server (time zone DJORGE_TZ, default America/Sao_Paulo)
or from the request (`now`, ISO 8601), and every date in the text is resolved against it:

  absolute   15/03/2025, 15-03, 2025-03-15, 3 de março (de 2025), março de 2025, 15/03 às 14h30
  relative   hoje, amanhã, ontem, anteontem, depois de amanhã, há 3 dias, daqui a duas semanas, em 10 dias,
             semana que vem / passada, próximo mês, mês passado, próxima sexta, sexta passada, sexta-feira
  English    today, tomorrow, yesterday, in 3 days, 3 days ago, next friday, last week, March 3, 2025

Used by the runtime:
  * decide(node, text)  NOUL / CHOICE about deadlines ("o prazo já venceu?", "vencido | no prazo | vence hoje",
                        "qual data é a mais próxima?") answered from the clock, with the dates as evidence;
                        returns None when the text is ambiguous (several deadlines, no date) -> the model decides.
  * annotate(text)      every date in the text with ISO value, weekday, days from now and past/today/future.
Pure Python, no network, safe on untrusted input (bounded text, bounded numbers).
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

MAX_TEXT = 200_000
import contextvars
REF = contextvars.ContextVar("djorge_now", default=None)   # per-request reference time (ISO)

MONTHS = {"janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "março": 3, "marco": 3, "mar": 3, "abril": 4, "abr": 4,
          "maio": 5, "mai": 5, "junho": 6, "jun": 6, "julho": 7, "jul": 7, "agosto": 8, "ago": 8,
          "setembro": 9, "set": 9, "outubro": 10, "out": 10, "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
          "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
          "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "december": 12, "dec": 12,
          "feb": 2, "apr": 4, "aug": 8}
WEEKDAYS = {"segunda": 0, "segunda-feira": 0, "terça": 1, "terca": 1, "terça-feira": 1, "terca-feira": 1,
            "quarta": 2, "quarta-feira": 2, "quinta": 3, "quinta-feira": 3, "sexta": 4, "sexta-feira": 4,
            "sábado": 5, "sabado": 5, "domingo": 6, "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
WD_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
UNITS = {"dia": 1, "dias": 1, "day": 1, "days": 1, "semana": 7, "semanas": 7, "week": 7, "weeks": 7,
         "mês": 30, "mes": 30, "meses": 30, "month": 30, "months": 30, "ano": 365, "anos": 365, "year": 365,
         "years": 365}
NUMW = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
        "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30,
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10}
RELWORD = {"hoje": 0, "today": 0, "amanhã": 1, "amanha": 1, "tomorrow": 1, "ontem": -1, "yesterday": -1,
           "anteontem": -2, "depois de amanhã": 2, "depois de amanha": 2}

_N = r"(\d{1,3}|" + "|".join(sorted(NUMW, key=len, reverse=True)) + r")"
_U = r"(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")"
_M = r"(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_W = r"(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")"
_TIME = r"(?:\s*(?:às|as|at|,)?\s*(\d{1,2})(?:[:h](\d{2})?)\s*(?:h|hs|horas)?)?"

PATTERNS = [   # (kind, regex) - order matters: longer / more specific first
    ("iso", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?\b")),
    ("dmy", re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b" + _TIME, re.I)),
    ("dm", re.compile(r"\b(\d{1,2})/(\d{1,2})\b(?![/.-]\d)" + _TIME, re.I)),
    ("d_de_m", re.compile(r"\b(\d{1,2})(?:º|o)?\s+de\s+" + _M + r"(?:\s+de\s+(\d{4}))?" + _TIME, re.I)),
    ("m_d_y", re.compile(r"\b" + _M + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?\b", re.I)),
    ("m_de_y", re.compile(r"\b" + _M + r"\s+de\s+(\d{4})\b", re.I)),
    ("ago", re.compile(r"\b(?:há|ha)\s+(?:cerca\s+de\s+)?" + _N + r"\s+" + _U + r"(?:\s+atrás)?\b|\b" + _N + r"\s+"
                       + _U + r"\s+(?:atrás|atras|ago)\b", re.I)),
    ("ahead", re.compile(r"\b(?:daqui\s+a|dentro\s+de|em|in|within)\s+" + _N + r"\s+" + _U + r"\b", re.I)),
    ("rel", re.compile(r"\b(depois de amanhã|depois de amanha|anteontem|amanhã|amanha|ontem|hoje|today|tomorrow|"
                       r"yesterday)\b" + _TIME, re.I)),
    ("wd_next", re.compile(r"\b(?:próxima|proxima|próximo|proximo|next|nesta|neste|esta|este|this)\s+" + _W + r"\b|\b"
                           + _W + r"\s+(?:que vem|próxima|proxima)\b", re.I)),
    ("wd_last", re.compile(r"\b(?:última|ultima|último|ultimo|last)\s+" + _W + r"\b|\b" + _W
                           + r"\s+(?:passada|passado)\b", re.I)),
    ("period", re.compile(r"\b(semana|mês|mes|ano|week|month|year)\s+(que vem|passad[oa]|seguinte)\b|\b(próxim[oa]|"
                          r"proxim[oa]|next|last|últim[oa]|ultim[oa])\s+(semana|mês|mes|ano|week|month|year)\b", re.I)),
    ("wd", re.compile(r"\b(?:na|no|on|até|ate|até a|até o)?\s*" + _W + r"\b(?!\s*(?:passad|que vem))", re.I)),
]
DEADLINE_CUE = re.compile(r"\b(vence|vencimento|venceu|vencid[oa]s?|prazo|até|ate|validade|válid[oa]|valid[oa]|"
                          r"expira|expirou|expiração|limite|due|deadline|expires?|until|entrega|pagar até|"
                          r"pagamento até|data limite)\b", re.I)


def tz_name() -> str:
    return os.environ.get("DJORGE_TZ", "America/Sao_Paulo")


def now(ref: Optional[str] = None) -> datetime:
    """Current local time (DJORGE_TZ) or the request's reference time (ISO 8601)."""
    ref = ref or REF.get()
    if ref:
        d = datetime.fromisoformat(str(ref).replace("Z", "+00:00"))
        return d
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name()))
    except Exception:
        return datetime.now()


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s.casefold()).encode("ascii", "ignore").decode()


def _num(tok: str) -> Optional[int]:
    if tok is None:
        return None
    if tok.isdigit():
        v = int(tok)
        return v if v <= 3650 else None
    return NUMW.get(tok.casefold())


def _mk(y, m, d) -> Optional[date]:
    try:
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def _year(y, today: date) -> int:
    y = int(y)
    return y + 2000 if y < 100 else y


def _nearest_year(m, d, today: date, prefer: str = "auto") -> Optional[date]:
    """'15/03' without a year: this year's date; with a deadline cue, the next occurrence is not assumed -
    the year is today's (a bill 'vence 15/03' read in December is past, which is the safe reading)."""
    return _mk(today.year, m, d)


def _add_months(d0: date, k: int) -> date:
    m = d0.month - 1 + k
    y = d0.year + m // 12
    m = m % 12 + 1
    for day in (d0.day, 30, 29, 28):
        x = _mk(y, m, day)
        if x:
            return x
    return d0


def find_dates(text: str, ref: Optional[datetime] = None) -> List[Dict]:
    """Every date expression with its resolved date (and time when stated)."""
    text = (text or "")[:MAX_TEXT]
    ref = ref or now()
    today = ref.date()
    out, used = [], []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            s, e = m.start(), m.end()
            seg = text[s:e]
            lead = len(seg) - len(seg.lstrip())
            s += lead
            if e <= s or any(s < b and a < e for a, b in used):
                continue
            g = m.groups()
            d, hh, mm, precision = None, None, None, "day"
            try:
                if kind == "iso":
                    d = _mk(g[0], g[1], g[2]); hh, mm = g[3], g[4]
                elif kind == "dmy":
                    d = _mk(_year(g[2], today), g[1], g[0]); hh, mm = g[3], g[4]
                elif kind == "dm":
                    left = _fold(text[max(0, s - 18):s])
                    if int(g[1]) > 12 or not (g[2] or re.search(
                            r"(dia|ate|em|vence\w*|data|no|na|para|prazo|desde|entre|a partir de|validade|:)\s*$", left)):
                        continue                                   # 2/3, 3/45: fractions, not dates
                    d = _nearest_year(g[1], g[0], today); hh, mm = g[2], g[3]
                elif kind == "d_de_m":
                    d = _mk(g[2] or today.year, MONTHS[g[1].casefold().rstrip(".")], g[0]); hh, mm = g[3], g[4]
                elif kind == "m_d_y":
                    d = _mk(g[2] or today.year, MONTHS[g[0].casefold().rstrip(".")], g[1])
                elif kind == "m_de_y":
                    d = _mk(g[1], MONTHS[g[0].casefold().rstrip(".")], 1); precision = "month"
                elif kind in ("ago", "ahead"):
                    n = _num(g[0] or g[2]) if kind == "ago" else _num(g[0])
                    u = (g[1] or g[3]) if kind == "ago" else g[1]
                    if n is None:
                        continue
                    k = UNITS[u.casefold()]
                    sign = -1 if kind == "ago" else 1
                    if k == 30:
                        d = _add_months(today, sign * n)
                    elif k == 365:
                        d = _mk(today.year + sign * n, today.month, min(today.day, 28 if today.month == 2 else today.day))
                    else:
                        d = today + timedelta(days=sign * n * k)
                    precision = "day" if k < 30 else ("month" if k == 30 else "year")
                elif kind == "rel":
                    d = today + timedelta(days=RELWORD[g[0].casefold()]); hh, mm = g[1], g[2]
                elif kind in ("wd_next", "wd_last", "wd"):
                    w = WEEKDAYS[(g[0] or g[1]).casefold()] if kind != "wd" else WEEKDAYS[g[0].casefold()]
                    delta = (w - today.weekday()) % 7
                    if kind == "wd_last":
                        delta = delta - 7 if delta else -7
                    elif kind == "wd_next":
                        delta = delta or 7
                    d = today + timedelta(days=delta)                 # bare weekday: the next one (or today)
                elif kind == "period":
                    unit = _fold(g[0] or g[3]); rel = _fold(g[1] or g[2])
                    sign = -1 if rel.startswith(("passad", "ultim", "last")) else 1
                    if unit in ("semana", "week"):
                        d = today + timedelta(days=7 * sign); precision = "week"
                    elif unit in ("mes", "month"):
                        d = _add_months(today, sign).replace(day=1); precision = "month"
                    else:
                        d = _mk(today.year + sign, 1, 1); precision = "year"
            except (KeyError, ValueError, TypeError, OverflowError):
                d = None
            if d is None:
                continue
            used.append((s, e))
            item = {"text": text[s:e].strip(), "start": s, "end": e, "kind": kind, "date": d.isoformat(),
                    "precision": precision}
            if hh is not None and str(hh).isdigit() and int(hh) < 24:
                mi = int(mm) if mm and str(mm).isdigit() and int(mm) < 60 else 0
                item["time"] = f"{int(hh):02d}:{mi:02d}"
            out.append(item)
    out.sort(key=lambda x: x["start"])
    return out


def relation(item: Dict, ref: datetime) -> Dict:
    d = date.fromisoformat(item["date"])
    today = ref.date()
    days = (d - today).days
    rel = "hoje" if days == 0 else ("passado" if days < 0 else "futuro")
    if item.get("precision") == "month" and (d.year, d.month) == (today.year, today.month):
        rel = "este mês"
    if rel == "hoje" and item.get("time"):
        h, mi = map(int, item["time"].split(":"))
        t = ref.replace(hour=h, minute=mi, second=0, microsecond=0)
        rel = "hoje (já passou)" if t < ref else "hoje (ainda não)"
    return dict(item, weekday=WD_PT[d.weekday()], days_from_now=days, relation=rel)


def annotate(text: str, ref: Optional[str] = None) -> Dict:
    r = now(ref)
    return {"now": r.isoformat(timespec="seconds"), "timezone": tz_name() if not ref else "request",
            "weekday": WD_PT[r.weekday()], "dates": [relation(x, r) for x in find_dates(text, r)]}


def deadline(text: str, ref: Optional[datetime] = None) -> Optional[Dict]:
    """The one date the text treats as a deadline (nearest after a cue like 'vence', 'prazo', 'até').
    None when there is no date or several competing deadlines."""
    ref = ref or now()
    ds = find_dates(text, ref)
    if not ds:
        return None
    cued = []
    for x in ds:
        left = text[max(0, x["start"] - 40):x["start"]]
        if DEADLINE_CUE.search(left):
            cued.append(x)
    pool = cued or (ds if len(ds) == 1 else [])
    if len({p["date"] for p in pool}) != 1:
        return None
    return relation(pool[0], ref)


# ------------------------------------------------------------------------- decisions answered from the clock --
EXPIRED = re.compile(r"\b(venceu|vencid[oa]s?|expirou|expirad[oa]s?|passou|atrasad[oa]s?|fora do prazo|já era|"
                     r"perdeu o prazo|overdue|expired|is past|has passed|late)\b", re.I)
VALID = re.compile(r"\b(no prazo|dentro do prazo|em dia|válid[oa]|valid[oa]|ainda (?:não )?(?:venceu|vale|está)|"
                   r"a vencer|não venceu|nao venceu|on time|still valid|not (?:yet )?(?:due|expired))\b", re.I)
TODAY = re.compile(r"\b(vence hoje|é hoje|e hoje|hoje|today|due today)\b", re.I)
SOONER = re.compile(r"\b(mais próxim[oa]|mais proxim[oa]|primeir[oa]|mais cedo|antes|earliest|soonest|first)\b", re.I)
LATER = re.compile(r"\b(mais distante|últim[oa]|ultim[oa]|mais tarde|depois|latest|last)\b", re.I)
RECENT = re.compile(r"\b(mais recente|most recent)\b", re.I)


def _state(dl: Dict) -> str:
    if dl["days_from_now"] < 0 or dl["relation"] == "hoje (já passou)":
        return "expired"
    if dl["days_from_now"] == 0:
        return "today"
    return "valid"


def _label_state(label: str) -> Optional[str]:
    if TODAY.search(label) and not EXPIRED.search(label):
        return "today"
    if VALID.search(label):
        return "valid"
    if EXPIRED.search(label):
        return "expired"
    return None


def decide(node, text: str, ref: Optional[datetime] = None) -> Optional[Dict]:
    """Deterministic answer for a temporal Decision, or None (the model decides)."""
    if os.environ.get("DJORGE_TEMPORAL", "1") == "0":
        return None
    ref = ref or now()
    ask = (getattr(node, "ask", "") or "")
    if node.kind == "NOUL":
        prop = node.options[0]
        neg = bool(re.search(r"\b(não|nao|not|ainda)\b", prop, re.I)) and VALID.search(prop)
        want = "valid" if neg or VALID.search(prop) else ("expired" if EXPIRED.search(prop)
                                                           else ("today" if TODAY.search(prop) else None))
        if want is None:
            return None
        dl = deadline(text, ref)
        if dl is None:
            return None
        st = _state(dl)
        ans = st == want or (want == "valid" and st == "today")
        return {"type": "distribution", "decision": "NOUL", "answer": ans, "p_true": 1.0 if ans else 0.0,
                "probability": 1.0, "confidence": 1.0, "grounded": True, "grounding": "temporal_rule",
                "engine": "deterministic", "evidence": {"deadline": dl, "now": ref.isoformat(timespec="minutes")}}
    if node.kind == "CHOICE":
        opts = list(node.options)
        states = [_label_state(o) for o in opts]
        if sum(s is not None for s in states) >= 2 and (DEADLINE_CUE.search(ask) or DEADLINE_CUE.search(text)):
            dl = deadline(text, ref)
            if dl is None:
                return None
            st = _state(dl)
            pick = [o for o, s in zip(opts, states) if s == st] or \
                   ([o for o, s in zip(opts, states) if s == "valid"] if st == "today" else [])
            if len(pick) != 1:
                return None
            return _choice(opts, pick[0], {"deadline": dl, "now": ref.isoformat(timespec="minutes")})
        # options that are themselves dates: "qual vence primeiro?" / "qual a mais recente?"
        ds = [find_dates(o, ref) for o in opts]
        if all(len(x) == 1 for x in ds) and len(opts) >= 2 and (SOONER.search(ask) or LATER.search(ask)
                                                               or RECENT.search(ask)):
            key = [(date.fromisoformat(x[0]["date"]), x[0].get("time", "")) for x in ds]
            if RECENT.search(ask):
                cand = [i for i, k in enumerate(key) if k[0] <= ref.date()] or list(range(len(opts)))
                i = max(cand, key=lambda j: key[j])
            elif SOONER.search(ask):
                fut = [i for i, k in enumerate(key) if k[0] >= ref.date()]
                i = min(fut or range(len(opts)), key=lambda j: key[j]) if re.search(r"próxim|proxim|soonest", ask, re.I) \
                    else min(range(len(opts)), key=lambda j: key[j])
            else:
                i = max(range(len(opts)), key=lambda j: key[j])
            if sum(k == key[i] for k in key) > 1:
                return None
            return _choice(opts, opts[i], {"dates": [relation(x[0], ref) for x in ds],
                                           "now": ref.isoformat(timespec="minutes")})
    return None


def _choice(opts, ans, ev):
    return {"type": "distribution", "decision": "CHOICE", "answer": ans, "probability": 1.0, "confidence": 1.0,
            "grounded": True, "grounding": "temporal_rule", "engine": "deterministic", "evidence": ev,
            "distribution": {o: (1.0 if o == ans else 0.0) for o in opts}}
