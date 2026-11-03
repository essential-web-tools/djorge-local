"""Contatos: junta as pessoas dos cards, das notificações/mensagens e das integrações conectadas (se tiverem
`contacts()`). Cada contato: {nome (Maiúsculas Iniciais), email, telefone, fontes[], vezes}."""
from __future__ import annotations

import re
from typing import Dict, List

from .tree import fold

MINUSC = {"da", "de", "do", "das", "dos", "e"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
FONE = re.compile(r"(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}")


def nome_fmt(s: str) -> str:
    ws = " ".join(str(s or "").replace("_", " ").split()).split(" ")
    return " ".join(w.lower() if w.lower() in MINUSC and i else (w if w.isupper() and 2 <= len(w) <= 4 and i else
                    w[:1].upper() + w[1:].lower()) for i, w in enumerate(ws) if w)


def fone_fmt(s: str) -> str:
    d = re.sub(r"\D", "", str(s or ""))
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return str(s or "").strip()


def _do_remetente(rem: str) -> Dict:
    rem = str(rem or "").strip()
    m = EMAIL.search(rem)
    email = m.group(0).lower() if m else None
    nome = re.sub(r"<[^>]*>|\"", "", rem).replace(email or "\0", "").strip(" <>\"'")
    if not nome and email:
        try:
            from djorge.runtime.recognizers import nome_do_email
            nome = nome_do_email(email).get("entidade") or email.split("@")[0]
        except Exception:
            nome = email.split("@")[0]
    f = FONE.search(rem) if not email else None
    return {"nome": nome, "email": email, "telefone": fone_fmt(f.group(0)) if f else None}


def listar(app, q: str = "", limit: int = 50) -> List[Dict]:
    pessoas: Dict[str, Dict] = {}

    def add(nome, email=None, telefone=None, fonte="cards"):
        nome = nome_fmt(nome) if nome else ""
        email = (email or "").strip().lower() or None
        telefone = fone_fmt(telefone) if telefone else None
        if not (nome or email or telefone):
            return
        key = email or (re.sub(r"\D", "", telefone) if telefone else None) or fold(nome)
        alvo = pessoas.get(key) or next((p for p in pessoas.values() if nome and fold(p["nome"]) == fold(nome)
                                         and (not email or not p["email"])), None)
        if alvo is None:
            alvo = pessoas.setdefault(key, {"nome": nome or (email or telefone), "email": email,
                                            "telefone": telefone, "fontes": [], "vezes": 0})
        alvo["email"] = alvo["email"] or email
        alvo["telefone"] = alvo["telefone"] or telefone
        if nome and (alvo["nome"] == alvo["email"] or alvo["nome"] == alvo["telefone"]):
            alvo["nome"] = nome
        if fonte not in alvo["fontes"]:
            alvo["fontes"].append(fonte)
        alvo["vezes"] += 1

    try:
        for c in app.cards.all():
            for p in c.get("people") or []:
                add(p.get("nome"), p.get("email"), p.get("telefone"), "cards")
    except Exception:
        pass
    store = getattr(app, "notificacoes", None)
    if store is not None:
        try:
            for n in store.list(limit=500, arquivada="todas"):
                if n.get("remetente"):
                    d = _do_remetente(n["remetente"])
                    add(d["nome"], d["email"] or (n.get("dados") or {}).get("remetente_email"),
                        d["telefone"] or (n.get("dados") or {}).get("telefone"), n.get("app") or "notificações")
        except Exception:
            pass
    integ = getattr(app, "integrations", None)
    if integ is not None and hasattr(integ, "contacts"):
        try:
            for p in integ.contacts(q) or []:
                add(p.get("nome") or p.get("name"), p.get("email"), p.get("telefone") or p.get("phone"),
                    p.get("fonte") or p.get("integration") or "integrações")
        except Exception:
            pass
    out = list(pessoas.values())
    if q:
        fq, dq = fold(q), re.sub(r"\D", "", q)
        out = [p for p in out if fq in fold(p["nome"]) or (p["email"] and fq in p["email"])
               or (dq and p["telefone"] and dq in re.sub(r"\D", "", p["telefone"]))]
    out.sort(key=lambda p: (-p["vezes"], fold(p["nome"])))
    return out[:limit]
