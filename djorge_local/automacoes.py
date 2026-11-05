"""Automações "Se acontecer X, faça Y" — guardadas em HOME/automacoes.json e compiladas para árvores de tree.py.

Automação: {id, nome, ativa, quando: {fontes[], apps[], remetente, palavras[], assunto, categoria, prioridade_min,
vip}, entao: [{acao, params: {titulo, campos[], coluna, prioridade, etiqueta, app, para, texto, url}}], criada,
contagem}

Compilação (o que roda de verdade é uma árvore comum):
  filtro (if: fontes/apps/remetente/palavras/categoria/prioridade/vip) --true--> assunto (check + keywords:
  palavra-chave primeiro, modelo se estiver carregado) --true--> ação 1 -> ação 2 ...
de_texto(frase): regras em pt-BR entendem a frase; com o modelo carregado, as marcações do modelo (rules.py)
completam o que as regras não acharam. Nada é ativado sem a pessoa ver o resumo (a rota devolve a automação).
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .tree import NOVAS_ACOES, PRIORIDADES, fold, validate

CAMPOS = ("valor", "prazo", "remetente", "empresa", "data", "hora", "link", "codigo", "pessoa", "classe", "resumo")
CATEGORIAS = ("pessoal", "trabalho", "financeiro", "compras e entregas", "agenda", "promoções", "notificações",
              "segurança")
ACAO_PT = {"criar_card": "criar um card", "tarefa": "criar uma tarefa", "alerta": "criar um card de alerta",
           "notificar": "avisar na tela", "etiquetar": "etiquetar", "arquivar": "arquivar",
           "abrir_app": "abrir o app", "responder_sugestao": "sugerir uma resposta",
           "destacar": "pôr em destaque na interface", "lembrete": "criar um lembrete", "nota": "criar uma nota",
           "atualizar_card": "atualizar o card", "mover_card": "mover o card", "concluir_card": "concluir o card",
           "prioridade": "mudar a prioridade", "abrir_site": "abrir o site", "ignorar": "ignorar (arquivar sem aviso)",
           "encadear": "rodar outra automação", "adicionar_lista": "adicionar à lista",
           "registrar": "registrar no histórico"}
CAMPO_PT = {"valor": "valor", "prazo": "prazo", "remetente": "quem mandou", "empresa": "empresa", "data": "data",
            "hora": "hora", "link": "link", "codigo": "código", "pessoa": "pessoa", "classe": "classificação",
            "resumo": "resumo"}
# Entradas (gatilhos). "mensagem" é o de sempre (notificações, e-mail, WhatsApp; o filtro de fontes decide).
GATILHOS = {"mensagem": "chegar uma mensagem", "card_criado": "um card for criado", "card_alterado": "um card mudar",
            "card_concluido": "um card for concluído", "card_atrasado": "um card atrasar",
            "prazo_chegando": "o prazo de um card estiver chegando", "horario": "for o horário",
            "alerta": "um alerta disparar", "nota_criada": "uma nota for criada", "nota_alterada": "uma nota mudar",
            "manual": "eu mandar rodar"}
DIAS_PT = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
TRAT_PT = {"extrair": "extrair", "classificar": "classificar", "resumir": "resumir", "formatar": "formatar",
           "agrupar": "agrupar"}
FORMATOS = ("maiusculas", "minusculas", "titulo", "data", "moeda")
# Condições de um grupo E/OU: tipo -> (como guardar o valor, frase)
ITENS = {"contem": "lista", "nao_contem": "lista", "remetente": "texto", "app": "lista", "fonte": "lista",
         "categoria": "texto", "prioridade_min": "texto", "valor_acima": "numero", "valor_abaixo": "numero",
         "valor_entre": "faixa", "regex": "texto", "tem_anexo": "nada", "tem_link": "nada", "tem_codigo": "nada",
         "horario": "horas", "dias": "dias", "vip": "nada", "etiqueta": "lista", "coluna": "lista"}
STOP = {"de", "da", "do", "das", "dos", "a", "o", "as", "os", "um", "uma", "uns", "umas", "e", "ou", "sobre", "com",
        "para", "pra", "que", "em", "no", "na", "nos", "nas", "mensagem", "mensagens", "email", "e-mail", "emails",
        "e-mails", "assunto", "algum", "alguma", "qualquer", "coisa", "falando", "fala", "falam", "tipo", "meu",
        "minha", "meus", "minhas", "novo", "nova", "novos", "novas"}
PARAMS = ("titulo", "campos", "coluna", "prioridade", "etiqueta", "app", "para", "texto", "url", "mensagem", "tipo",
          "web", "assunto", "cor", "icone", "fixar", "ate", "tom", "abrir", "lista", "automacao", "concluir", "card_id")


def stems(assunto: str) -> List[str]:
    """Raízes das palavras do assunto ("orçamentos" -> "orcament") para casar sem o modelo."""
    out = []
    for w in re.findall(r"[\wÀ-ÿ-]+", fold(assunto)):
        if w in STOP or len(w) < 3:
            continue
        w = re.sub(r"(oes|aes|ns|s)$", "", w) if len(w) > 4 else w
        out.append(w[:8])
    return list(dict.fromkeys(out))


def _id() -> str:
    return uuid.uuid4().hex[:10]


def _lst(v) -> List[str]:
    if isinstance(v, str) and "," in v:
        v = v.split(",")
    return [str(x).strip()[:80] for x in (v if isinstance(v, list) else [v] if v not in (None, "", False) else [])
            if str(x).strip()][:20]


def _numero(v):
    if v in (None, "", False):
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    from .tree import _num
    n = _num(str(v))
    if n is None:
        raise ValueError(f"valor inválido: {v}")
    return n


def _hm(v) -> str:
    s = str(v or "").strip().lower().replace("h", ":")
    if not s:
        return ""
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?:?$", s)
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ValueError(f"horário inválido: {v} (use 09:00)")
    return f"{int(m.group(1)):02d}:{int(m.group(2) or 0):02d}"


def _dias(v) -> List[int]:
    if v in ("uteis", "úteis", "dias uteis", "dias úteis"):
        return [0, 1, 2, 3, 4]
    if v in ("fim de semana", "fds"):
        return [5, 6]
    out = []
    for d in (v if isinstance(v, list) else [v] if v not in (None, "") else []):
        if isinstance(d, str) and not d.strip().isdigit():
            k = fold(d.strip())[:3]
            idx = [fold(x) for x in DIAS_PT].index(k) if k in [fold(x) for x in DIAS_PT] else None
            if idx is None:
                raise ValueError(f"dia da semana inválido: {d}")
            d = idx
        d = int(d)
        if not 0 <= d <= 6:
            raise ValueError("dias da semana: 0 (segunda) a 6 (domingo)")
        out.append(d)
    return sorted(set(out))


def _regex(v) -> str:
    from .tree import TreeError, regex_segura
    if not v:
        return ""
    try:
        return regex_segura(str(v))
    except TreeError as e:
        raise ValueError(str(e))


def _item(it) -> Dict:
    it = dict(it or {})
    tp = str(it.get("tipo") or "")
    if tp not in ITENS:
        raise ValueError(f"condição desconhecida: {tp} (use {', '.join(ITENS)})")
    v, como = it.get("valor"), ITENS[tp]
    if como == "lista":
        v = _lst(v)
        if not v:
            raise ValueError(f"condição “{tp}” sem valor")
    elif como == "texto":
        v = _regex(v) if tp == "regex" else str(v or "").strip()[:120]
        if not v:
            raise ValueError(f"condição “{tp}” sem valor")
        if tp == "categoria" and v not in CATEGORIAS:
            raise ValueError(f"categoria desconhecida: {v}")
        if tp == "prioridade_min" and v not in PRIORIDADES:
            raise ValueError("prioridade_min: baixa, normal, alta ou urgente")
    elif como == "numero":
        v = _numero(v)
        if v == "":
            raise ValueError(f"condição “{tp}” sem valor")
    elif como == "faixa":
        xs = v if isinstance(v, list) else str(v or "").replace(" e ", ",").split(",")
        xs = [_numero(x) for x in xs if str(x).strip()][:2]
        if len(xs) != 2:
            raise ValueError("valor entre: diga os dois números")
        v = sorted(xs)
    elif como == "horas":
        xs = v if isinstance(v, list) else re.split(r"\s*(?:-|–|a|até|ate)\s*", str(v or ""))
        v = [_hm(x) for x in xs[:2]]
        if len(v) != 2 or not all(v):
            raise ValueError("horário: diga início e fim (09:00-18:00)")
    elif como == "dias":
        v = _dias(v)
        if not v:
            raise ValueError("diga os dias da semana")
    else:
        v = True
    return {"tipo": tp, "valor": v, "negar": bool(it.get("negar"))}


def _gatilho(g) -> Dict:
    g = dict(g or {}) if isinstance(g, dict) else {"tipo": str(g or "mensagem")}
    tp = str(g.get("tipo") or "mensagem")
    if tp not in GATILHOS:
        raise ValueError(f"gatilho desconhecido: {tp} (use {', '.join(GATILHOS)})")
    out = {"tipo": tp}
    if tp == "prazo_chegando":
        out["horas"] = max(1, min(24 * 30, int(float(g.get("horas") or 24))))
    if tp == "horario":
        out["hora"] = _hm(g.get("hora") or "09:00")
        out["dias"] = _dias(g.get("dias")) if g.get("dias") not in (None, "", []) else []
    return out


def _tratamento(t) -> Dict:
    t = dict(t or {})
    tp = str(t.get("tipo") or "")
    if tp not in TRAT_PT:
        raise ValueError(f"tratamento desconhecido: {tp} (use {', '.join(TRAT_PT)})")
    out = {"tipo": tp}
    if tp == "extrair":
        out["campos"] = [c for c in _lst(t.get("campos")) if c in CAMPOS] or ["valor", "prazo", "remetente"]
    elif tp == "classificar":
        ops = _lst(t.get("opcoes"))
        if len(ops) < 2:
            raise ValueError("classificar: diga pelo menos duas opções")
        out["opcoes"] = ops[:10]
        out["campo"] = re.sub(r"\W", "", fold(t.get("campo") or "classe"))[:20] or "classe"
    elif tp == "formatar":
        como = str(t.get("como") or "maiusculas")
        if como not in FORMATOS:
            raise ValueError(f"formatar: use {', '.join(FORMATOS)}")
        out["campo"] = re.sub(r"\W", "", str(t.get("campo") or "title"))[:20] or "title"
        out["como"] = como
    elif tp == "agrupar":
        out["minutos"] = max(1, min(24 * 60, int(float(t.get("minutos") or 30))))
    return out


def normalizar(a: Dict, base: Optional[Dict] = None) -> Dict:
    a = copy.deepcopy(a or {})
    out = copy.deepcopy(base) if base else {"id": _id(), "criada": time.strftime("%Y-%m-%dT%H:%M:%S"), "contagem": 0}
    out["nome"] = " ".join(str(a.get("nome", out.get("nome", "")) or "").split())[:80] or "Automação"
    out["ativa"] = bool(a.get("ativa", out.get("ativa", True)))
    out["gatilho"] = _gatilho(a.get("gatilho", out.get("gatilho")))
    q = dict(out.get("quando") or {}, **(a.get("quando") or {}))
    lst = _lst
    quando = {"fontes": lst(q.get("fontes")), "apps": lst(q.get("apps")), "remetente": str(q.get("remetente") or "")[:120],
              "palavras": lst(q.get("palavras")), "assunto": str(q.get("assunto") or "")[:200],
              "categoria": str(q.get("categoria") or ""), "prioridade_min": str(q.get("prioridade_min") or ""),
              "vip": bool(q.get("vip")),
              # v2.5
              "nao_contem": lst(q.get("nao_contem")), "regex": _regex(q.get("regex")),
              "tem_anexo": bool(q.get("tem_anexo")), "tem_link": bool(q.get("tem_link")),
              "tem_codigo": bool(q.get("tem_codigo")), "valor_min": _numero(q.get("valor_min")),
              "valor_max": _numero(q.get("valor_max")), "hora_de": _hm(q.get("hora_de")),
              "hora_ate": _hm(q.get("hora_ate")), "dias": _dias(q.get("dias")),
              "etiquetas_card": lst(q.get("etiquetas_card")), "colunas": lst(q.get("colunas")),
              "modelo_acha": " ".join(str(q.get("modelo_acha") or "").split())[:200],
              "quantidade": {}, "modo": "ou" if q.get("modo") == "ou" else "e", "grupos": []}
    if quando["categoria"] and quando["categoria"] not in CATEGORIAS:
        raise ValueError(f"categoria desconhecida: {quando['categoria']}")
    if quando["prioridade_min"] and quando["prioridade_min"] not in PRIORIDADES:
        raise ValueError("prioridade_min: baixa, normal, alta ou urgente")
    if bool(quando["hora_de"]) != bool(quando["hora_ate"]):
        raise ValueError("horário: diga o início e o fim (das 09:00 às 18:00)")
    qt = q.get("quantidade") or {}
    if isinstance(qt, dict) and qt.get("n"):
        quando["quantidade"] = {"n": max(1, min(1000, int(float(qt["n"])))),
                                "minutos": max(1, min(24 * 60, int(float(qt.get("minutos") or 10))))}
    for g in (q.get("grupos") or [])[:6]:
        itens = [_item(x) for x in (g or {}).get("itens") or []][:12]
        if itens:
            quando["grupos"].append({"modo": "ou" if (g or {}).get("modo") == "ou" else "e", "itens": itens})
    out["quando"] = quando
    out["tratar"] = [_tratamento(t) for t in (a.get("tratar", out.get("tratar")) or [])[:6]]
    entao = a.get("entao", out.get("entao")) or []
    if not isinstance(entao, list) or not entao:
        raise ValueError("diga o que fazer (entao: [{acao, params}])")
    acoes = []
    for e in entao[:8]:
        ac = str((e or {}).get("acao") or "")
        if ac not in NOVAS_ACOES:
            raise ValueError(f"ação desconhecida: {ac} (use {', '.join(NOVAS_ACOES)})")
        p = dict((e or {}).get("params") or {})
        p["campos"] = [c for c in (p.get("campos") or []) if c in CAMPOS]
        acoes.append({"acao": ac, "params": {k: v for k, v in p.items() if k in PARAMS}})
        pp = acoes[-1]["params"]
        if ac == "destacar":
            from .uiprefs import ate_valido, estilo_destaque
            est = estilo_destaque(pp)
            acoes[-1]["params"] = dict(est, ate=ate_valido(pp.get("ate")))
        elif ac == "abrir_site" and not str(pp.get("url") or "").startswith(("https://", "http://")):
            raise ValueError("abrir site: use um endereço https://")
        elif ac == "encadear" and not pp.get("automacao"):
            raise ValueError("rodar outra automação: diga qual")
        elif ac == "notificar" and pp.get("tom") and pp["tom"] not in ("info", "aviso", "urgente", "sucesso"):
            raise ValueError("tom do aviso: info, aviso, urgente ou sucesso")
    out["entao"] = acoes
    return out


# ----------------------------------------------------------------------------------- condições <-> árvore --
_ITEM_COND = {"contem": "contains_any", "nao_contem": "not_contains", "app": "app_in", "fonte": "source_in",
              "etiqueta": "tag_in", "coluna": "column_in", "regex": "regex", "prioridade_min": "prioridade_min",
              "valor_acima": "valor_gt", "valor_abaixo": "valor_lt", "horario": "hora_entre", "dias": "dias_semana",
              "tem_anexo": "has_attachment", "tem_link": "has_link", "tem_codigo": "has_code", "vip": "vip"}


def _item_cond(it: Dict) -> Dict:
    tp, v = it["tipo"], it["valor"]
    if tp == "remetente":
        c = {"sender_has": [v]}
    elif tp == "categoria":
        c = {"categoria_in": [v]}
    elif tp == "valor_entre":
        c = {"valor_gt": v[0], "valor_lt": v[1]}
    else:
        c = {_ITEM_COND[tp]: v}
    return {"not": c} if it.get("negar") else c


def _cond_item(c: Dict) -> Optional[Dict]:
    neg = False
    if set(c) == {"not"} and isinstance(c["not"], dict):
        neg, c = True, c["not"]
    inv = {v: k for k, v in _ITEM_COND.items()}
    if set(c) == {"valor_gt", "valor_lt"}:
        return {"tipo": "valor_entre", "valor": [c["valor_gt"], c["valor_lt"]], "negar": neg}
    if len(c) != 1:
        return None
    k, v = next(iter(c.items()))
    if k == "sender_has" and isinstance(v, list) and len(v) == 1:
        return {"tipo": "remetente", "valor": v[0], "negar": neg}
    if k == "categoria_in" and isinstance(v, list) and len(v) == 1:
        return {"tipo": "categoria", "valor": v[0], "negar": neg}
    if k in inv:
        return {"tipo": inv[k], "valor": v, "negar": neg}
    return None


def _pecas(q: Dict) -> List[Dict]:
    """Condições simples do "Se…" (cada uma vira um pedaço do nó if)."""
    p = []
    if q.get("fontes"):
        p.append({"source_in": q["fontes"]})
    if q.get("apps"):
        p.append({"app_in": q["apps"]})
    if q.get("remetente"):
        p.append({"sender_has": [q["remetente"]]})
    if q.get("palavras"):
        p.append({"contains_any": q["palavras"]})
    if q.get("categoria"):
        p.append({"categoria_in": [q["categoria"]]})
    if q.get("prioridade_min"):
        p.append({"prioridade_min": q["prioridade_min"]})
    if q.get("vip"):
        p.append({"vip": True})
    if q.get("nao_contem"):
        p.append({"not_contains": q["nao_contem"]})
    if q.get("regex"):
        p.append({"regex": q["regex"]})
    for k, ck in (("tem_anexo", "has_attachment"), ("tem_link", "has_link"), ("tem_codigo", "has_code")):
        if q.get(k):
            p.append({ck: True})
    v = {}
    if q.get("valor_min") not in (None, ""):
        v["valor_gt"] = q["valor_min"]
    if q.get("valor_max") not in (None, ""):
        v["valor_lt"] = q["valor_max"]
    if v:
        p.append(v)
    if q.get("hora_de") and q.get("hora_ate"):
        p.append({"hora_entre": [q["hora_de"], q["hora_ate"]]})
    if q.get("dias"):
        p.append({"dias_semana": q["dias"]})
    if q.get("etiquetas_card"):
        p.append({"tag_in": q["etiquetas_card"]})
    if q.get("colunas"):
        p.append({"column_in": q["colunas"]})
    return p


def _cond_quando(q: Dict) -> Dict:
    pecas = _pecas(q)
    if q.get("modo") == "ou" and len(pecas) > 1:
        cond = {"any": pecas}
    else:
        cond = {}
        for x in pecas:
            cond.update(x)
    gs = [{("any" if g["modo"] == "ou" else "all"): [_item_cond(i) for i in g["itens"]]} for g in q.get("grupos") or []]
    if gs:
        cond["all"] = gs
    return cond


_PECA_CAMPO = {"source_in": "fontes", "app_in": "apps", "contains_any": "palavras", "not_contains": "nao_contem",
               "tag_in": "etiquetas_card", "column_in": "colunas", "dias_semana": "dias"}


def _pecas_quando(cond: Dict, quando: Dict) -> bool:
    """Volta de _pecas (um dict com as chaves simples); False se tiver algo que o "Se…" não representa."""
    for k, v in cond.items():
        if k in _PECA_CAMPO and isinstance(v, list):
            quando[_PECA_CAMPO[k]] = list(v)
        elif k == "sender_has" and isinstance(v, list) and len(v) <= 1:
            quando["remetente"] = str(v[0]) if v else ""
        elif k == "categoria_in" and isinstance(v, list) and len(v) <= 1:
            quando["categoria"] = str(v[0]) if v else ""
        elif k == "prioridade_min":
            quando["prioridade_min"] = str(v or "")
        elif k == "vip":
            quando["vip"] = bool(v)
        elif k == "regex":
            quando["regex"] = str(v or "")
        elif k in ("has_attachment", "has_link", "has_code"):
            quando[{"has_attachment": "tem_anexo", "has_link": "tem_link", "has_code": "tem_codigo"}[k]] = bool(v)
        elif k == "valor_gt":
            quando["valor_min"] = v
        elif k == "valor_lt":
            quando["valor_max"] = v
        elif k == "hora_entre" and isinstance(v, list) and len(v) == 2:
            quando["hora_de"], quando["hora_ate"] = v
        else:
            return False
    return True


def compilar(a: Dict) -> Dict:
    """Automação -> árvore de tree.py (validada).
    filtro (if) -> contagem (if count_min) -> assunto (check + keywords) -> "o modelo acha que" (check) ->
    tratamentos (action tratar) -> ações."""
    q, nodes = a["quando"], {}
    cond = _cond_quando(q)
    ids = [f"acao{i + 1}" for i in range(len(a["entao"]))]
    for i, (nid, e) in enumerate(zip(ids, a["entao"])):
        n = {"action": e["acao"], **copy.deepcopy(e["params"])}
        if i + 1 < len(ids):
            n["then"] = ids[i + 1]
        nodes[nid] = n
    first = ids[0]
    trat = a.get("tratar") or []
    for i in range(len(trat) - 1, -1, -1):
        nid = f"tratar{i + 1}"
        nodes[nid] = {"action": "tratar", **copy.deepcopy(trat[i]), "then": first}
        first = nid
    if q.get("modelo_acha"):
        frase = q["modelo_acha"].rstrip(".") + "."
        nodes["modelo"] = {"check": frase[:1].upper() + frase[1:], "keywords": [], "min_probability": 0.6,
                           "branches": {"true": first}}
        first = "modelo"
    if q.get("assunto"):
        nodes["assunto"] = {"check": f"Esta mensagem é sobre {q['assunto']}.", "keywords": stems(q["assunto"]),
                            "min_probability": 0.6, "branches": {"true": first}}
        first = "assunto"
    if (q.get("quantidade") or {}).get("n"):
        nodes["contagem"] = {"if": {"count_min": dict(q["quantidade"])}, "branches": {"true": first}}
        first = "contagem"
    if cond:
        nodes["filtro"] = {"if": cond, "branches": {"true": first}}
        first = "filtro"
    tree = {"name": f"auto-{a['id']}", "enabled": a.get("ativa", True), "start": first, "nodes": nodes,
            "origem": "automacao", "automacao": a["id"]}
    g = a.get("gatilho") or {"tipo": "mensagem"}
    if g.get("tipo", "mensagem") != "mensagem":
        tree["gatilho"] = copy.deepcopy(g)
    validate([tree])
    return tree


def _um(v):
    if isinstance(v, list):
        return v[0] if len(v) == 1 else None
    return v


def _so_true(n) -> Optional[str]:
    br = n.get("branches") or {}
    if set(map(str, br)) - {"True", "true"} or not br or "then" in n:
        return None
    return _um(next(iter(br.values())))


def de_arvore(tree: Dict) -> Optional[Dict]:
    """Volta de compilar(): árvore (editada no editor visual) -> {ativa, gatilho, quando, tratar, entao}, ou None
    quando a árvore tem um formato que uma automação não representa (aí ela é salva como árvore própria)."""
    nodes = tree.get("nodes") or {}
    nid, seen = tree.get("start"), set()
    quando = {"fontes": [], "apps": [], "remetente": "", "palavras": [], "assunto": "", "categoria": "",
              "prioridade_min": "", "vip": False, "modo": "e", "grupos": []}
    n = nodes.get(nid)
    if n is not None and "if" in n and not (n.get("if") or {}).get("count_min"):
        c = dict(n.get("if") or {})
        gs = c.pop("all", None)
        if gs is not None:
            if not isinstance(gs, list):
                return None
            for g in gs:
                if not isinstance(g, dict) or len(g) != 1 or next(iter(g)) not in ("all", "any"):
                    return None
                modo, its = next(iter(g.items()))
                itens = [_cond_item(x) if isinstance(x, dict) else None for x in its or []]
                if not itens or None in itens:
                    return None
                quando["grupos"].append({"modo": "ou" if modo == "any" else "e", "itens": itens})
        if "any" in c:
            pecas = c.pop("any")
            if c or not isinstance(pecas, list):
                return None
            quando["modo"] = "ou"
            for x in pecas:
                if not isinstance(x, dict) or not _pecas_quando(x, quando):
                    return None
        elif not _pecas_quando(c, quando):
            return None
        seen.add(nid)
        nid = _so_true(n)
        if nid is None:
            return None
        n = nodes.get(nid)
    if n is not None and "if" in n and (n.get("if") or {}).get("count_min"):
        if len(n["if"]) != 1:
            return None
        quando["quantidade"] = dict(n["if"]["count_min"])
        seen.add(nid)
        nid = _so_true(n)
        if nid is None:
            return None
        n = nodes.get(nid)
    for _ in range(2):
        if n is not None and "check" in n:
            txt = str(n.get("check") or "").strip()
            m = re.match(r"^esta mensagem é sobre (.+?)\.?$", txt, re.I)
            if n.get("keywords") or m:
                if quando["assunto"]:
                    return None
                quando["assunto"] = (m.group(1) if m else txt).strip()
            else:
                if quando.get("modelo_acha"):
                    return None
                quando["modelo_acha"] = txt.rstrip(".")
            seen.add(nid)
            nid = _so_true(n)
            if nid is None:
                return None
            n = nodes.get(nid)
    tratar, entao = [], []
    while n is not None:
        if nid in seen or "action" not in n or n.get("branches"):
            return None
        seen.add(nid)
        if n["action"] == "tratar":
            if entao:
                return None
            tratar.append({k: copy.deepcopy(v) for k, v in n.items() if k not in ("action", "then", "auto")})
        elif n["action"] in NOVAS_ACOES:
            entao.append({"acao": n["action"], "params": {k: copy.deepcopy(v) for k, v in n.items()
                                                          if k not in ("action", "then", "auto")}})
        else:
            return None
        nxt = n.get("then")
        if isinstance(nxt, list):
            if len(nxt) > 1:
                return None
            nxt = nxt[0] if nxt else None
        nid, n = nxt, (nodes.get(nxt) if nxt else None)
        if nxt and n is None:
            return None
    if not entao or seen != set(nodes):
        return None
    out = {"ativa": tree.get("enabled", True) is not False, "quando": quando, "tratar": tratar, "entao": entao,
           "gatilho": copy.deepcopy(tree.get("gatilho") or {"tipo": "mensagem"})}
    return out


def _junta(xs: List[str], conj: str = "e") -> str:
    xs = [x for x in xs if x]
    return (", ".join(xs[:-1]) + f" {conj} " + xs[-1]) if len(xs) > 1 else (xs[0] if xs else "")


def _dias_pt(ds: List[int]) -> str:
    if ds == [0, 1, 2, 3, 4]:
        return "em dias úteis"
    if ds == [5, 6]:
        return "no fim de semana"
    if not ds or len(ds) == 7:
        return "todo dia"
    return "às " + _junta([DIAS_PT[d] for d in ds])


def _brl(v) -> str:
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(v)


def _item_pt(it: Dict) -> str:
    tp, v = it["tipo"], it["valor"]
    lista = lambda xs: " ou ".join(f"“{x}”" for x in xs)
    t = {"contem": lambda: f"tiver {lista(v)}", "nao_contem": lambda: f"não tiver {lista(v)}",
         "remetente": lambda: f"for de {v}", "app": lambda: f"vier do app {' ou '.join(v)}",
         "fonte": lambda: f"vier de {' ou '.join(v)}", "categoria": lambda: f"for da categoria {v}",
         "prioridade_min": lambda: f"tiver prioridade {v} ou maior", "valor_acima": lambda: f"o valor passar de {_brl(v)}",
         "valor_abaixo": lambda: f"o valor for menor que {_brl(v)}",
         "valor_entre": lambda: f"o valor estiver entre {_brl(v[0])} e {_brl(v[1])}",
         "regex": lambda: f"casar com o padrão /{v}/", "tem_anexo": lambda: "tiver anexo",
         "tem_link": lambda: "tiver link", "tem_codigo": lambda: "tiver código",
         "horario": lambda: f"for entre {v[0]} e {v[1]}", "dias": lambda: _dias_pt(v),
         "vip": lambda: "for de alguém importante (VIP)", "etiqueta": lambda: f"o card tiver a etiqueta {lista(v)}",
         "coluna": lambda: f"o card estiver em {' ou '.join(v)}"}[tp]()
    return ("não " + t) if it.get("negar") and not t.startswith("não ") else t


def _gatilho_pt(g: Dict) -> str:
    tp = (g or {}).get("tipo", "mensagem")
    if tp == "horario":
        return f"Todo dia às {g.get('hora')}" if not g.get("dias") else f"{_dias_pt(g['dias']).capitalize()}, às {g.get('hora')}"
    if tp == "prazo_chegando":
        return f"Quando faltar {g.get('horas')} h para o prazo de um card"
    return "Quando " + GATILHOS[tp]


def resumo(a: Dict) -> str:
    q = a["quando"]
    g = a.get("gatilho") or {"tipo": "mensagem"}
    partes = []
    fontes = {"notifications": "uma notificação", "email": "um e-mail", "whatsapp": "uma mensagem do WhatsApp"}
    if g.get("tipo", "mensagem") == "mensagem":
        partes.append("Se chegar " + (" ou ".join(fontes.get(f, f) for f in q["fontes"]) if q["fontes"] else "uma mensagem"))
    else:
        partes.append(_gatilho_pt(g) + (" e" if any(_pecas(q)) or q.get("assunto") else ""))
    conds = []
    if q["apps"]:
        conds.append("do app " + " ou ".join(q["apps"]))
    if q["remetente"]:
        conds.append("de " + q["remetente"])
    if q["vip"]:
        conds.append("de alguém importante (VIP)")
    if q["assunto"]:
        conds.append("sobre " + q["assunto"])
    if q["palavras"]:
        conds.append("com " + " ou ".join(f"“{p}”" for p in q["palavras"]))
    if q["categoria"]:
        conds.append(f"da categoria {q['categoria']}")
    if q["prioridade_min"]:
        conds.append(f"com prioridade {q['prioridade_min']} ou maior")
    if q.get("nao_contem"):
        conds.append("sem " + " nem ".join(f"“{p}”" for p in q["nao_contem"]))
    if q.get("regex"):
        conds.append(f"que case com /{q['regex']}/")
    for k, t in (("tem_anexo", "com anexo"), ("tem_link", "com link"), ("tem_codigo", "com código")):
        if q.get(k):
            conds.append(t)
    if q.get("valor_min") not in (None, "") and q.get("valor_max") not in (None, ""):
        conds.append(f"com valor entre {_brl(q['valor_min'])} e {_brl(q['valor_max'])}")
    elif q.get("valor_min") not in (None, ""):
        conds.append(f"com valor acima de {_brl(q['valor_min'])}")
    elif q.get("valor_max") not in (None, ""):
        conds.append(f"com valor abaixo de {_brl(q['valor_max'])}")
    if q.get("hora_de"):
        conds.append(f"entre {q['hora_de']} e {q['hora_ate']}")
    if q.get("dias"):
        conds.append(_dias_pt(q["dias"]))
    if q.get("etiquetas_card"):
        conds.append("com a etiqueta " + " ou ".join(f"“{x}”" for x in q["etiquetas_card"]))
    if q.get("colunas"):
        conds.append("na coluna " + " ou ".join(q["colunas"]))
    if q.get("modo") == "ou" and len(conds) > 1:
        partes.append("(" + _junta(conds, "ou") + ")")
    else:
        partes += conds
    for gr in q.get("grupos") or []:
        partes.append("e se " + _junta([_item_pt(i) for i in gr["itens"]], "ou" if gr["modo"] == "ou" else "e"))
    if q.get("modelo_acha"):
        partes.append(f"e o modelo achar que “{q['modelo_acha']}”")
    if (q.get("quantidade") or {}).get("n"):
        partes.append(f"(mais de {q['quantidade']['n']} em {q['quantidade']['minutos']} min)")
    trat = []
    for t in a.get("tratar") or []:
        if t["tipo"] == "extrair":
            trat.append("extrair " + _junta([CAMPO_PT.get(c, c) for c in t["campos"]]))
        elif t["tipo"] == "classificar":
            trat.append("classificar em " + " / ".join(t["opcoes"]))
        elif t["tipo"] == "resumir":
            trat.append("resumir")
        elif t["tipo"] == "formatar":
            trat.append(f"formatar {t['campo']} ({t['como']})")
        elif t["tipo"] == "agrupar":
            trat.append(f"juntar tudo a cada {t['minutos']} min")
    faz = []
    for e in a["entao"]:
        t = ACAO_PT[e["acao"]]
        p = e["params"]
        if p.get("etiqueta") and e["acao"] == "etiquetar":
            t += f" como “{p['etiqueta']}”"
        if e["acao"] in ("mover_card", "atualizar_card") and p.get("coluna"):
            t += f" para {p['coluna']}"
        if e["acao"] == "prioridade" and p.get("prioridade") not in (None, ""):
            t += f" para {p['prioridade']}"
        if e["acao"] == "adicionar_lista" and p.get("lista"):
            t = f"adicionar à lista “{p['lista']}”"
        if e["acao"] == "encadear" and p.get("automacao"):
            t += f" (“{p['automacao']}”)"
        if e["acao"] == "notificar" and p.get("tom") in ("aviso", "urgente"):
            t += f" ({p['tom']})"
        if p.get("campos"):
            cs = [CAMPO_PT[c] for c in p["campos"]]
            t += " com " + (", ".join(cs[:-1]) + " e " + cs[-1] if len(cs) > 1 else cs[0])
        faz.append(t)
    meio = (", " + _junta(trat) if trat else "")
    return " ".join(partes) + meio + ", então " + _junta(faz) + "."


# --------------------------------------------------------------------------------------------- tratamentos --
def _frases(t: str) -> List[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", str(t or "")) if len(x.strip()) > 3]


def frase_central(texto: str) -> str:
    """Resumo extrativo: a frase com mais palavras frequentes do texto (sem inventar nada)."""
    fr = _frases(texto)
    if len(fr) <= 1:
        return (fr[0] if fr else str(texto or "").strip())[:280]
    pal = lambda s: [w for w in re.findall(r"[a-z0-9]{3,}", fold(s)) if w not in STOP]
    freq: Dict[str, int] = {}
    for w in pal(texto):
        freq[w] = freq.get(w, 0) + 1
    melhor = max(fr, key=lambda s: (sum(freq.get(w, 0) for w in set(pal(s))) / (1 + len(pal(s)) ** 0.5),
                                    -fr.index(s)))
    return melhor[:280]


_EXTRAI = {"valor": r"r\$\s*[\d.]+(?:,\d{1,2})?", "link": r"https?://\S+|\bwww\.\S+",
           "codigo": r"(?:codigo|code|senha|token|otp|pin)\D{0,25}(\d{4,8})\b",
           "prazo": r"\b(?:ate|vence\w*|prazo\D{0,10}|vencimento\D{0,10})\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
           "data": r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", "hora": r"\b(\d{1,2}[:h]\d{2})\b"}


def extrair(ev: Dict, campos: List[str]) -> Dict:
    """Campos que faltam no evento, com regras simples (o secretário já preenche os que reconheceu)."""
    txt = f"{ev.get('title') or ''}\n{ev.get('text') or ''}"
    ft = fold(txt)
    out = {}
    for c in campos:
        if ev.get(c) not in (None, ""):
            continue
        if c in _EXTRAI:
            m = re.search(_EXTRAI[c], ft if c not in ("link",) else txt, re.I)
            if m:
                v = m.group(1) if m.groups() else m.group(0)
                if c == "valor":
                    m2 = re.search(r"R\$\s*[\d.]+(?:,\d{1,2})?", txt, re.I)
                    v = m2.group(0) if m2 else v.upper()
                out[c] = v.rstrip(".,;)")
        elif c in ("remetente", "pessoa"):
            v = ev.get("sender") or ev.get("remetente")
            if not v:
                m = re.search(r"(?:^|\n|—|-)\s*([A-ZÁÉÍÓÚ][a-zà-ÿ]+(?:\s[A-ZÁÉÍÓÚ][a-zà-ÿ]+)?)\s*$", txt.strip())
                v = m.group(1) if m else ""
            if v:
                out[c] = v
        elif c == "empresa":
            m = re.search(r"\b([A-Z][\w&]+(?:\s[A-Z][\w&]+)?)\s+(?:Ltda|S\.?A\.?|ME|Eireli)\b", txt) or \
                re.search(r"\b(?:da|do|na|no)\s+(?:empresa|loja)\s+([A-Z][\w&]+)", txt)
            if m:
                out[c] = m.group(1)
        elif c == "resumo":
            out[c] = frase_central(ev.get("text") or ev.get("title") or "")
    return out


def classificar(runner, ev: Dict, opcoes: List[str]) -> Dict:
    """CHOICE entre as opções da pessoa: com o modelo carregado, ele escolhe; sem ele, palavras das opções."""
    txt = f"{ev.get('title') or ''}\n{ev.get('text') or ''}"
    z = runner._modelo() if runner is not None else None
    if z is not None:
        try:
            from .tree import build_query
            r = z.execute(build_query({"choice": opcoes}), sources={"text": txt},
                          lang=runner.cfg.get("language", "pt"))["result"]
            ans = r.get("answer")
            if isinstance(ans, int) and not isinstance(ans, bool) and 0 <= ans < len(opcoes):
                ans = opcoes[ans]
            if r.get("label"):
                ans = r["label"]
            if ans in opcoes:
                return {"answer": ans, "probability": r.get("probability"), "engine": "modelo"}
        except Exception:
            pass
    ft = fold(txt)
    pontos = [(sum(1 for s in stems(o) if s in ft), -i, o) for i, o in enumerate(opcoes)]
    melhor = max(pontos)
    if melhor[0] == 0:
        return {"answer": "", "probability": 0.0, "engine": "regra"}
    return {"answer": melhor[2], "probability": 0.7, "engine": "regra"}


def formatar(v, como: str) -> str:
    s = "" if v is None else str(v)
    if como == "maiusculas":
        return s.upper()
    if como == "minusculas":
        return s.lower()
    if como == "titulo":
        return " ".join(w if len(w) <= 2 and w.islower() else w[:1].upper() + w[1:].lower() for w in s.split())
    if como == "moeda":
        from .tree import _num
        n = _num(s)
        return _brl(n) if n is not None else s
    if como == "data":
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?", s)
        if m:
            return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" + (f" {m.group(4)}:{m.group(5)}" if m.group(4) else "")
        m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", s.strip())
        if m:
            ano = m.group(3) or time.strftime("%Y")
            ano = ("20" + ano) if len(ano) == 2 else ano
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{ano}"
    return s


def juntar(itens: List[Dict]) -> Dict:
    """Várias mensagens agrupadas -> um evento com o resumo de todas."""
    base = dict(itens[-1]) if itens else {}
    linhas = [f"• {(x.get('sender') or x.get('remetente') or x.get('app') or '').strip() + ': ' if (x.get('sender') or x.get('remetente') or x.get('app')) else ''}"
              f"{frase_central(x.get('text') or x.get('title') or '')[:160]}" for x in itens]
    base.update({"title": f"{len(itens)} mensage{'ns' if len(itens) != 1 else 'm'} agrupada{'s' if len(itens) != 1 else ''}",
                 "text": "\n".join(linhas), "agrupadas": len(itens), "resumo": "\n".join(linhas)})
    base.pop("notif_id", None)
    return base


def tratar(runner, n: Dict, ctx: Dict) -> Dict:
    """Um tratamento (nó action: tratar) — muda ctx["event"]."""
    ev, tp = ctx["event"], n.get("tipo")
    out = {"action": "tratar", "tipo": tp, "status": "ok"}
    if tp == "extrair":
        novos = extrair(ev, [c for c in n.get("campos") or [] if c in CAMPOS])
        ev.update(novos)
        out["campos"] = novos
    elif tp == "classificar":
        r = classificar(runner, ev, list(n.get("opcoes") or []))
        campo = n.get("campo") or "classe"
        ev[campo] = r["answer"]
        out.update(campo=campo, answer=r["answer"], probability=r.get("probability"), engine=r.get("engine"))
    elif tp == "resumir":
        ev["resumo"] = frase_central(ev.get("text") or ev.get("title") or "")
        out["resumo"] = ev["resumo"]
    elif tp == "formatar":
        campo = n.get("campo") or "title"
        ev[campo] = formatar(ev.get(campo), n.get("como") or "maiusculas")
        out.update(campo=campo, valor=ev[campo])
    elif tp == "agrupar":
        if ctx.get("mode") == "testar":
            out["status"] = f"juntaria com as próximas por {n.get('minutos')} min (no teste segue direto)"
            return out
        k = (ctx.get("tree"), ctx.get("nid"))
        g = runner.grupos.get(k)
        agora = runner.relogio()
        if g is None:
            runner.grupos[k] = {"inicio": agora, "itens": [dict(ev)]}
            return dict(out, status="agrupado", parar=True, itens=1)
        g["itens"].append(dict(ev))
        if agora - g["inicio"] < float(n.get("minutos") or 30) * 60:
            return dict(out, status="agrupado", parar=True, itens=len(g["itens"]))
        runner.grupos.pop(k, None)
        j = juntar(g["itens"])
        ev.clear(); ev.update(j)
        out.update(status="juntou", itens=len(g["itens"]))
    return out

# ------------------------------------------------------------------------------------------------ receitas --
def _r(rid, nome, desc, quando, entao):
    return {"id": rid, "nome": nome, "descricao": desc, "ativa": True, "quando": quando, "entao": entao}


MODELOS = [
    _r("boletos", "Boletos e contas", "Conta, boleto ou fatura vira tarefa para pagar, com valor e vencimento.",
       {"palavras": ["boleto", "fatura", "vencimento", "conta de", "pagamento pendente", "pix"]},
       [{"acao": "tarefa", "params": {"titulo": "Pagar {empresa}", "campos": ["valor", "prazo", "codigo", "empresa"],
                                       "coluna": "hoje", "prioridade": 2, "etiqueta": "contas"}},
        {"acao": "etiquetar", "params": {"etiqueta": "financeiro"}}]),
    _r("orcamentos", "Orçamentos", "Mensagem sobre orçamento vira card de alerta com valor, prazo e quem mandou.",
       {"assunto": "orçamentos"},
       [{"acao": "alerta", "params": {"titulo": "Alerta de orçamento — {remetente}",
                                       "campos": ["valor", "prazo", "remetente", "empresa"], "etiqueta": "orçamento"}}]),
    _r("entregas", "Entregas", "Pedido enviado ou a caminho vira card de entrega com o link de rastreio.",
       {"palavras": ["pedido", "entrega", "rastreio", "rastreamento", "enviado", "saiu para entrega", "a caminho"]},
       [{"acao": "criar_card", "params": {"titulo": "Entrega — {empresa}", "campos": ["empresa", "prazo", "link"],
                                           "coluna": "aguardando", "etiqueta": "entregas"}},
        {"acao": "etiquetar", "params": {"etiqueta": "compras e entregas"}}]),
    _r("reunioes", "Reuniões e convites", "Convite ou reunião com data vira evento na agenda com o link.",
       {"palavras": ["reunião", "convite", "call", "meet", "zoom", "teams", "consulta"]},
       [{"acao": "criar_card", "params": {"titulo": "{title}", "tipo": "evento", "campos": ["data", "hora", "link",
                                                                                           "remetente"],
                                           "etiqueta": "reunião"}}]),
    _r("codigos", "Códigos de verificação", "Código de verificação aparece num aviso rápido (não vira card).",
       {"palavras": ["código de verificação", "código de acesso", "código de segurança", "seu código",
                    "verification code", "otp"]},
       [{"acao": "notificar", "params": {"titulo": "Código {codigo}", "mensagem": "{app}: {title}"}},
        {"acao": "etiquetar", "params": {"etiqueta": "códigos"}}]),
    _r("vip", "Chefe e pessoas importantes", "Mensagem de alguém da lista VIP vira alerta urgente.",
       {"vip": True},
       [{"acao": "alerta", "params": {"titulo": "{remetente} mandou mensagem", "campos": ["prazo", "link"],
                                       "prioridade": 3, "etiqueta": "vip"}},
        {"acao": "notificar", "params": {"titulo": "{remetente}", "mensagem": "{text:140}"}}]),
    _r("promocoes", "Promoções → arquivar", "Propaganda, cupom e newsletter vão direto para o arquivo.",
       {"categoria": "promoções"},
       [{"acao": "arquivar", "params": {}}]),
]


# ------------------------------------------------------------------------------------------------ de_texto --
ACAO_RX = [  # (regex sem acento, ação) — a ordem importa
    (r"\b(card|cartao)\s+de\s+alerta|\balerta\b|\balert[ae]\b", "alerta"),
    (r"\btarefa\b", "tarefa"),
    (r"\b(crie|cria|criar|faca|faz|fazer|coloque|bote|adicione|adiciona)\b.*\b(card|cartao|nota|evento|lembrete)\b|"
     r"\b(card|cartao)\b", "criar_card"),
    (r"\b(avise|avisa|avisar|notifique|notifica|notificar|me mostre|mostre um aviso|me lembre)\b", "notificar"),
    (r"\b(arquive|arquiva|arquivar|ignore|ignora|esconda)\b", "arquivar"),
    (r"\b(etiquete|etiqueta|etiquetar|marque como|marca como|classifique como)\b", "etiquetar"),
    (r"\b(abra|abre|abrir)\b.*\b(whatsapp|navegador|e-?mail|site)\b", "abrir_app"),
    (r"\b(sugira|sugere|sugerir|prepare|prepara|rascunh\w*)\b.*\bresposta\b|\bresponda\b|\bresponder\b",
     "responder_sugestao"),
]
CAMPO_RX = {"valor": r"\b(valor|valores|preco|quanto custa|total)\b", "prazo": r"\b(prazo|vencimento|validade|ate quando)\b",
            "remetente": r"\b(quem mandou|quem enviou|remetente|de quem|quem pediu|contato)\b",
            "empresa": r"\b(empresa|loja|fornecedor|estabelecimento)\b", "data": r"\b(data|dia)\b",
            "hora": r"\b(hora|horario)\b", "link": r"\b(link|endereco|url)\b", "codigo": r"\b(codigo|token)\b"}
CAT_RX = [(r"\b(boleto|fatura|conta de|contas a pagar|cobranca|pagamento)", "financeiro"),
          (r"\b(promoc|propaganda|cupom|newsletter|oferta)", "promoções"),
          (r"\b(entrega|rastreio|encomenda)", "compras e entregas"),
          (r"\b(reuniao|reunioes|convite|consulta)", "agenda"),
          (r"\b(banco|bancos|cartao de credito)\b", "financeiro")]
APPS = ["WhatsApp", "Telegram", "Outlook", "Gmail", "Teams", "Slack", "Instagram", "Discord", "Signal"]


_GATILHO = re.compile(r"\b(sempre que|toda vez que|todas as vezes que|cada vez que|assim que|quando|caso)\s+", re.I)
_GATILHO_SE = re.compile(r"(?:^|[,;:.]\s*|\be\s+)(se)\s+", re.I)
_FIM_COND = re.compile(r"\s*(?:,|;|\s(?:entao|então)\s|\s+e\s+(?=(?:me\s+|a\s+|o\s+)?(?:coloque|coloca|crie|cria|deixe|deixa|"
                       r"destaque|destaca|avise|avisa|mostre|mostra|ponha|poe|põe|bote|marque|marca|arquive|arquiva|"
                       r"etiquete|etiqueta|abra|abre|fixe|fixa|faca|faça|faz)\b))", re.I)
_NOME = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ]+){0,2}"
_FALA = re.compile(r"\b(?:alerta|aviso|notifica[cç][aã]o|toast|lembrete)\s+(?:dizendo|escrito|que diga|que diz|com a frase|"
                   r"com o texto|com a mensagem|falando)\s*:?\s*[\"“'‘]([^\"”'’]{1,120})[\"”'’]|"
                   r"\b(?:alerta|aviso)\s+(?:dizendo|escrito|que diga|que diz|falando)\s*:?\s*([^,;.]{2,80}?)(?=\s+(?:sempre que|toda vez que|"
                   r"quando|caso|se)\b|[,;.]|$)", re.I)
DESTACAR_RX = r"\b(em destaque|destaque|destaca|destacar|destaques|realce|realca|ressalte|ressalta|fixe no topo|fixa no topo)\b"
ICONE_RX = [(r"\b(amor|coracao|namorad\w*|esposa|marido|mae|pai)\b", "coracao"), (r"\b(urgente|alerta)\b", "alerta"),
            (r"\b(fixa|fixe|topo)\b", "pino")]


def _cond_e_faz(texto: str):
    """Separa o gatilho ("sempre que a Tamy mandar mensagem") do que fazer (o resto da frase, antes e depois)."""
    m = _GATILHO.search(texto) or _GATILHO_SE.search(texto)
    if not m:
        return None
    ini = m.start(1)
    resto = texto[m.end():]
    f = _FIM_COND.search(resto)
    cond = resto[:f.start()] if f else resto
    depois = resto[f.end():] if f else ""
    faz = (texto[:ini].strip(" ,;.") + " " + depois).strip(" ,;.")
    return cond.strip(" ,;."), faz or cond


def _remetente(cond: str):
    """("a", "Tamy Pqna") de "a Tamy Pqna enviar mensagem"; ("", "fulano@x.com"); None."""
    m = re.search(r"[\w.+-]+@[\w.-]+", cond)
    if m:
        return "", m.group(0)
    for m in re.finditer(r"\b(d[oa]|de|a|o|pel[oa])\s+(" + _NOME + r")", cond):
        nome = m.group(2)
        if nome.split()[0] in APPS:
            continue
        art = {"da": "a", "do": "o", "a": "a", "o": "o", "pela": "a", "pelo": "o"}.get(m.group(1).lower(), "")
        return art, " ".join(w if w.islower() and len(w) <= 3 else w[:1].upper() + w[1:] for w in nome.split())
    m = re.search(r"^\s*(" + _NOME + r")\s+(?:me\s+)?(?:mand|envi|escrev|lig|respond|chamar)", cond)
    if m and m.group(1).split()[0] not in APPS:
        return "", m.group(1)
    return None


def resumo_curto(a: Dict, artigo: str = "") -> str:
    """"Se a Tamy Pqna mandar mensagem → aviso “Seu amor mandou mensagem” e a mensagem dela fica em destaque"."""
    q = a["quando"]
    coisa = "e-mail" if "email" in q["fontes"] else "notificação" if "notifications" in q["fontes"] else \
        ("mensagem no " + q["apps"][0]) if q["apps"] else "mensagem"
    fem = coisa != "e-mail"
    if q["remetente"]:
        quem = (artigo + " " if artigo else "") + q["remetente"]
        gat = f"Se {quem} mandar {coisa}"
    else:
        gat = f"Se chegar {coisa}"
        if q["vip"]:
            gat += " de alguém importante"
        if q["categoria"]:
            gat += f" da categoria {q['categoria']}"
    if q["assunto"]:
        gat += f" sobre {q['assunto']}"
    if q["palavras"]:
        gat += " com " + " ou ".join(f"“{p}”" for p in q["palavras"])
    dono = {"a": "dela", "o": "dele"}.get(artigo, "")
    faz = []
    for e in a["entao"]:
        p, ac = e["params"], e["acao"]
        tit = str(p.get("titulo") or "")
        if ac == "notificar":
            faz.append(f"aviso “{tit}”" if tit and "{" not in tit else "aviso na tela")
        elif ac == "destacar":
            faz.append(f"{'a' if fem else 'o'} {coisa.split(' no ')[0]}{' ' + dono if dono else ''} fica em destaque"
                       + (" no topo" if p.get("fixar") and not dono else ""))
        elif ac in ("alerta", "tarefa", "criar_card"):
            nome = {"alerta": "card de alerta", "tarefa": "tarefa", "criar_card": "card"}[ac]
            faz.append(f"{nome} “{tit}”" if tit and "{" not in tit else nome)
        elif ac == "etiquetar":
            faz.append(f"etiqueta “{p.get('etiqueta')}”")
        else:
            faz.append(ACAO_PT[ac])
    return gat + " → " + (", ".join(faz[:-1]) + " e " + faz[-1] if len(faz) > 1 else faz[0])


def de_texto(frase: str, marcar=None, vips=()) -> Dict:
    """Frase pt-BR -> automação (não salva). `marcar(text)` (opcional) devolve as marcações do modelo."""
    texto = " ".join(str(frase or "").split())[:600]
    if len(texto) < 8:
        raise ValueError("escreva a automação, ex.: “Se eu receber uma mensagem sobre orçamentos, crie um card de alerta”")
    fala = _FALA.search(texto)
    fala_txt = (fala.group(1) or fala.group(2)).strip() if fala else ""
    sem_fala = (texto[:fala.start()] + " alerta " + texto[fala.end():]) if fala else texto
    cf = _cond_e_faz(sem_fala)
    if cf:
        cond, faz = cf
    else:
        parts = re.split(r",\s*", sem_fala, maxsplit=1)
        cond, faz = (parts + [""])[:2] if len(parts) == 2 else (sem_fala, sem_fala)
    sc, sf = fold(cond), fold(faz)
    notas, quando = [], {"fontes": [], "apps": [], "palavras": []}
    if re.search(r"\be-?mails?\b", sc):
        quando["fontes"].append("email")
    if re.search(r"\bnotificac", sc):
        quando["fontes"].append("notifications")
    quando["apps"] = [a for a in APPS if re.search(rf"\b{a.casefold()}\b|\bzap\b" if a == "WhatsApp" else rf"\b{a.casefold()}\b", sc)]
    ass = re.search(r"\b(?:sobre|falando de|falando sobre|a respeito de|relacionad[ao]s? a|referente a)\s+(.+)$", cond, re.I)
    if ass:
        a = re.split(r"\s+(?:d[oa]s?|de)\s+(?=[A-ZÁÉÍÓÚ])|\s+(?:no|pelo|pela|do app)\s+", ass.group(1).strip(" .,;"))[0]
        quando["assunto"] = a.strip(" .,;")
    artigo = ""
    rem = _remetente(cond if not ass else cond[:ass.start()] + " " + cond[ass.end():])
    if rem:
        artigo, quando["remetente"] = rem
    if re.search(r"\b(meu chefe|minha chefe|vips?|pessoas? importantes?)\b", sc):
        quando["vip"] = True
    quando["palavras"] += re.findall(r"[\"“']([^\"”']{2,40})[\"”']", cond)
    for rx, cat in CAT_RX:
        if not quando.get("assunto") and not quando["palavras"] and re.search(rx, sc):
            quando["categoria"] = cat; break
    if re.search(r"\b(urgente|urgentes)\b", sc):
        quando["prioridade_min"] = "urgente"
    elif re.search(r"\b(importante|importantes)\b", sc) and not quando.get("vip"):
        quando["prioridade_min"] = "alta"
    if marcar is not None:                                  # o modelo completa o que as regras não acharam
        try:
            f = marcar(cond)
            if not quando.get("remetente") and (f.get("email") or f.get("pessoa")):
                quando["remetente"] = (f.get("email") or f.get("pessoa"))[0]
            if not quando["apps"] and f.get("app"):
                quando["apps"] = f["app"][:2]
            if not quando.get("assunto") and not quando["palavras"] and f.get("palavra"):
                quando["assunto"] = f["palavra"][0]
        except Exception:
            notas.append("O modelo não está carregado: entendi só pelas regras.")
    acoes = []
    if fala_txt:
        acoes.append("notificar")
    for rx, ac in ACAO_RX:
        if ac == "alerta" and fala_txt:
            continue
        if re.search(rx, sf) and ac not in acoes:
            if ac == "criar_card" and ("alerta" in acoes or "tarefa" in acoes):
                continue
            acoes.append(ac)
    if re.search(DESTACAR_RX, sf) and "destacar" not in acoes:
        acoes.append("destacar")
        if acoes == ["criar_card", "destacar"] and not re.search(r"\b(card|cartao|tarefa|evento|lembrete)\b", sf):
            acoes.remove("criar_card")
    if not acoes:
        acoes = ["criar_card"]
        notas.append("Não entendi o que fazer: deixei “criar um card”. Ajuste antes de ativar.")
    campos = [c for c, rx in CAMPO_RX.items() if re.search(rx, sf)]
    tema = quando.get("assunto") or (quando["palavras"][0] if quando["palavras"] else quando.get("categoria") or "")
    tm = re.search(r"\b(?:card|cartao|cartão|alerta|tarefa)\s+de\s+(?:alerta\s+de\s+)?([\wÀ-ÿ ]+?)(?:\s+com\b|[,.]|$)", faz, re.I)
    rotulo = (tm.group(1).strip() if tm and fold(tm.group(1).strip()) != "alerta" else tema).strip()
    entao = []
    for ac in acoes:
        p: Dict = {}
        if ac in ("alerta", "tarefa", "criar_card"):
            base = {"alerta": "Alerta de {r}", "tarefa": "{R}", "criar_card": "{R}"}[ac]
            if rotulo or ac == "alerta":
                r = rotulo or "mensagem"
                t = base.replace("{r}", r).replace("{R}", r[:1].upper() + r[1:])
                p["titulo"] = (t[:1].upper() + t[1:]) + (" — {remetente}" if ac != "tarefa" or "remetente" in campos else "")
            else:
                p["titulo"] = "{title}" + (" — {remetente}" if "remetente" in campos else "")
            p["campos"] = campos or (["remetente", "prazo"] if ac == "alerta" else [])
            if rotulo:
                p["etiqueta"] = fold(rotulo)[:30] if not re.search(r"[^\x00-\x7f]", rotulo) else rotulo.casefold()[:30]
            if re.search(r"\bevento\b|\bagenda\b", sf):
                p["tipo"] = "evento"
        elif ac == "etiquetar":
            et = re.search(r"\b(?:como|etiqueta)\s+[\"“']?([\wÀ-ÿ ]{2,30}?)[\"”']?(?:[,.]|$|\s+e\s)", faz, re.I)
            p["etiqueta"] = (et.group(1).strip() if et else rotulo or "importante").casefold()
        elif ac == "notificar":
            if fala_txt:
                p["titulo"] = fala_txt
                p["mensagem"] = ("{remetente}: " if not quando.get("remetente") else "") + "{text:140}"
            else:
                p["titulo"] = (rotulo[:1].upper() + rotulo[1:] if rotulo else "Mensagem") + " — {remetente}"
                p["mensagem"] = "{text:140}"
        elif ac == "destacar":
            from .uiprefs import ACCENTS
            cor = next((c for c in ACCENTS if re.search(rf"\b{c}\b", sf)), "rosa")
            ic = next((i for rx, i in ICONE_RX if re.search(rx, fold(texto))), "estrela")
            p = {"cor": cor, "icone": ic, "fixar": True}
        elif ac == "abrir_app":
            p["app"] = "email" if re.search(r"e-?mail", sf) else "navegador" if re.search(r"navegador|site", sf) else "whatsapp"
        entao.append({"acao": ac, "params": p})
    nome = (rotulo[:1].upper() + rotulo[1:]) if rotulo else (
        f"Mensagens de {quando['remetente']}" if quando.get("remetente") else "Automação")
    a = normalizar({"nome": nome, "quando": quando, "entao": entao})
    if not any(a["quando"][k] for k in ("fontes", "apps", "remetente", "palavras", "assunto", "categoria",
                                        "prioridade_min", "vip")):
        notas.append("Vale para tudo que chegar. Se não for isso, diga de quem, de qual app ou sobre o quê.")
    a["origem"] = texto
    return {"automacao": a, "resumo": resumo(a), "resumo_curto": resumo_curto(a, artigo), "notas": notas}


# ------------------------------------------------------------------------------------------------- store --
class Automacoes:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._itens: List[Dict] = []
        self._arvores: Dict[str, Dict] = {}
        if self.path.is_file():
            try:
                self._itens = json.loads(self.path.read_text(encoding="utf-8")).get("automacoes") or []
            except Exception:
                self._itens = []
        self._recompilar()

    def _recompilar(self):
        arv = {}
        for a in self._itens:
            try:
                arv[a["id"]] = compilar(a)
            except Exception:
                pass
        self._arvores = arv

    def _gravar(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"automacoes": self._itens}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        self._recompilar()

    def listar(self) -> List[Dict]:
        with self.lock:
            return [dict(copy.deepcopy(a), resumo=resumo(a)) for a in self._itens]

    def get(self, aid: str) -> Optional[Dict]:
        with self.lock:
            return copy.deepcopy(next((a for a in self._itens if a["id"] == aid), None))

    def salvar(self, dados: Dict) -> Dict:
        with self.lock:
            atual = next((a for a in self._itens if a["id"] == dados.get("id")), None) if dados.get("id") else None
            a = normalizar(dados, atual)
            if atual is None and dados.get("id"):
                a["id"] = str(dados["id"])[:40]
            if dados.get("origem"):
                a["origem"] = str(dados["origem"])[:600]
            compilar(a)
            if atual is not None:
                self._itens[self._itens.index(atual)] = a
            else:
                self._itens.append(a)
            self._gravar()
            return dict(copy.deepcopy(a), resumo=resumo(a))

    def apagar(self, aid: str) -> bool:
        with self.lock:
            n = len(self._itens)
            self._itens = [a for a in self._itens if a["id"] != aid]
            if len(self._itens) != n:
                self._gravar()
                return True
            return False

    def contar(self, aid: str):
        with self.lock:
            for a in self._itens:
                if a["id"] == aid:
                    a["contagem"] = int(a.get("contagem") or 0) + 1
                    a["ultima"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                self._gravar()
            except Exception:
                pass

    def ativas(self) -> List[tuple]:
        """[(automação, árvore)] das ativas."""
        with self.lock:
            return [(a, self._arvores[a["id"]]) for a in self._itens if a.get("ativa", True) and a["id"] in self._arvores]

    def arvore(self, aid: str) -> Optional[Dict]:
        return copy.deepcopy(self._arvores.get(aid))

    @staticmethod
    def modelos() -> List[Dict]:
        return [dict(copy.deepcopy(m), resumo=resumo(normalizar(m))) for m in MODELOS]
