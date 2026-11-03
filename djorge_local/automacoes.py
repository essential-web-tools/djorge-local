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
           "abrir_app": "abrir o app", "responder_sugestao": "sugerir uma resposta"}
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
            "web", "assunto")}})
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
          (r"\b(reuniao|reunioes|convite|consulta)", "agenda")]
APPS = ["WhatsApp", "Telegram", "Outlook", "Gmail", "Teams", "Slack", "Instagram", "Discord", "Signal"]


def de_texto(frase: str, marcar=None, vips=()) -> Dict:
    """Frase pt-BR -> automação (não salva). `marcar(text)` (opcional) devolve as marcações do modelo."""
    texto = " ".join(str(frase or "").split())[:600]
    if len(texto) < 8:
        raise ValueError("escreva a automação, ex.: “Se eu receber uma mensagem sobre orçamentos, crie um card de alerta”")
    s = fold(texto)
    m = re.match(r"^\s*(?:se|quando|sempre que|toda vez que|caso)\s+(.+?)(?:,\s*|\s+(?:entao|então)\s+)(.+)$", texto,
                 re.I)
    if m:
        cond, faz = m.group(1), m.group(2)
    else:
        parts = re.split(r",\s*", texto, maxsplit=1)
        cond, faz = (parts + [""])[:2] if len(parts) == 2 else (texto, texto)
    sc, sf = fold(cond), fold(faz)
    notas, quando = [], {"fontes": [], "apps": [], "palavras": []}
    if re.search(r"\be-?mails?\b", sc):
        quando["fontes"].append("email")
    if re.search(r"\bnotificac", sc):
        quando["fontes"].append("notifications")
    quando["apps"] = [a for a in APPS if re.search(rf"\b{a.casefold()}\b", sc)]
    ass = re.search(r"\b(?:sobre|falando de|falando sobre|a respeito de|relacionad[ao]s? a|referente a)\s+(.+)$", cond, re.I)
    if ass:
        a = re.split(r"\s+(?:d[oa]s?|de)\s+(?=[A-ZÁÉÍÓÚ])|\s+(?:no|pelo|pela|do app)\s+", ass.group(1).strip(" .,;"))[0]
        quando["assunto"] = a.strip(" .,;")
    rem = re.search(r"\b(?:d[oa]|de)\s+([A-ZÁÉÍÓÚÂÊÔÃÕ][\wÀ-ÿ]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕ][\wÀ-ÿ]+)?|[\w.+-]+@[\w.-]+)", cond)
    if rem and rem.group(1) not in APPS:
        quando["remetente"] = rem.group(1)
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
    for rx, ac in ACAO_RX:
        if re.search(rx, sf) and ac not in acoes:
            if ac == "criar_card" and ("alerta" in acoes or "tarefa" in acoes):
                continue
            acoes.append(ac)
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
            p["titulo"] = (rotulo[:1].upper() + rotulo[1:] if rotulo else "Mensagem") + " — {remetente}"
            p["mensagem"] = "{text:140}"
        elif ac == "abrir_app":
            p["app"] = "email" if re.search(r"e-?mail", sf) else "navegador" if re.search(r"navegador|site", sf) else "whatsapp"
        entao.append({"acao": ac, "params": p})
    nome = (rotulo[:1].upper() + rotulo[1:]) if rotulo else "Automação"
    a = normalizar({"nome": nome, "quando": quando, "entao": entao})
    if not any(a["quando"][k] for k in ("fontes", "apps", "remetente", "palavras", "assunto", "categoria",
                                        "prioridade_min", "vip")):
        notas.append("Vale para tudo que chegar. Se não for isso, diga de quem, de qual app ou sobre o quê.")
    a["origem"] = texto
    return {"automacao": a, "resumo": resumo(a), "notas": notas}


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
