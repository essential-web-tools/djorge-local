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

CAMPOS = ("valor", "prazo", "remetente", "empresa", "data", "hora", "link", "codigo")
CATEGORIAS = ("pessoal", "trabalho", "financeiro", "compras e entregas", "agenda", "promoções", "notificações",
              "segurança")
ACAO_PT = {"criar_card": "criar um card", "tarefa": "criar uma tarefa", "alerta": "criar um card de alerta",
           "notificar": "avisar na tela", "etiquetar": "etiquetar", "arquivar": "arquivar",
           "abrir_app": "abrir o app", "responder_sugestao": "sugerir uma resposta",
           "destacar": "pôr em destaque na interface"}
CAMPO_PT = {"valor": "valor", "prazo": "prazo", "remetente": "quem mandou", "empresa": "empresa", "data": "data",
            "hora": "hora", "link": "link", "codigo": "código"}
STOP = {"de", "da", "do", "das", "dos", "a", "o", "as", "os", "um", "uma", "uns", "umas", "e", "ou", "sobre", "com",
        "para", "pra", "que", "em", "no", "na", "nos", "nas", "mensagem", "mensagens", "email", "e-mail", "emails",
        "e-mails", "assunto", "algum", "alguma", "qualquer", "coisa", "falando", "fala", "falam", "tipo", "meu",
        "minha", "meus", "minhas", "novo", "nova", "novos", "novas"}


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


def normalizar(a: Dict, base: Optional[Dict] = None) -> Dict:
    a = copy.deepcopy(a or {})
    out = copy.deepcopy(base) if base else {"id": _id(), "criada": time.strftime("%Y-%m-%dT%H:%M:%S"), "contagem": 0}
    out["nome"] = " ".join(str(a.get("nome", out.get("nome", "")) or "").split())[:80] or "Automação"
    out["ativa"] = bool(a.get("ativa", out.get("ativa", True)))
    q = dict(out.get("quando") or {}, **(a.get("quando") or {}))
    lst = lambda v: [str(x).strip()[:80] for x in (v if isinstance(v, list) else [v] if v else []) if str(x).strip()]
    quando = {"fontes": lst(q.get("fontes")), "apps": lst(q.get("apps")), "remetente": str(q.get("remetente") or "")[:120],
              "palavras": lst(q.get("palavras")), "assunto": str(q.get("assunto") or "")[:200],
              "categoria": str(q.get("categoria") or ""), "prioridade_min": str(q.get("prioridade_min") or ""),
              "vip": bool(q.get("vip"))}
    if quando["categoria"] and quando["categoria"] not in CATEGORIAS:
        raise ValueError(f"categoria desconhecida: {quando['categoria']}")
    if quando["prioridade_min"] and quando["prioridade_min"] not in PRIORIDADES:
        raise ValueError("prioridade_min: baixa, normal, alta ou urgente")
    out["quando"] = quando
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
        acoes.append({"acao": ac, "params": {k: v for k, v in p.items() if k in (
            "titulo", "campos", "coluna", "prioridade", "etiqueta", "app", "para", "texto", "url", "mensagem", "tipo",
            "web", "assunto", "cor", "icone", "fixar", "ate")}})
        if ac == "destacar":
            from .uiprefs import ate_valido, estilo_destaque
            est = estilo_destaque(acoes[-1]["params"])
            acoes[-1]["params"] = dict(est, ate=ate_valido(acoes[-1]["params"].get("ate")))
    out["entao"] = acoes
    return out


def compilar(a: Dict) -> Dict:
    """Automação -> árvore de tree.py (validada)."""
    q, nodes = a["quando"], {}
    cond = {}
    if q.get("fontes"):
        cond["source_in"] = q["fontes"]
    if q.get("apps"):
        cond["app_in"] = q["apps"]
    if q.get("remetente"):
        cond["sender_has"] = [q["remetente"]]
    if q.get("palavras"):
        cond["contains_any"] = q["palavras"]
    if q.get("categoria"):
        cond["categoria_in"] = [q["categoria"]]
    if q.get("prioridade_min"):
        cond["prioridade_min"] = q["prioridade_min"]
    if q.get("vip"):
        cond["vip"] = True
    ids = [f"acao{i + 1}" for i in range(len(a["entao"]))]
    for i, (nid, e) in enumerate(zip(ids, a["entao"])):
        n = {"action": e["acao"], **copy.deepcopy(e["params"])}
        if i + 1 < len(ids):
            n["then"] = ids[i + 1]
        nodes[nid] = n
    first = ids[0]
    if q.get("assunto"):
        nodes["assunto"] = {"check": f"Esta mensagem é sobre {q['assunto']}.", "keywords": stems(q["assunto"]),
                            "min_probability": 0.6, "branches": {"true": first}}
        first = "assunto"
    if cond:
        nodes["filtro"] = {"if": cond, "branches": {"true": first}}
        first = "filtro"
    tree = {"name": f"auto-{a['id']}", "enabled": a.get("ativa", True), "start": first, "nodes": nodes,
            "origem": "automacao", "automacao": a["id"]}
    validate([tree])
    return tree


