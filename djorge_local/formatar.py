"""Formatação de tudo que o Djorge mostra (respostas, cards, campos), em pt-BR:

    nome("ana maria da silva")          -> "Ana Maria da Silva"
    data("2026-11-03T14:00")            -> "ter., 3 de nov. às 14:00"   (hoje/amanhã por extenso)
    hora("9h") / hora("14:5")           -> "09:00" / "14:05"
    valor(1250) / valor("1250.5")       -> "R$ 1.250,00" / "R$ 1.250,50"
    titulo("de pagar a conta de luz às") -> "Pagar a conta de luz"
    frase("criei o card.")              -> "Criei o card."
    card(c) / resposta(out)             -> cópias formatadas (título, pessoas, quando_fmt, valor_fmt dos campos)

Só biblioteca padrão; nada aqui muda o sentido do texto, só a forma.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Dict, Iterable, Optional

PARTICULAS = {"da", "de", "do", "das", "dos", "e", "d'", "di", "du", "van", "von", "del", "la"}
SIGLAS = {"rh", "ti", "ong", "cpf", "cnpj", "sus", "inss", "ibge", "usp", "ufrj", "bb", "cef", "xp", "pj", "pf"}
SEM_CURTO = ["seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom."]
MES_CURTO = ["jan.", "fev.", "mar.", "abr.", "maio", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."]
SOLTAS = {"às", "as", "a", "à", "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em", "para", "pra", "pro",
          "e", "o", "que", "com", "até", "ate", "por", "pelas", "pelo", "pela", "ao", "dia", "toda", "todo", "todos",
          "todas", "hoje", "volta", "lá", "la", "desde", "entre", "sobre", "ou"}


def _palavra(w: str, primeira: bool, caixa_alta: bool = False) -> str:
    if not w:
        return w
    low = w.casefold()
    if not primeira and low in PARTICULAS:
        return low
    if low in SIGLAS or (not caixa_alta and w.isupper() and 2 <= len(w) <= 4 and w.isalpha()):
        return w.upper()
    if "-" in w:
        return "-".join(_palavra(x, primeira or i > 0) if x.casefold() not in PARTICULAS else x.casefold()
                        for i, x in enumerate(w.split("-")))
    if low.startswith(("d'", "d’")) and len(w) > 2:
        return low[:2] + w[2:3].upper() + w[3:].lower()
    if re.search(r"\d", w) or "@" in w or "." in w[1:-1]:
        return w                                  # e-mails, sites, códigos: como vieram
    return w[:1].upper() + w[1:].lower()


def nome(s) -> str:
    """Nome de pessoa/empresa em Maiúsculas Iniciais, com 'da/de/do/dos/das/e' em minúsculas no meio."""
    s = " ".join(str(s or "").split())
    if not s:
        return s
    ws = s.split(" ")
    altas = [w for w in ws if w.isalpha() and w.isupper()]
    alta = len(altas) >= 2 and len(altas) * 2 >= len(ws)   # "JOÃO DOS SANTOS": nome em caixa alta não é sigla
    return " ".join(_palavra(w, i == 0, alta) for i, w in enumerate(ws))


def hora(s) -> str:
    """'9h' '9:00' '14h30' '14:5' -> 'HH:MM'. O que não for hora volta como veio."""
    if s is None:
        return ""
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:[:h]\s*(\d{1,2})?)?\s*(?:min)?\s*", str(s))
    if not m:
        return str(s)
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else str(s)


def data(iso, hoje: Optional[_dt.date] = None, relativo: bool = True, com_hora: bool = True) -> str:
    """'2026-11-03T14:00' -> 'ter., 3 de nov. às 14:00' (ano só se for outro; 'hoje'/'amanhã' quando couber)."""
    if not iso:
        return ""
    s = str(iso)
    try:
        d = _dt.date.fromisoformat(s[:10])
    except ValueError:
        return s
    hoje = hoje or _dt.date.today()
    if relativo and d == hoje:
        t = "hoje"
    elif relativo and d == hoje + _dt.timedelta(1):
        t = "amanhã"
    elif relativo and d == hoje - _dt.timedelta(1):
        t = "ontem"
    else:
        t = f"{SEM_CURTO[d.weekday()]}, {d.day} de {MES_CURTO[d.month - 1]}"
        if d.year != hoje.year:
            t += f" de {d.year}"
    if com_hora and len(s) > 10 and s[10] in "T ":
        t += f" às {hora(s[11:16])}"
    return t


def intervalo(ini, fim=None, hoje: Optional[_dt.date] = None) -> str:
    """Início e fim no mesmo dia: 'ter., 3 de nov. das 14:00 às 16:00'."""
    if not ini:
        return ""
    if fim and len(str(ini)) > 10 and len(str(fim)) > 10 and str(ini)[:10] == str(fim)[:10]:
        return f"{data(str(ini)[:10], hoje)} das {hora(str(ini)[11:16])} às {hora(str(fim)[11:16])}"
    return data(ini, hoje)


def valor(v, moeda: str = "R$") -> str:
    """1250 / '1250.5' / '1.250,5' / 'R$ 2k' -> 'R$ 1.250,00'."""
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip().replace("R$", "").strip()
        mult = 1000 if re.search(r"\d\s*(?:k|mil)\b", s, re.I) else 1
        s = re.sub(r"(?i)\s*(?:k|mil|reais)\b", "", s).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", s) and not re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
            x = float(s)
        elif re.fullmatch(r"[\d.]+(?:,\d+)?", s):
            x = float(s.replace(".", "").replace(",", "."))
        else:
            return str(v)
        x *= mult
    t = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{moeda} {t}"


def frase(s) -> str:
    """Primeira letra maiúscula e espaços limpos (não mexe no resto)."""
    s = " ".join(str(s or "").split())
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"(?<!\.)\.\.(?!\.)", ".", s)                    # "nov.." -> "nov."
    i = next((k for k, c in enumerate(s) if c.isalnum()), None)
    if i is None or not s[i].isalpha():
        return s                                              # começa com número ("25/12 cai...")
    return s[:i] + s[i].upper() + s[i + 1:]


def titulo(s, nomes: Iterable[str] = ()) -> str:
    """Título limpo: sem preposição solta no começo/fim, sem pontuação sobrando, primeira letra maiúscula e os
    nomes conhecidos (pessoas, empresas) em Maiúsculas Iniciais."""
    s = " ".join(str(s or "").replace("“", "").replace("”", "").split())
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    ws = s.split(" ") if s else []
    while ws and ws[-1].casefold().strip(",.;:!?-") in SOLTAS:
        ws.pop()
    while ws and ws[0].casefold().strip(",.;:!?-") in SOLTAS:
        ws.pop(0)
    s = " ".join(ws).strip(" ,.;:!?-–")
    s = re.sub(r"\b(\w+)( \1\b)+", r"\1", s, flags=re.I)            # "com com" -> "com"
    for n in sorted({x for x in nomes if x}, key=len, reverse=True):
        s = re.sub(rf"(?i)(?<!\w){re.escape(n)}(?!\w)", nome(n), s)
    return frase(s)[:200]


def card(c: Dict, hoje: Optional[_dt.date] = None) -> Dict:
    """Cópia do card com título e pessoas formatados e 'quando_fmt' para mostrar."""
    if not isinstance(c, dict):
        return c
    out = dict(c)
    people = []
    for p in c.get("people") or []:
        if isinstance(p, dict):
            p = dict(p)
            if p.get("nome") and "@" not in str(p["nome"]):
                p["nome"] = nome(p["nome"])
        people.append(p)
    if "people" in c:
        out["people"] = people
    if c.get("title"):
        out["title"] = titulo(c["title"], [p.get("nome") for p in people if isinstance(p, dict)])
    w = c.get("start") or c.get("due")
    if w:
        out["quando_fmt"] = intervalo(w, c.get("end"), hoje) if c.get("start") else data(w, hoje)
    return out


def campo_fmt(campo: str, valor_, texto: str = "", hoje: Optional[_dt.date] = None) -> str:
    """O valor de um campo reconhecido pronto para mostrar."""
    if valor_ in (None, ""):
        return frase(texto) if texto else ""
    if campo == "data":
        return data(valor_, hoje)
    if campo == "hora":
        return hora(valor_)
    if campo == "valor":
        return valor(valor_)
    if campo in ("entidade", "pessoa", "empresa ou estabelecimento"):
        return nome(valor_)
    if campo == "duração":
        try:
            m = int(valor_)
            return f"{m // 60}h{m % 60:02d}" if m >= 60 and m % 60 else f"{m // 60}h" if m >= 60 else f"{m} min"
        except (TypeError, ValueError):
            return str(valor_)
    if campo == "frequência ou repetição":
        return frase(valor_)
    return str(valor_)


def resposta(out: Dict, hoje: Optional[_dt.date] = None) -> Dict:
    """Aplica a formatação a uma resposta do comando (no lugar): reply, titulo, campos e cards dos resultados."""
    if out.get("reply"):
        out["reply"] = frase(out["reply"])
    for f in out.get("fields") or []:
        f["valor_fmt"] = campo_fmt(f.get("campo"), f.get("valor"), f.get("texto", ""), hoje)
    if out.get("titulo"):
        out["titulo"] = titulo(out["titulo"])
    p = out.get("proposal") or {}
    if isinstance(p.get("card"), dict):
        p["card"] = card(p["card"], hoje)
    if p.get("name"):
        p["name"] = nome(p["name"])
    res = []
    for r in out.get("results") or []:
        res.append(card(r, hoje) if isinstance(r, dict) and "kind" in r and "title" in r else r)
    if "results" in out:
        out["results"] = res
    return out
