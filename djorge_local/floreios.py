"""Tom e enfeites curtos de alertas (frases de modelo; nunca texto livre).

  tom(texto, categoria=None, engine=None) -> "feliz" | "serio" | "neutro"
  enfeitar(titulo, mensagem, tom, ocasiao=None, nome_usuario=None, seed=None, ativo=True) -> {titulo, mensagem, icone}
ocasiao: None | "aniversario" (pessoa=...) | "feriado" (nome=..., quando="hoje"|"amanha"|...) | "comemorativa" | "conquista"
  Pode ser str ("aniversario") ou dict {"tipo": "aniversario", "pessoa": "Ana"} / {"tipo": "feriado", "nome": "Finados", "quando": "amanhã"}.
"""
import hashlib
import random
import re
import unicodedata
from typing import Dict, Optional

TONS = ("feliz", "serio", "neutro")
CHOICE_OPCOES = ["alegre ou comemorativo", "sério, urgente ou preocupante", "neutro ou rotineiro"]

_SERIO = re.compile(r"\b(urgente|atrasad[oa]|vencid[oa]|vence hoje|multa|juros|prazo final|ultimo dia|cancelad[oa]|"
                    r"erro|falha|falhou|exame|resultado|hospital|medic[oa]|remedio|medicamento|cirurgia|consulta|"
                    r"advogad|processo|audiencia|tribunal|policia|seguranca|senha|fraude|cobranca|divida|"
                    r"importante|atencao|critico|emergencia|velorio|enterro|luto|falecimento|missa de setimo|"
                    r"demissao|imposto|darf|boleto)\b")
_FELIZ = re.compile(r"\b(aniversario|parabens|festa|comemora|celebra|conquista|conclu|consegui|aprovad[oa]|"
                    r"formatura|casamento|nasciment|ferias|viagem|feriado|natal|ano novo|reveillon|promocao|"
                    r"show|presente|bonus|meta batida|batemos|uhul|uhuul|parabens|namorad|dia das maes|dia dos pais)\b")
_CAT = {"aniversario": "feliz", "comemorativa": "feliz", "conquista": "feliz", "feriado": "feliz",
        "financeiro": "serio", "saude": "serio", "prazo": "serio", "erro": "serio", "seguranca": "serio",
        "pagamento": "serio", "rotina": "neutro", "lembrete": "neutro"}