def _um(v):
    if isinstance(v, list):
        return v[0] if len(v) == 1 else None
    return v


_COND_QUANDO = {"source_in": "fontes", "app_in": "apps", "contains_any": "palavras"}


def de_arvore(tree: Dict) -> Optional[Dict]:
    """Volta de compilar(): árvore (editada no editor visual) -> {ativa, quando, entao}, ou None quando a árvore
    tem um formato que uma automação não representa (aí ela é salva como árvore própria)."""
    nodes = tree.get("nodes") or {}
    nid, seen = tree.get("start"), set()
    quando = {"fontes": [], "apps": [], "remetente": "", "palavras": [], "assunto": "", "categoria": "",
              "prioridade_min": "", "vip": False}
    n = nodes.get(nid)
    if n is not None and "if" in n:
        c = n.get("if") or {}
        br = n.get("branches") or {}
        if set(map(str, br)) - {"True", "true"} or not br or "then" in n:
            return None
        for k, v in c.items():
            if k in _COND_QUANDO and isinstance(v, list):
                quando[_COND_QUANDO[k]] = [str(x) for x in v]
            elif k == "sender_has" and isinstance(v, list) and len(v) <= 1:
                quando["remetente"] = str(v[0]) if v else ""
            elif k == "categoria_in" and isinstance(v, list) and len(v) <= 1:
                quando["categoria"] = str(v[0]) if v else ""
            elif k == "prioridade_min":
                quando["prioridade_min"] = str(v or "")
            elif k == "vip":
                quando["vip"] = bool(v)
            else:
                return None
        seen.add(nid)
        nid = _um(next(iter(br.values())))
        if nid is None:
            return None
        n = nodes.get(nid)
    if n is not None and "check" in n:
        br = n.get("branches") or {}
        if set(map(str, br)) - {"True", "true"} or not br or "then" in n:
            return None
        txt = str(n.get("check") or "").strip()
        m = re.match(r"^esta mensagem é sobre (.+?)\.?$", txt, re.I)
        quando["assunto"] = (m.group(1) if m else txt).strip()
        seen.add(nid)
        nid = _um(next(iter(br.values())))
        if nid is None:
            return None
        n = nodes.get(nid)
    entao = []
    while n is not None:
        if nid in seen or "action" not in n or n["action"] not in NOVAS_ACOES or n.get("branches"):
            return None
        seen.add(nid)
        entao.append({"acao": n["action"], "params": {k: copy.deepcopy(v) for k, v in n.items()
                                                      if k not in ("action", "then", "auto")}})
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
    return {"ativa": tree.get("enabled", True) is not False, "quando": quando, "entao": entao}


def resumo(a: Dict) -> str:
    q = a["quando"]
    partes = []
    fontes = {"notifications": "uma notificação", "email": "um e-mail", "whatsapp": "uma mensagem do WhatsApp"}
    partes.append("Se chegar " + (" ou ".join(fontes.get(f, f) for f in q["fontes"]) if q["fontes"] else "uma mensagem"))
    if q["apps"]:
        partes.append("do app " + " ou ".join(q["apps"]))
    if q["remetente"]:
        partes.append("de " + q["remetente"])
    if q["vip"]:
        partes.append("de alguém importante (VIP)")
    if q["assunto"]:
        partes.append("sobre " + q["assunto"])
    if q["palavras"]:
        partes.append("com " + " ou ".join(f"“{p}”" for p in q["palavras"]))
    if q["categoria"]:
        partes.append(f"da categoria {q['categoria']}")
    if q["prioridade_min"]:
        partes.append(f"com prioridade {q['prioridade_min']} ou maior")
    faz = []
    for e in a["entao"]:
        t = ACAO_PT[e["acao"]]
        p = e["params"]
        if p.get("etiqueta") and e["acao"] == "etiquetar":
            t += f" como “{p['etiqueta']}”"
        if p.get("campos"):
            cs = [CAMPO_PT[c] for c in p["campos"]]
            t += " com " + (", ".join(cs[:-1]) + " e " + cs[-1] if len(cs) > 1 else cs[0])
        faz.append(t)
    return " ".join(partes) + ", então " + (", ".join(faz[:-1]) + " e " + faz[-1] if len(faz) > 1 else faz[0]) + "."


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
