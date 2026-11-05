"""Prioridade dinâmica dos cards e perguntas sobre informações que faltam.

  calcular(card, agora, cards_do_dia) -> {nivel 0..3, motivo, conflito, ordem}
      A prioridade sobe quanto mais perto do prazo/início e com a carga do dia (outras tarefas importantes
      no mesmo dia: conflito + ordem sugerida). Só regras, leve: roda ao listar (com intervalo).
  origem(card) -> "manual" | "auto"   (prioridade escolhida pela pessoa nunca é sobrescrita)
  recalcular(store, agora) -> nº de cards atualizados (só os de origem auto)
  faltantes(card, nao_perguntar=()) -> [{campo, pergunta, tipo}]   (cards vindos de notificação/automação/comando)
  responder(card, campo, valor) -> patch para Cards.update
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from typing import Dict, Iterable, List, Optional

ORIGENS_PERGUNTA = ("notificacao", "automacao", "comando")
CAMPOS = {"prazo": ("Qual o prazo?", "date"), "horario": ("Que horas?", "time"), "local": ("Onde vai ser?", "text"),
          "valor": ("Qual o valor?", "text"), "responsavel": ("Quem é o responsável?", "text")}
_PAG = re.compile(r"\b(pagar|pagamento|boleto|fatura|conta de|pix|transferir|transferencia|mensalidade|aluguel|cobranca)\b")
_DINHEIRO = re.compile(r"(r\$\s*\d|\d+[.,]\d{2}\b|\b\d+\s*reais\b)")
_LOCAL = re.compile(r"\b(reuniao|consulta|encontro|almoco|jantar|visita|entrevista|aula|exame|festa|evento)\b")


def _fold(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _dt_de(v) -> Optional[_dt.datetime]:
    if not v:
        return None
    try:
        return _dt.datetime.fromisoformat(str(v)[:19])
    except ValueError:
        try:
            return _dt.datetime.fromisoformat(str(v)[:10])
        except ValueError:
            return None


def data_ref(card: Dict) -> Optional[_dt.datetime]:
    return _dt_de(card.get("due")) or _dt_de(card.get("start"))


def origem(card: Dict) -> str:
    m = card.get("meta") or {}
    o = m.get("prioridade_origem")
    if o in ("manual", "auto"):
        return o
    return "manual" if int(card.get("priority") or 0) > 0 else "auto"   # antigos com prioridade: da pessoa


def _ativo(c: Dict) -> bool:
    return not c.get("done") and c.get("column") != "feito"


def _importante(c: Dict) -> bool:
    return int(c.get("priority") or 0) >= 2


def ordem_do_dia(cards: Iterable[Dict], agora=None) -> List[Dict]:
    """Ordem sugerida para o dia: mais prioritário primeiro, depois o horário mais cedo."""
    def k(c):
        d = data_ref(c)
        return (-int(c.get("priority") or 0), d.strftime("%H:%M") if d and (d.hour or d.minute) else "99", c.get("created") or "")
    return sorted([c for c in cards if _ativo(c)], key=k)


def calcular(card: Dict, agora=None, cards_do_dia: Iterable[Dict] = ()) -> Dict:
    agora = agora or _dt.datetime.now()
    if isinstance(agora, _dt.date) and not isinstance(agora, _dt.datetime):
        agora = _dt.datetime.combine(agora, _dt.time(0))
    base = int((card.get("meta") or {}).get("prioridade_base") or 0)
    d = data_ref(card)
    if not d or not _ativo(card):
        return {"nivel": max(0, min(3, base)), "motivo": "", "conflito": False, "ordem": []}
    dias = (d.date() - agora.date()).days
    verbo = "vence" if card.get("due") else "começa"
    if dias < 0:
        nivel, porque = 3, "atrasada" if card.get("due") else "já passou"
    elif dias == 0:
        nivel, porque = 3, f"{verbo} hoje"
    elif dias == 1:
        nivel, porque = 2, f"{verbo} amanhã"
    elif dias <= 3:
        nivel, porque = 1, f"{verbo} em {dias} dias"
    elif dias <= 7:
        nivel, porque = 1 if base else 0, f"{verbo} nesta semana" if base else ""
    else:
        nivel, porque = 0, ""
    outros = [c for c in cards_do_dia if c.get("id") != card.get("id") and _ativo(c) and _importante(c)
              and (data_ref(c) or d).date() == d.date()]
    conflito = len(outros) >= 2 or (len(outros) >= 1 and dias <= 1 and nivel >= 2)
    partes = [porque] if porque else []
    if outros:
        n = len(outros) + 1
        partes.append(f"{n} tarefas importantes no dia")
        if len(outros) >= 2 and 0 <= dias <= 3 and nivel < 3:
            nivel += 1
    nivel = max(nivel, min(3, base))
    ordem = []
    if conflito:
        ordem = [c.get("id") for c in ordem_do_dia(list(outros) + [dict(card, priority=nivel)], agora)]
    motivo = ("sobe: " + " · ".join(partes)) if partes and nivel > 0 else ""
    return {"nivel": max(0, min(3, nivel)), "motivo": motivo, "conflito": bool(conflito), "ordem": ordem}


def _dia(c) -> Optional[str]:
    d = data_ref(c)
    return d.date().isoformat() if d else None


def por_dia(cards: Iterable[Dict]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for c in cards:
        k = _dia(c)
        if k and _ativo(c):
            out.setdefault(k, []).append(c)
    return out


def anotar(cards: List[Dict], agora=None) -> List[Dict]:
    """Acrescenta `prioridade_auto` (calculada) a cada card, sem gravar nada."""
    dias = por_dia(cards)
    for c in cards:
        r = calcular(c, agora, dias.get(_dia(c) or "", []))
        r["origem"] = origem(c)
        c["prioridade_auto"] = r
    return cards


def recalcular(store, agora=None) -> int:
    """Grava a prioridade calculada dos cards de origem auto (nunca toca nos de origem manual)."""
    try:
        cards = store.list(done=False)
    except TypeError:
        cards = [c for c in store.all() if not c.get("done")]
    dias = por_dia(cards)
    n = 0
    for c in cards:
        if origem(c) != "auto" or not data_ref(c):
            continue
        r = calcular(c, agora, dias.get(_dia(c) or "", []))
        m = c.get("meta") or {}
        if r["nivel"] != int(c.get("priority") or 0) or r["motivo"] != m.get("prioridade_motivo"):
            store.set_auto(c["id"], r["nivel"], {"prioridade_origem": "auto", "prioridade_motivo": r["motivo"]})
            n += 1
    return n


# ------------------------------------------------------------------ informações faltantes --
def origem_card(card: Dict) -> str:
    m = card.get("meta") or {}
    if m.get("origem"):
        return str(m["origem"])
    integ = (card.get("source") or {}).get("integration") or ""
    if integ == "secretario":
        return "notificacao"
    if integ in ("automacao", "automacoes", "tree", "arvore"):
        return "automacao"
    return integ or "app"


def faltantes(card: Dict, nao_perguntar: Iterable[str] = (), limite: int = 2) -> List[Dict]:
    """O que falta num card criado a partir de notificação/automação/comando. `nao_perguntar` (global):
    "campo" ou "origem:campo"; o card guarda os recusados em meta.nao_perguntar."""
    org = origem_card(card)
    if org not in ORIGENS_PERGUNTA or not _ativo(card):
        return []
    m = card.get("meta") or {}
    resp = m.get("respostas") or {}
    bloq = set(m.get("nao_perguntar") or []) | {x.split(":", 1)[1] for x in nao_perguntar
                                                if ":" in x and x.split(":", 1)[0] in (org, "*")} | \
        {x for x in nao_perguntar if ":" not in x}
    txt = _fold(" ".join([card.get("title") or "", card.get("body") or ""]))
    kind = card.get("kind") or "tarefa"
    st = _dt_de(card.get("start"))
    out = []

    def add(campo):
        if campo not in bloq and campo not in resp:
            p, t = CAMPOS[campo]
            out.append({"campo": campo, "pergunta": p, "tipo": t, "origem": org})

    if kind in ("tarefa", "lembrete") and not card.get("due") and not card.get("start"):
        add("prazo")
    if kind == "evento" and (not st or (not card.get("all_day") and st.hour == 0 and st.minute == 0)):
        add("horario")
    if kind == "evento" and _LOCAL.search(txt) and not re.search(r"\b(local|onde|endereco|sala|rua|av\.?)\b", txt):
        add("local")
    if _PAG.search(txt) and not _DINHEIRO.search(txt):
        add("valor")
    if kind == "tarefa" and org in ("notificacao", "automacao") and not card.get("people"):
        add("responsavel")
    return out[:limite]


_ROTULO = {"local": "Local", "valor": "Valor"}


def responder(card: Dict, campo: str, valor) -> Dict:
    """Patch para gravar a resposta da pessoa no card."""
    if campo not in CAMPOS:
        raise ValueError(f"campo: use {', '.join(CAMPOS)}")
    v = " ".join(str(valor or "").split())[:200]
    if not v:
        raise ValueError("escreva a resposta")
    meta = dict(card.get("meta") or {})
    meta["respostas"] = dict(meta.get("respostas") or {}, **{campo: v})
    patch: Dict = {"meta": meta}
    if campo == "prazo":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?", v):
            raise ValueError("prazo: use AAAA-MM-DD")
        patch["due"] = v if "T" in v else v + "T00:00:00"
    elif campo == "horario":
        if not re.fullmatch(r"\d{1,2}:\d{2}", v):
            raise ValueError("horário: use HH:MM")
        d = (data_ref(card) or _dt.datetime.now()).date().isoformat()
        patch["start"] = f"{d}T{int(v.split(':')[0]):02d}:{v.split(':')[1]}:00"
        patch["all_day"] = False
    elif campo == "responsavel":
        patch["people"] = list(card.get("people") or []) + [{"nome": v, "tipo": "pessoa"}]
    else:
        body = card.get("body") or ""
        patch["body"] = (body + ("\n" if body else "") + f"{_ROTULO[campo]}: {v}")
    return patch


def recusar(card: Dict, campo: str) -> Dict:
    meta = dict(card.get("meta") or {})
    meta["nao_perguntar"] = sorted(set(meta.get("nao_perguntar") or []) | {campo})
    return {"meta": meta}