def _fold(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")


def tom(texto, categoria: Optional[str] = None, engine=None) -> str:
    c = _fold(categoria)
    if c in _CAT:
        return _CAT[c]
    t = _fold(texto)
    s, f = bool(_SERIO.search(t)), bool(_FELIZ.search(t))
    if s and not f:
        return "serio"
    if f and not s:
        return "feliz"
    if engine is not None and t.strip():
        try:
            from djorge.runtime.semantic import SemReq
            r = engine.run([SemReq("CHOICE", str(texto)[:1000], options=CHOICE_OPCOES)])[0]
            a = (r or {}).get("answer")
            if a in CHOICE_OPCOES:
                return TONS[CHOICE_OPCOES.index(a)]
        except Exception:
            pass
    return "serio" if s else "neutro"  # empate sem modelo: na dúvida, sério (não enfeita algo grave)


_FELIZ_T = ["Uhuul! {t}", "Que dia bom: {t}", "Oba! {t}", "{t} 🎉"]
_FELIZ_M = ["{m}", "{m} Aproveite!", "{m} Bom demais!", "{m}"]
_CONQ_T = ["Parabéns{n}!", "Uhuul{n}!", "Mandou bem{n}!"]
_ANIV_M = ["Hoje é aniversário de {p} — que tal mandar os parabéns?",
           "{p} faz aniversário hoje. Uma mensagem cai bem!",
           "Dia de festa: aniversário de {p}. Não esqueça os parabéns!"]
_ANIV_PROX = "Aniversário de {p} {q} — já pensou no presente?"
_FER_M = ["{Q} é feriado ({f}) — {m}", "{Q} é feriado ({f}). {m}", "Feriado de {f} {q}: {m}"]
_COMEM_M = ["{Q} é {f}! {m}", "{f} {q} — {m}"]
_SERIO_T = ["Atenção: {t}", "Importante: {t}"]
_ICONE = {"feliz": "🎉", "serio": "⚠️", "neutro": "🔔", "aniversario": "🎂", "feriado": "📅", "comemorativa": "🎈",
          "conquista": "🏆"}


def _rng(seed, *partes):
    if seed is None:
        return random.Random()
    h = hashlib.sha1(("|".join(map(str, (seed,) + partes))).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def _limpo(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def enfeitar(titulo, mensagem, tom: str = "neutro", ocasiao=None, nome_usuario: Optional[str] = None,
             seed=None, ativo: bool = True) -> Dict:
    t, m = _limpo(titulo), _limpo(mensagem)
    if not ativo or tom not in TONS:
        return {"titulo": t, "mensagem": m, "icone": _ICONE["neutro"] if ativo else ""}
    oc = ocasiao if isinstance(ocasiao, dict) else ({"tipo": ocasiao} if ocasiao else {})
    tipo = _fold(oc.get("tipo"))
    r = _rng(seed, t, m, tipo)
    n = f", {_limpo(nome_usuario)}" if nome_usuario else ""
    q = _limpo(oc.get("quando") or "hoje")
    Q = q[:1].upper() + q[1:]

    if tom == "serio":  # sem enfeites, só um prefixo de seriedade (sem duplicar)
        tt = t if re.match(r"(?i)(aten[cç][aã]o|importante|urgente)\b", t) else r.choice(_SERIO_T).format(t=t)
        return {"titulo": tt, "mensagem": m, "icone": _ICONE["serio"]}
    if tom == "neutro" and tipo not in ("aniversario", "feriado", "comemorativa"):
        return {"titulo": t, "mensagem": m, "icone": _ICONE["neutro"]}

    if tipo == "aniversario":
        p = _limpo(oc.get("pessoa")) or (re.sub(r"(?i)^anivers[aá]rio\s+d[aeo]\s+", "", t) or "alguém especial")
        if q.lower() == "hoje":
            msg = r.choice(_ANIV_M).format(p=p)
        else:
            msg = _ANIV_PROX.format(p=p, q=q)
        if m and _fold(m) not in _fold(msg) and not _fold(m).startswith("aniversario"):
            msg = f"{msg} {m}"
        return {"titulo": f"Aniversário de {p} 🎂" if q.lower() == "hoje" else f"Aniversário de {p}",
                "mensagem": msg, "icone": _ICONE["aniversario"]}
    if tipo in ("feriado", "comemorativa"):
        f = _limpo(oc.get("nome")) or t
        pool = _FER_M if tipo == "feriado" else _COMEM_M
        msg = r.choice(pool).format(Q=Q, q=q, f=f, m=m or "bom descanso!" if tipo == "feriado" else m or "aproveite!")
        return {"titulo": t or f, "mensagem": msg.strip(), "icone": _ICONE[tipo]}
    if tipo == "conquista":
        return {"titulo": r.choice(_CONQ_T).format(n=n), "mensagem": m or t, "icone": _ICONE["conquista"]}
    # feliz genérico: um único enfeite, sem empilhar exclamações
    tt = t if re.match(r"(?i)(uhu+l|oba|parab[eé]ns|que dia bom)", t) else r.choice(_FELIZ_T).format(t=t)
    mm = r.choice(_FELIZ_M).format(m=m).strip() if m else m
    if nome_usuario and r.random() < 0.3 and mm:
        mm = f"{_limpo(nome_usuario)}, {mm[:1].lower()}{mm[1:]}"
    return {"titulo": tt, "mensagem": mm, "icone": _ICONE["feliz"]}
