"""Preferências seguras de interface (lista fechada, nada de código), em HOME/ui.json.

Também entende comandos em português ("deixa o tema escuro", "esconde a coluna feito",
"salva filtro urgentes desta semana") e devolve um patch validado.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import re
import threading
import unicodedata
from pathlib import Path
from typing import Dict, Optional

from .cards import COLUMNS, KINDS

ACCENTS = {"rosa": "#b56576", "vinho": "#6d2e46", "terracota": "#c06c4a", "mostarda": "#c9a227",
           "verde": "#5a7d5a", "azul": "#4a6c8c", "lavanda": "#8a7bb0", "grafite": "#4a4a4a"}
VIEWS = ("inicio", "kanban", "agenda", "lista", "mensagens", "notificacoes", "automacoes")
SIDEBAR = ("inicio", "kanban", "agenda", "lista", "busca", "integracoes", "regras", "modelo", "configuracoes")
FILTER_KEYS = ("q", "kind", "column", "tag", "from", "to", "source", "priority", "done", "person")

DEFAULTS = {
    "theme": "auto",                 # claro | escuro | auto
    "density": "normal",             # compacta | normal | confortavel
    "accent": "rosa",                # nome da paleta da marca
    "default_view": "inicio",
    "kanban_columns": list(COLUMNS),
    "saved_filters": [],             # [{name, filters:{...}}]
    "sidebar_order": list(SIDEBAR),
    "voice_enabled": False,
    "speak_replies": False,
    "font_size": "normal",           # pequena | normal | grande | muito grande
    "prioridade": "modelo",          # modelo (o DOFL decide) | equilibrado | regras (respostas rápidas)
    "nome_usuario": "",              # vazio -> "você"
    "nome_agente": "Djorge",
    "destaques": [],                 # [{id, tipo, alvo, estilo:{cor, icone, fixar}, ate}] (lista fechada, sem código)
    "inicio_secoes": list(("destaques",) + ("tarefas", "eventos", "mensagens", "notificacoes")),
    "inicio_ocultas": [],            # seções do Início escondidas
    "secao_destaque": "",            # seção do Início em destaque ("" = nenhuma)
}
CHOICES = {"theme": ("claro", "escuro", "auto"), "density": ("compacta", "normal", "confortavel"),
           "accent": tuple(ACCENTS), "default_view": VIEWS,
           "font_size": ("pequena", "normal", "grande", "muito grande"),
           "prioridade": ("modelo", "equilibrado", "regras")}
# o front usa outros nomes para alguns valores e chaves: aceitos aqui e gravados no nome do servidor
ALIAS_CHAVE = {"columns": "kanban_columns", "voice": "voice_enabled", "speak": "speak_replies",
               "user_name": "nome_usuario", "agent_name": "nome_agente", "priority_mode": "prioridade"}
ALIAS_VALOR = {"theme": {"light": "claro", "dark": "escuro"},
               "density": {"compact": "compacta", "comfortable": "normal", "comfy": "confortavel", "confortável": "confortavel"},
               "font_size": {"sm": "pequena", "md": "normal", "lg": "grande", "xl": "muito grande"},
               "default_view": {"quadro": "kanban", "tarefas": "lista", "início": "inicio"},
               "prioridade": {"rapido": "regras", "rápido": "regras", "rules": "regras", "model": "modelo"}}
EXTRA_VIEWS = ("mensagens", "notificacoes", "automacoes")
FONT_STEPS = CHOICES["font_size"]
DENS_STEPS = CHOICES["density"]


# ------------------------------------------------------------------ destaques (o agente edita a interface) --
TIPOS_DESTAQUE = ("card", "notificacao", "remetente", "etiqueta", "secao")
ICONES = ("estrela", "coracao", "alerta", "pino", "sino", "fogo")
SECOES_INICIO = ("destaques", "tarefas", "eventos", "mensagens", "notificacoes")
SECAO_PT = {"destaques": "Em destaque", "tarefas": "Tarefas mais relevantes", "eventos": "Próximos eventos",
            "mensagens": "Mensagens importantes", "notificacoes": "Notificações recentes"}
ALVOS_CARD = ("@urgentes", "@importantes", "@hoje", "@atrasadas")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")


def estilo_destaque(e) -> Dict:
    """Estilo seguro: cor da paleta, ícone da lista, fixar no topo. Levanta ValueError."""
    e = e if isinstance(e, dict) else {}
    cor = str(e.get("cor") or "rosa")
    if cor not in ACCENTS:
        raise ValueError(f"cor do destaque: use {', '.join(ACCENTS)}")
    ic = str(e.get("icone") or "estrela")
    if ic not in ICONES:
        raise ValueError(f"ícone do destaque: use {', '.join(ICONES)}")
    return {"cor": cor, "icone": ic, "fixar": bool(e.get("fixar", True))}


def ate_valido(v) -> str:
    v = str(v or "").strip()
    if v and not _ISO.match(v):
        raise ValueError("ate: data ISO (AAAA-MM-DD ou AAAA-MM-DDTHH:MM)")
    return v


def destaque_valido(d) -> Dict:
    if not isinstance(d, dict):
        raise ValueError("destaque: objeto esperado")
    tipo = d.get("tipo")
    if tipo not in TIPOS_DESTAQUE:
        raise ValueError(f"destaque.tipo: use {', '.join(TIPOS_DESTAQUE)}")
    alvo = " ".join(str(d.get("alvo") or "").split())[:80]
    if not alvo or re.search(r"[<>{}\\;]", alvo):
        raise ValueError("destaque.alvo: escreva só o nome, id ou etiqueta")
    if tipo == "secao" and alvo not in SECOES_INICIO:
        raise ValueError(f"destaque de seção: use {', '.join(SECOES_INICIO)}")
    if tipo == "card" and alvo.startswith("@") and alvo not in ALVOS_CARD:
        raise ValueError(f"destaque de cards: use um id ou {', '.join(ALVOS_CARD)}")
    did = re.sub(r"[^\w-]", "", str(d.get("id") or "") or f"{tipo}-{_fold(alvo).replace(' ', '-')}")[:40]
    return {"id": did, "tipo": tipo, "alvo": alvo, "estilo": estilo_destaque(d.get("estilo")), "ate": ate_valido(d.get("ate"))}


def destaques_ativos(lista, agora: Optional[_dt.datetime] = None):
    agora = (agora or _dt.datetime.now()).strftime("%Y-%m-%dT%H:%M")
    return [d for d in lista or [] if not d.get("ate") or (d["ate"] if len(d["ate"]) > 10 else d["ate"] + "T23:59") >= agora]


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def validate(patch: Dict) -> Dict:
    """Patch estrito: só chaves conhecidas, valores da lista fechada. Levanta ValueError."""
    if not isinstance(patch, dict):
        raise ValueError("preferências: objeto esperado")
    out = {}
    for k, v in patch.items():
        if k not in DEFAULTS:
            raise ValueError(f"preferência desconhecida: {k}")
        if k in CHOICES:
            if v not in CHOICES[k]:
                raise ValueError(f"{k}: use {', '.join(CHOICES[k])}")
        elif k in ("nome_usuario", "nome_agente"):
            if not isinstance(v, str) or re.search(r"[<>{}\\]", v):
                raise ValueError(f"{k}: escreva só o nome")
            v = " ".join(v.split())[:40]
            if k == "nome_agente" and not v:
                v = DEFAULTS["nome_agente"]
        elif k in ("voice_enabled", "speak_replies"):
            if not isinstance(v, bool):
                raise ValueError(f"{k}: verdadeiro ou falso")
        elif k == "kanban_columns":
            if not isinstance(v, list) or not v or any(c not in COLUMNS for c in v) or len(set(v)) != len(v):
                raise ValueError(f"kanban_columns: lista sem repetição com {', '.join(COLUMNS)}")
        elif k == "sidebar_order":
            if not isinstance(v, list) or not v or len(set(v)) != len(v) or \
                    any(not isinstance(x, str) or x not in SIDEBAR + EXTRA_VIEWS + ("quadro", "tarefas") for x in v):
                raise ValueError(f"sidebar_order: itens sem repetição entre {', '.join(SIDEBAR + EXTRA_VIEWS)}")
        elif k == "destaques":
            if not isinstance(v, list) or len(v) > 40:
                raise ValueError("destaques: lista com até 40 itens")
            vistos, clean = set(), []
            for d in v:
                d = destaque_valido(d)
                if d["id"] not in vistos:
                    vistos.add(d["id"]); clean.append(d)
            v = clean
        elif k in ("inicio_secoes", "inicio_ocultas"):
            if not isinstance(v, list) or len(set(v)) != len(v) or any(x not in SECOES_INICIO for x in v) or \
                    (k == "inicio_secoes" and not v):
                raise ValueError(f"{k}: itens sem repetição entre {', '.join(SECOES_INICIO)}")
        elif k == "secao_destaque":
            if v not in ("",) + SECOES_INICIO:
                raise ValueError(f"secao_destaque: use {', '.join(SECOES_INICIO)} ou vazio")
        elif k == "saved_filters":
            if not isinstance(v, list) or len(v) > 30:
                raise ValueError("saved_filters: lista com até 30 filtros")
            clean = []
            for f in v:
                if not isinstance(f, dict) or not str(f.get("name") or "").strip():
                    raise ValueError("saved_filters: cada filtro precisa de {name, filters}")
                fl = f.get("filters") or {}
                if not isinstance(fl, dict) or any(x not in FILTER_KEYS for x in fl):
                    raise ValueError(f"saved_filters: filtros aceitos: {', '.join(FILTER_KEYS)}")
                fl = {x: str(y)[:120] for x, y in fl.items() if y not in (None, "")}
                if fl.get("kind") and any(x not in KINDS for x in fl["kind"].split(",")):
                    raise ValueError("saved_filters: kind inválido")
                if fl.get("column") and any(x not in COLUMNS for x in fl["column"].split(",")):
                    raise ValueError("saved_filters: column inválida")
                clean.append({"name": str(f["name"]).strip()[:60], "filters": fl})
            v = clean
        out[k] = v
    return out


def normalizar(patch: Dict) -> Dict:
    """Troca os nomes do front pelos do servidor (theme "dark" -> "escuro", "columns" -> "kanban_columns"...)."""
    if not isinstance(patch, dict):
        return patch
    out = {}
    for k, v in patch.items():
        k = ALIAS_CHAVE.get(k, k)
        if isinstance(v, str) and k in ALIAS_VALOR:
            v = ALIAS_VALOR[k].get(v, v)
        if k in ("voice_enabled", "speak_replies") and v in (0, 1):
            v = bool(v)
        out[k] = v
    return out


class UIPrefs:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def get(self) -> Dict:
        cur = copy.deepcopy(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in (raw or {}).items():
                try:
                    cur.update(validate({k: v}))
                except ValueError:
                    pass                                   # valor estragado: fica o padrão
        except (OSError, ValueError):
            pass
        return cur

    def set(self, patch: Dict) -> Dict:
        """Grava as chaves válidas. Chaves desconhecidas (de versões novas do front, ou "_server") são ignoradas
        quando há alguma chave conhecida no patch: antes um único campo estranho fazia a gravação inteira falhar
        e nada ficava salvo. Valor inválido numa chave conhecida continua sendo erro (ValueError)."""
        patch = normalizar(patch)
        if not isinstance(patch, dict):
            raise ValueError("preferências: objeto esperado")
        conhecidas = {k: v for k, v in patch.items() if k in DEFAULTS}
        if not conhecidas:
            validate(patch)                                # nada conhecido: erro claro ("preferência desconhecida")
        p = validate(conhecidas)
        with self.lock:
            cur = self.get()
            cur.update(p)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            return cur

    # ------------------------------------------------------------------ destaques --
    _SEC_RX = {"tarefas": r"tarefas?", "eventos": r"eventos?|agenda|compromissos?", "mensagens": r"mensagens?",
               "notificacoes": r"notificac\w*", "destaques": r"destaques?|em destaque"}

    def _parse_destaque(self, text: str, s: str, cur: Dict, hoje) -> Optional[Dict]:
        if re.search(r"\bcor (de )?destaque\b", s):
            return None
        ds = list(cur.get("destaques") or [])
        no_inicio = re.search(r"\b(no |do |o )?(inicio|painel|tela inicial)\b", s)
        secs = [k for k, rx in self._SEC_RX.items() if re.search(rf"\b(?:{rx})\b", s)]
        # ordem e visibilidade das seções do Início
        if no_inicio and secs and not re.search(r"\bdestac|\bdestaque", s):
            sec = secs[0]
            if re.search(r"\b(esconde|esconda|oculta|oculte|tira|tire|remove|remova)\b", s):
                oc = [x for x in cur.get("inicio_ocultas") or [] if x != sec] + [sec]
                return {"patch": {"inicio_ocultas": oc}, "descricao": f"esconder “{SECAO_PT[sec]}” do Início"}
            if re.search(r"\b(mostra|mostre|exibe|exiba|volta|traz|traga)\b", s):
                oc = [x for x in cur.get("inicio_ocultas") or [] if x != sec]
                return {"patch": {"inicio_ocultas": oc}, "descricao": f"mostrar “{SECAO_PT[sec]}” no Início"}
            if re.search(r"\b(primeiro|no topo|em cima|antes|no comeco)\b", s):
                ordem = [sec] + [x for x in cur.get("inicio_secoes") or SECOES_INICIO if x != sec]
                return {"patch": {"inicio_secoes": ordem}, "descricao": f"“{SECAO_PT[sec]}” primeiro no Início"}
            if re.search(r"\b(por ultimo|no fim|embaixo|depois)\b", s):
                ordem = [x for x in cur.get("inicio_secoes") or SECOES_INICIO if x != sec] + [sec]
                return {"patch": {"inicio_secoes": ordem}, "descricao": f"“{SECAO_PT[sec]}” por último no Início"}
            return None
        if not re.search(r"\b(destac\w*|destaques?|realc\w*|ressalt\w*)\b", s):
            return None
        # tirar destaque
        if re.search(r"\b(tira|tire|tirar|remove|remova|apaga|apague|limpa|limpe|desfaz|desfaca|sem)\b", s):
            alvo = re.search(r"\bdestaques?\s+(?:d[aoe]s?\s+)(?:mensagens?\s+|notificac\w+\s+|e-?mails?\s+)?(?:d[aoe]s?\s+)?(.+)$", s)
            if alvo:
                a = alvo.group(1).strip()
                resto = [d for d in ds if a not in _fold(d["alvo"]) and _fold(d["alvo"]) not in a]
                sec = cur.get("secao_destaque") if not any(re.search(rf"\b(?:{rx})\b", a) for rx in self._SEC_RX.values()) else ""
                if len(resto) != len(ds) or sec != cur.get("secao_destaque"):
                    return {"patch": {"destaques": resto, "secao_destaque": sec or ""}, "descricao": f"tirar o destaque de {a}"}
            return {"patch": {"destaques": [], "secao_destaque": ""}, "descricao": "tirar todos os destaques"}
        cor = next((c for c in ACCENTS if re.search(rf"\b{c}\b", s)), "rosa")
        icone = "coracao" if re.search(r"\b(amor|coracao)\b", s) else "alerta" if re.search(r"\burgent", s) else \
            "pino" if re.search(r"\b(fixa|fixe|topo)\b", s) else "estrela"
        ate = ""
        if re.search(r"\b(so )?hoje\b", s):
            ate = hoje.isoformat()
        elif re.search(r"\bate amanha\b", s):
            ate = (hoje + _dt.timedelta(1)).isoformat()
        else:
            m = re.search(r"\bate (?:o dia |dia )?(\d{1,2})/(\d{1,2})\b", s)
            if m:
                try:
                    dt = _dt.date(hoje.year, int(m.group(2)), int(m.group(1)))
                    ate = (dt if dt >= hoje else dt.replace(year=hoje.year + 1)).isoformat()
                except ValueError:
                    pass
        estilo = {"cor": cor, "icone": icone, "fixar": True}
        novo = None
        m = re.search(r"\b(?:mensagens?|notificac\w+|e-?mails?|coisas|tudo|recados?|whats\w*)\s+(?:que\s+chegam?\s+)?"
                      r"(?:d[aoe]s?|vind[ao]s? d[aoe]s?)\s+([A-Za-zÀ-ÿ][\wÀ-ÿ.@-]*(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]*){0,2})", text)
        if m and _fold(m.group(1)) not in ("hoje", "semana", "inicio", "painel"):
            nome = " ".join(w[:1].upper() + w[1:] for w in m.group(1).split())
            novo = {"tipo": "remetente", "alvo": nome, "estilo": estilo, "ate": ate}
            desc = f"mensagens de {nome} em destaque"
        elif re.search(r"\btarefas?\b|\bcards?\b", s) and re.search(r"\b(urgentes?|importantes?|de hoje|atrasad\w*)\b", s):
            alvo = "@urgentes" if "urgent" in s else "@atrasadas" if "atrasad" in s else "@hoje" if "de hoje" in s else "@importantes"
            novo = {"tipo": "card", "alvo": alvo, "estilo": estilo, "ate": ate}
            desc = {"@urgentes": "tarefas urgentes", "@atrasadas": "tarefas atrasadas", "@hoje": "tarefas de hoje",
                    "@importantes": "tarefas importantes"}[alvo] + " em destaque" + (" no Início" if no_inicio else "")
        elif re.search(r"#([\w-]+)|\betiqueta\s+([\wÀ-ÿ-]+)", text):
            m2 = re.search(r"#([\w-]+)|\betiqueta\s+([\wÀ-ÿ-]+)", text)
            et = (m2.group(1) or m2.group(2)).casefold()
            novo = {"tipo": "etiqueta", "alvo": et, "estilo": estilo, "ate": ate}
            desc = f"etiqueta “{et}” em destaque"
        elif secs:
            sec = secs[0]
            novo = {"tipo": "secao", "alvo": sec, "estilo": estilo, "ate": ate}
            d = destaque_valido(novo)
            resto = [x for x in ds if x["tipo"] != "secao"] + [d]
            return {"patch": {"destaques": resto[-40:], "secao_destaque": sec}, "descricao": f"“{SECAO_PT[sec]}” em destaque"}
        if novo is None:
            return None
        d = destaque_valido(novo)
        resto = [x for x in ds if x["id"] != d["id"]] + [d]
        return {"patch": {"destaques": resto[-40:]}, "descricao": desc + (f" até {ate}" if ate else "") +
                (f" ({cor})" if cor != "rosa" else "")}

    # ------------------------------------------------------------------ comandos em português --
    def parse(self, text: str, hoje: Optional[_dt.date] = None) -> Optional[Dict]:
        """Texto -> {"patch": {...}, "descricao": "..."} ou None se não for um comando de interface."""
        s = _fold(text).strip(" .!?")
        cur = self.get()
        hoje = hoje or _dt.date.today()
        try:
            from .pistas import condicional
            if condicional(text):                          # "sempre que… destaca": é automação, não interface
                return None
        except Exception:
            pass
        d = self._parse_destaque(text, s, cur, hoje)
        if d is not None:
            return d
        # nomes: "me chama de Arthur", "seu nome agora é Jorge"
        m = re.search(r"(?i)^(?:(?:por favor|pode)\s+)?(?:me\s+cham[ae]r?|cham[ae]-me|meu nome (?:e|é))\s+(?:de\s+)?"
                      r"([A-Za-zÀ-ÿ][\wÀ-ÿ'-]*(?:\s+[A-Za-zÀ-ÿ][\wÀ-ÿ'-]*)?)\s*[.!]?$", text.strip())
        if m:
            nome = " ".join(w[:1].upper() + w[1:] for w in m.group(1).split())
            return {"patch": {"nome_usuario": nome}, "descricao": f"chamar você de {nome}"}
        m = re.search(r"(?i)^(?:(?:agora\s+)?(?:o\s+)?seu nome (?:agora\s+)?(?:e|é|vai ser|sera|será)|vou te chamar de|"
                      r"(?:pode\s+)?se chamar)\s+([A-Za-zÀ-ÿ][\wÀ-ÿ'-]*)\s*[.!]?$", text.strip())
        if m:
            nome = m.group(1)[:1].upper() + m.group(1)[1:]
            return {"patch": {"nome_agente": nome}, "descricao": f"o assistente agora se chama {nome}"}
        if re.search(r"\b(respostas? rapidas?|regras primeiro|prioridade (as|para as|pras) regras)\b", s) and \
                re.search(r"\b(prefir\w*|usa|use|quero|ativa|ative|liga|ligue|prioriza\w*|da|de)\b", s):
            return {"patch": {"prioridade": "regras"}, "descricao": "respostas rápidas (regras primeiro)"}
        if re.search(r"\b(modelo primeiro|prioridade (ao|para o|pro) modelo|deixa o modelo decidir)\b", s):
            return {"patch": {"prioridade": "modelo"}, "descricao": "o modelo decide primeiro"}
        # tema
        if re.search(r"\b(tema|modo|deixa|deixe|muda|mude|coloca|coloque|poe|bota|usa|use|ativa|ative|liga|ligue)\b.*"
                     r"\b(escuro|noturno|dark|claro|light|automatico|auto)\b", s) or re.fullmatch(r"(tema |modo )?(escuro|claro)", s):
            if re.search(r"\b(escuro|noturno|dark)\b", s):
                return {"patch": {"theme": "escuro"}, "descricao": "tema escuro"}
            if re.search(r"\b(claro|light)\b", s):
                return {"patch": {"theme": "claro"}, "descricao": "tema claro"}
            return {"patch": {"theme": "auto"}, "descricao": "tema automático (segue o Windows)"}
        # colunas do kanban
        m = re.search(r"\b(esconde|esconda|oculta|oculte|tira|tire|remove|remova|mostra|mostre|exibe|exiba|volta|"
                      r"reexibe|traz|traga)\b.*\bcolunas?\b\s*(?:de\s+|do\s+|da\s+)?(.+)$", s)
        if m:
            cols = [c for c in COLUMNS if re.search(rf"\b{c}\b", m.group(2))]
            if cols:
                hide = m.group(1).startswith(("escond", "ocult", "tir", "remov"))
                vis = [c for c in cur["kanban_columns"] if c not in cols] if hide else \
                      [c for c in COLUMNS if c in cur["kanban_columns"] or c in cols]
                if not vis:
                    return None
                return {"patch": {"kanban_columns": vis},
                        "descricao": ("esconder" if hide else "mostrar") + " a coluna " + " e ".join(cols)}
        # filtros salvos
        m = re.search(r"\b(salva|salve|guarda|guarde|cria|crie)\b\s+(?:um\s+|o\s+)?filtro\s+(?:de\s+|chamado\s+)?(.+)$", s)
        if m:
            nome = re.search(r"filtro\s+(?:de\s+|chamado\s+)?(.+)$", text.strip(" .!?"), re.I)
            nome = (nome.group(1) if nome else m.group(2)).strip(" \"'“”")
            f = {}
            if re.search(r"\burgent", m.group(2)):
                f["priority"] = "3"
            elif re.search(r"\b(importante|alta prioridade|prioridade alta)", m.group(2)):
                f["priority"] = "2+"
            if re.search(r"\b(desta|nesta|essa|esta) semana\b", m.group(2)):
                f["from"] = (hoje - _dt.timedelta(hoje.weekday())).isoformat()
                f["to"] = (hoje + _dt.timedelta(6 - hoje.weekday())).isoformat()
            elif re.search(r"\bhoje\b", m.group(2)):
                f["from"] = f["to"] = hoje.isoformat()
            for k in KINDS:
                if re.search(rf"\b{k}s?\b", m.group(2)):
                    f["kind"] = k
            if re.search(r"\b(pendente|aberto|nao feit)", m.group(2)):
                f["done"] = "false"
            tg = re.search(r"#([\w-]+)", text)
            if tg:
                f["tag"] = tg.group(1)
            if not f:
                f["q"] = nome
            fs = [x for x in cur["saved_filters"] if _fold(x["name"]) != _fold(nome)] + [{"name": nome, "filters": f}]
            return {"patch": {"saved_filters": fs[-30:]}, "descricao": f"salvar o filtro “{nome}”"}
        m = re.search(r"\b(apaga|apague|remove|remova|exclui|exclua)\b\s+o\s+filtro\s+(.+)$", s)
        if m:
            fs = [x for x in cur["saved_filters"] if _fold(x["name"]) != m.group(2).strip()]
            if len(fs) != len(cur["saved_filters"]):
                return {"patch": {"saved_filters": fs}, "descricao": f"apagar o filtro “{m.group(2).strip()}”"}
        # visão padrão
        m = re.search(r"\b(abre|abrir|abra|comeca|comecar|comece|inicia|iniciar|visao padrao|tela inicial|padrao)\b.*"
                      r"\b(inicio|kanban|quadro|agenda|calendario|lista)\b", s)
        if m and re.search(r"\b(sempre|padrao|comec|inici|tela inicial)", s):
            v = {"quadro": "kanban", "calendario": "agenda"}.get(m.group(2), m.group(2))
            return {"patch": {"default_view": v}, "descricao": f"abrir sempre em {v}"}
        # voz e fala das respostas
        if re.search(r"\b(fala|fale|ler|le|leia)\b.*\brespostas?\b|\brespostas?\b.*\b(em voz alta|faladas?)\b", s):
            off = bool(re.search(r"\b(nao|para de|pare de|desliga|sem)\b", s))
            return {"patch": {"speak_replies": not off}, "descricao": ("não " if off else "") + "falar as respostas"}
        if re.search(r"\b(liga|ligue|ativa|ative|desliga|desligue|desativa|desative)\b.*\b(voz|microfone)\b", s):
            off = bool(re.search(r"\bdes(liga|ligue|ativa|ative)\b", s))
            return {"patch": {"voice_enabled": not off}, "descricao": ("desligar" if off else "ligar") + " a voz"}
        # fonte
        if re.search(r"\b(fonte|letra|texto)\b", s) and re.search(r"\b(maior|menor|aumenta|aumente|diminui|diminua|"
                                                                   r"pequena|grande|normal)\b", s):
            i = FONT_STEPS.index(cur["font_size"])
            if re.search(r"\b(maior|aumenta|aumente)\b", s):
                i = min(i + 1, len(FONT_STEPS) - 1)
            elif re.search(r"\b(menor|diminui|diminua)\b", s):
                i = max(i - 1, 0)
            else:
                i = FONT_STEPS.index("muito grande" if "muito grande" in s else "grande" if "grande" in s else
                                     "pequena" if "pequena" in s else "normal")
            return {"patch": {"font_size": FONT_STEPS[i]}, "descricao": f"fonte {FONT_STEPS[i]}"}
        # densidade
        if re.search(r"\b(compact[ao]|densidade|espacad[ao]|confortavel|apertad[ao]|mais espaco|menos espaco)\b", s):
            if re.search(r"\b(compact|apertad|menos espaco)", s):
                v = "compacta"
            elif re.search(r"\b(espacad|confortavel|mais espaco)", s):
                v = "confortavel"
            else:
                v = "normal"
            return {"patch": {"density": v}, "descricao": f"densidade {v}"}
        # cor de destaque
        m = re.search(r"\b(cor|destaque)\b.*\b(" + "|".join(ACCENTS) + r")\b", s)
        if m:
            return {"patch": {"accent": m.group(2)}, "descricao": f"cor de destaque {m.group(2)}"}
        return None
