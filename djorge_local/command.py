"""O card de entrada estilo IA: texto -> ferramenta do app -> proposta -> (confirmação) -> execução.

    limpeza (clean) -> regras (reconhecer, atalho_envio, corpo_mensagem)
    -> DOFL, quando carregado (NOUL "o app consegue", CHOICE das ferramentas, resolve dos campos do app)
    -> combinar -> PISTAS + decidir (guardrails) -> proposta por ferramenta -> resposta por MODELOS DE FRASE
    -> formatar (nomes, datas, horas, valores, título).

O modelo só decide; toda frase mostrada sai das tabelas FRASE abaixo. Sem modelo, as pistas escolhem a ferramenta
e a resposta avisa disso; o que só o modelo faz (regra automática) levanta ModeloAusente (HTTP 409).
Na dúvida o comando pergunta com opções ("perguntas"); quando não entende, devolve "dicas" ("Tente: …").

Com stream=True, run() devolve {"id"} na hora e o trabalho segue numa thread; etapas(id) mostra o progresso
({"etapas": [{"t", "texto", "estado"}], "pronto", "resultado"?}).
"""
from __future__ import annotations

import collections
import datetime as _dt
import re
import threading
import time
import unicodedata
import urllib.parse
import uuid
from typing import Dict, List, Optional

from djorge.runtime.recognizers import (Trecho, _sem_acento, atalho_envio, combinar, corpo_mensagem, data_humana,
                                        reconhecer, resolver_quando)
from djorge.training.flash_data import ASK_TOOL, CAN, TOOL_PT, TOOLS

from . import formatar as F
from . import pistas as P

CAMPOS = ["data", "hora", "período do dia", "pessoa", "evento ou compromisso", "lugar", "lista",
          "endereço de e-mail", "empresa ou estabelecimento", "frequência ou repetição"]
# o que o modelo extrai (resolve ENTITY, com os nomes de campo do treino: SLOT_PT de commands_data). Datas, horas e
# repetição ficam SEMPRE com as regras (o treino deixou de propósito para o relógio do app).
CAMPOS_MODELO = ["pessoa", "empresa ou estabelecimento", "lugar", "evento ou compromisso", "lista", "aplicativo",
                 "endereço de e-mail"]
PRIORIDADES = ("modelo", "equilibrado", "regras")
PERGUNTA_REGRA = "Este pedido descreve uma regra que deve acontecer sempre que algo ocorrer?"
SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
KINDS = ("tarefa", "evento", "lembrete", "nota", "lista", "mensagem")

FRASE = {
    "sem_modelo": "(Sem o modelo: entendi só pelas regras.)",
    "calc": "{expr} = {valor}.",
    "calc_erro": "Não consegui montar essa conta. Escreva só os números e as operações, ex.: 18% de 2450.",
    "hora": "Agora são {hora}.",
    "data": "Hoje é {semana}, {data}.",
    "data_de": "{data_txt} cai {semana_de} ({data}).",
    "dias_ate": "Faltam {dias} dias para {data_txt} ({data}).",
    "dias_desde": "Já se passaram {dias} dias desde {data_txt} ({data}).",
    "dias_hoje": "{data_txt} é hoje.",
    "agenda_vazia": "Nada na agenda {periodo}.",
    "agenda": "Você tem {n} {itens} {periodo}. O próximo: {primeiro}.",
    "card_criado": "Criei {artigo} {tipo} “{titulo}”{quando}.",
    "card_confirmar": "Posso criar {artigo} {tipo} “{titulo}”{quando}?",
    "card_atualizado": "Atualizei {artigo} {tipo} “{titulo}”{quando}.",
    "repete": " ({rec})",
    "lista_criada": "Criei a lista “{titulo}” com {n} {itens}.",
    "msg_confirmar": "Enviar para {destino} pelo {canal}: “{corpo}”?",
    "msg_sem_canal": "Mensagem para {destino}: “{corpo}”. Conecte o Gmail, o Outlook ou o WhatsApp para enviar.",
    "msg_sem_destino": "Para quem devo mandar? Use, por exemplo, #ana@email.com &texto&.",
    "msg_sem_corpo": "O que devo dizer para {destino}?",
    "msg_enviada": "Mensagem enviada para {destino}.",
    "busca_vazia": "Não achei nada sobre “{q}” nos seus dados.",
    "busca": "Aqui estão {n} {itens} sobre “{q}”.",
    "busca_web": "Aqui estão resultados sobre “{q}”.",
    "web_vazia": "Não achei nada na internet sobre “{q}”.",
    "web_perguntar": "Quer que eu busque “{q}” na internet?",
    "web_depois_local": "Não achei nada sobre “{q}” nos seus dados. Quer que eu busque “{q}” na internet?",
    "abrir": "Abrir {alvo}{com}{texto}?",
    "abrir_ok": "Abrindo {alvo}.",
    "regra": "Montei a regra: {resumo} Confira no editor antes de ativar.",
    "regra_criada": "Regra “{nome}” ativada.",
    "automacao": "Posso criar esta automação? {resumo}.",
    "automacao_criada": "Automação “{nome}” salva e ativa.",
    "ui": "Pronto: {descricao}.",
    "nenhuma": "Não entendi bem o que fazer com isso. Posso criar cards e lembretes, montar listas, mandar mensagens, "
               "buscar, fazer contas, ver datas e montar regras.",
    "perguntar": "Quer que eu {opcoes}?",
    "cancelado": "Tudo bem, não fiz nada.",
    "bloqueado": "Não vou fazer isso: {motivos}.",
}
TIPO_PT = {"tarefa": ("a", "tarefa"), "evento": ("o", "evento"), "lembrete": ("o", "lembrete"),
           "nota": ("a", "nota"), "lista": ("a", "lista"), "mensagem": ("a", "mensagem")}
# etapas narradas: o agente (nome_agente das preferências) na 3ª pessoa, com os dados reais; uma linha por acontecimento
ETAPA = {"ler": "Lendo o seu pedido…", "entender": "{agente} está entendendo o que {quem} quer…",
         "data": "Reconheceu a data {data}", "prazo": "Reconheceu o prazo {data}", "repete": "Reconheceu a repetição {rec}",
         "achou": "Encontrou “{x}”", "entendeu": "Entendeu: {intencao}",
         "ferramenta": "{agente} está usando a ferramenta {nome}…", "web": "{agente} está buscando na internet…",
         "contato": "{agente} está procurando o contato de {nome}…", "pronto": "Pronto"}
INTENCAO = {"calcular": "fazer uma conta", "datas_horas": "ver uma data ou hora", "proximas_tarefas": "ver a agenda",
            "listas": "mexer numa lista", "bilhete": "mandar uma mensagem", "buscar": "buscar nos seus dados",
            "web": "buscar na internet", "abrir": "abrir um app", "regra": "criar uma regra automática",
            "interface": "mudar a aparência do app"}
INTENCAO_CARD = {"tarefa": "criar uma tarefa", "evento": "criar um evento", "lembrete": "criar um lembrete",
                 "nota": "criar uma nota"}
# "Encontre quem foi Silvio Santos" -> "Silvio Santos": o que sobra do pedido de busca sem o verbo e a pergunta
_BUSCA_VERBO = re.compile(r"(?i)^\s*(?:(?:por favor|pf|djorge)[,\s]+)?(?:me\s+)?(?:diz(?:er)?|diga|conta|conte|fala|fale|"
                          r"mostra|mostre)?\s*(?:procura|procure|busca|busque|acha|ache|encontra|encontre|pesquisa|"
                          r"pesquise|descobre|descubra|veja|v[eê])?(?:r)?\s*(?:pra mim\s+|para mim\s+)?"
                          r"(?:(?:na internet|na web|no google|na net)\s+)?(?:por\s+|pelo\s+|pela\s+|sobre\s+)?")
_PERGUNTA = re.compile(r"(?i)^\s*(?:quem\s+(?:é|e|foi|era|s[aã]o|foram)|o\s+que\s+(?:é|e|foi|significa)|"
                       r"(?:o\s+)?que\s+(?:é|e)|informa[cç][oõ]es\s+sobre|not[ií]cias\s+(?:sobre|de|do|da))\s+"
                       r"(?:o\s+|a\s+|os\s+|as\s+|um\s+|uma\s+)?")
SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
         "outlook": "https://outlook.live.com", "maps": "https://maps.google.com", "mapa": "https://maps.google.com",
         "spotify": "https://open.spotify.com", "navegador": "https://www.google.com", "chrome": "https://www.google.com",
         "edge": "https://www.bing.com", "firefox": "https://www.google.com"}
SEGUIR = {"decisao": "seguir", "motivos": [], "p": None}


def _alertas(text: str, agora) -> Optional[Dict]:
    """alertas.de_texto (V2_SPEC_4), protegido: sem o módulo ou com erro, nada de alerta."""
    try:
        from .alertas import de_texto
        return de_texto(text, agora)
    except Exception:
        return None


HIST_LIMPAR = re.compile(r"(?i)^\s*(?:(?:por favor|pf|djorge)[,\s]+)?(?:me\s+)?(?:apag\w*|limp\w*|exclu\w*|delet\w*|"
                         r"zer\w*|remov\w*)\s+(?:tudo\s+)?(?:(?:o|meu|todo\s+o)\s+)*hist[oó]rico\b(.*)$")


def pedido_limpar_historico(text: str, hoje) -> Optional[Dict]:
    """"apaga o histórico" -> {tudo: True}; "limpa o histórico de hoje" -> {hoje: data}; "... antes de 01/11" -> antes_de."""
    m = HIST_LIMPAR.search(str(text or ""))
    if not m:
        return None
    resto = _sem_acento(m.group(1).casefold())
    if re.search(r"\bde hoje\b|\bhoje\b", resto):
        return {"hoje": hoje.isoformat()}
    if re.search(r"\bantes\b", resto):
        try:
            q = resolver_quando(m.group(1), [], None)
            if q.get("data"):
                return {"antes_de": q["data"]}
        except Exception:
            pass
    return {"tudo": True}


class ModeloAusente(Exception):
    pass


def _fmt_num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}".replace(",", ".")
    s = f"{v:,.6f}".rstrip("0").rstrip(".")
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _data_pt(iso: str) -> str:
    d = _dt.date.fromisoformat(iso[:10])
    return f"{d.day} de {MESES[d.month - 1]}" + ("" if d.year == _dt.date.today().year else f" de {d.year}")


def _quando_pt(card: Dict) -> str:
    w = card.get("start") or card.get("due")
    if not w:
        return ""
    return " para " + (F.intervalo(w, card.get("end")) if card.get("start") else F.data(w))


def _now(cfg):
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo(cfg.get("timezone") or "America/Sao_Paulo")).replace(tzinfo=None, second=0,
                                                                                               microsecond=0)
    except Exception:
        return _dt.datetime.now().replace(second=0, microsecond=0)


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def keyword_tool(text: str) -> (str, float):
    """Compatibilidade: a ferramenta só pelas pistas (sem o modelo)."""
    d = P.decidir(None, P.pistas(text), texto=text)
    return d["tool"], d["confianca"]


PREFIXO = re.compile(r"^\s*(?:(?:por favor|pf|ei|djorge)[,\s]+)*(?:(?:me\s+)?(?:lembra|lembre|lembrar|avisa|avise)(?:-me)?\s*[:,\-]?\s+(?:de\s+|que\s+)?|"
                     r"(?:marca|marque|marcar|agenda|agende|agendar|anota|anote|anotar|cria|crie|criar|adiciona|"
                     r"adicione|coloca|coloque|bota|poe|põe)(?:\s+a[ií])?\s*[:,\-]?\s+(?:um[a]?\s+|o\s+|a\s+)?(?:(?:card|tarefa|lembrete|"
                     r"evento|nota|compromisso|alarme)\s*[:\-]?\s+(?:de\s+|para\s+|pra\s+)?)?|(?:preciso|tenho que)\s*[:,]?\s+)", re.I)
BORDAS = {"às", "as", "a", "à", "de", "do", "da", "no", "na", "em", "para", "pra", "pro", "dia", "e", "o", "que",
          "com", "até", "ate", "hoje", "por", "volta", "lá", "la", "pelas", "das", "dos", "toda", "todo", "todos", "todas"}
PREP_CAMPO = {"às", "as", "a", "à", "em", "no", "na", "de", "do", "da", "para", "pra", "pro", "até", "ate", "dia",
              "pelas", "pelo", "pela", "ao", "desde", "por", "toda", "todo", "durante", "nos", "e"}


def _sem_campos(text: str, fields: List[Trecho], drop=("data", "hora", "período do dia", "frequência ou repetição",
                                                       "endereço de e-mail", "link de reunião", "duração")) -> str:
    keep = list(text)
    for f in fields:
        if f.campo in drop:
            for i in range(f.ini, min(f.fim, len(keep))):
                keep[i] = " "
            j = f.ini - 1                                  # a preposição que só servia ao campo vai junto
            while j >= 0 and keep[j] == " ":
                j -= 1
            k = j
            while k >= 0 and keep[k] not in " ,.;:!?":
                k -= 1
            if j >= 0 and "".join(keep[k + 1:j + 1]).casefold() in PREP_CAMPO:
                for i in range(k + 1, j + 1):
                    keep[i] = " "
    t = PREFIXO.sub("", "".join(keep))
    t = re.sub(r"(?i)^\s*(?:um[a]?\s+)?(?:lembrete|tarefa|evento|nota|card|compromisso|alarme)\s*[:\-]?\s+(?:de\s+|para\s+|pra\s+)?(?=\w)", "", t)
    words = t.split()
    while words and words[-1].casefold().strip(",.;:!?") in BORDAS:
        words.pop()
    while words and words[0].casefold().strip(",.;:!?") in BORDAS:
        words.pop(0)
    t = " ".join(words).strip(" ,.;:!?-")
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return (t[:1].upper() + t[1:])[:200] if t else ""


class Command:
    def __init__(self, app):
        self.app = app
        self.pending: "collections.OrderedDict[str, Dict]" = collections.OrderedDict()
        self.lock = threading.Lock()
        self._etapas: "collections.OrderedDict[str, Dict]" = collections.OrderedDict()
        self.web_transporte = None                        # testes injetam um transporte falso para web.py

    # ------------------------------------------------------------------ etapas --
    def _etapa(self, cid: Optional[str], texto: str, estado: str = "agora"):
        if not cid:
            return
        with self.lock:
            e = self._etapas.get(cid)
            if e is None:
                return
            for x in e["etapas"]:
                if x["estado"] == "agora":
                    x["estado"] = "ok"
            if e["etapas"] and e["etapas"][-1]["texto"] == texto:
                e["etapas"][-1]["estado"] = estado
                return
            e["etapas"].append({"t": round((time.perf_counter() - e["_t0"]) * 1000, 1), "texto": texto, "estado": estado})

    def _abre_etapas(self, cid: str):
        with self.lock:
            self._etapas[cid] = {"etapas": [], "pronto": False, "_t0": time.perf_counter()}
            while len(self._etapas) > 200:
                self._etapas.popitem(last=False)

    def _fecha_etapas(self, cid: str, resultado=None, erro: Optional[str] = None):
        if erro:
            self._etapa(cid, erro, "erro")
        else:
            self._etapa(cid, ETAPA["pronto"], "ok")
        with self.lock:
            e = self._etapas.get(cid)
            if e is not None:
                e["pronto"] = True
                if resultado is not None:
                    e["resultado"] = resultado

    def etapas(self, cid: str) -> Dict:
        """{"etapas": [{"t", "texto", "estado": "ok|agora|erro"}], "pronto": bool, "resultado"?: ...}"""
        with self.lock:
            e = self._etapas.get(str(cid))
            if e is None:
                raise KeyError(f"pedido não encontrado ou expirado: {cid}")
            out = {"etapas": [dict(x) for x in e["etapas"]], "pronto": e["pronto"]}
            if "resultado" in e:
                out["resultado"] = e["resultado"]
            return out

    # ------------------------------------------------------------------ modelo --
    def engine(self):
        """O motor do DOFL só se já estiver carregado (o comando nunca trava esperando o modelo)."""
        m = self.app.models
        try:
            names = [n for n in m.loaded() if n in m.decision_models()]
        except Exception:
            return None
        if not names:
            return None
        z = m.get(names[0])
        return getattr(z.semantic, "engine", z.semantic)

    def _prefs(self) -> Dict:
        """prioridade (modelo|equilibrado|regras), nome do agente e da pessoa, das preferências gravadas."""
        try:
            u = self.app.ui.get() or {}
        except Exception:
            u = {}
        pr = u.get("prioridade") if u.get("prioridade") in PRIORIDADES else "modelo"
        return {"prioridade": pr, "agente": (str(u.get("nome_agente") or "").strip() or "Djorge"),
                "usuario": str(u.get("nome_usuario") or "").strip()}

    def _model(self, eng, text: str) -> Dict:
        """Uma só chamada ao DOFL com as perguntas do treino: NOUL (CAN: o app consegue?), CHOICE das ferramentas
        (ASK_TOOL, TOOL_PT + descrições TOOLS) e resolve ENTITY dos campos do app (CAMPOS_MODELO)."""
        from djorge.runtime.semantic import SemReq
        names = list(TOOLS)
        reqs = [SemReq("NOUL", text, options=[CAN["pt"]]),
                SemReq("CHOICE", text, options=[TOOL_PT[t] for t in names], descriptions=[TOOLS[t] for t in names],
                       ask=ASK_TOOL["pt"])]
        reqs += [SemReq("resolve", text, mode="ENTITY", target=c, threshold=0.5) for c in CAMPOS_MODELO]
        reqs.append(SemReq("NOUL", text, options=[PERGUNTA_REGRA]))   # no mesmo lote
        out = eng.run(reqs)
        try:
            p_regra = float(out[-1].get("p_true", out[-1].get("probability", 0)))
        except Exception:
            p_regra = None
        out = out[:-1]
        can = float(out[0].get("p_true", out[0].get("probability", 0)))
        back = {TOOL_PT[t]: t for t in names}
        dist = out[1].get("distribution") or {}
        tools = {back[k]: round(float(v), 4) for k, v in dist.items() if k in back}
        trechos = []
        for campo, ms in zip(CAMPOS_MODELO, out[2:]):
            for m in getattr(ms, "matches", []) or []:
                if (getattr(m, "attributes", None) or {}).get("polarity") == "negated":
                    continue                                # "não é com a Ana" não vira a Ana
                trechos.append(Trecho(m.start, m.end, campo, text[m.start:m.end], valor=text[m.start:m.end].strip(),
                                      p=float(m.probability), fonte="modelo"))
        return {"can": can, "tools": tools, "trechos": trechos, "p_regra": p_regra}

    # ------------------------------------------------------------------ comando --
    def run(self, text: str, context: Optional[Dict] = None, stream: bool = False, cid: Optional[str] = None) -> Dict:
        if not str(text or "").strip():
            raise ValueError("escreva um pedido")
        cid = cid or uuid.uuid4().hex[:12]
        self._abre_etapas(cid)
        if not stream:
            try:
                out = self._run(text, context, cid)
            except ModeloAusente:
                self._fecha_etapas(cid, {"error": "modelo_ausente"}, "Preciso do modelo para isso")
                raise
            except Exception as e:
                self._fecha_etapas(cid, None, "Não deu certo")
                raise
            self._fecha_etapas(cid, None)
            return out

        def trabalho():
            try:
                out = self._run(text, context, cid)
                self._fecha_etapas(cid, out)
            except ModeloAusente as e:
                self._fecha_etapas(cid, {"error": "modelo_ausente", "tool": str(e),
                                         "dicas": ["Baixe o modelo para criar regras automáticas."]},
                                   "Preciso do modelo para isso")
            except Exception as e:
                self._fecha_etapas(cid, {"error": str(e)[:300], "dicas": P.dicas(text)}, "Não deu certo")
        threading.Thread(target=trabalho, daemon=True, name=f"comando-{cid}").start()
        return {"id": cid, "stream": True}

    def _run(self, text: str, context: Optional[Dict], cid: str) -> Dict:
        t0 = time.perf_counter()
        context = dict(context or {})
        self._etapa(cid, ETAPA["ler"])
        raw = str(text or "").strip()[:4000]
        from djorge.runtime.clean import clean
        try:
            text = clean(raw).text or raw
        except Exception:
            text = raw
        if "#" in raw and "&" in raw:                   # o atalho usa símbolos que a limpeza pode mexer
            text = raw
        agora = _now(self.app.cfg)
        hoje = agora.date()
        pref = self._prefs()
        agente = pref["agente"]
        regras = reconhecer(text, hoje, agora)              # datas, horas, repetição, e-mail, telefone: sempre regras
        atalho = atalho_envio(text) if "#" in text else None
        corpo = corpo_mensagem(text)
        eng = self.engine()
        aviso = None
        ui = self.app.ui.parse(text, hoje) if not context.get("tool") else None
        model_tools, can = None, None
        self._etapa(cid, ETAPA["entender"].format(agente=agente, quem=pref["usuario"] or "você"))
        if eng is not None and ui is None:
            md = self._model(eng, text)
            fields = combinar(text, md["trechos"], regras)
            model_tools, can = md["tools"], round(md["can"], 4)
            pr = md.get("p_regra")
            if pr is not None and model_tools:            # "descreve uma regra que acontece sempre que…?"
                model_tools = dict(model_tools)
                if pr >= 0.6 and P.condicional(text) or pr >= 0.85:
                    model_tools["regra"] = max(model_tools.get("regra", 0), pr)
                    if P.condicional(text):
                        model_tools["bilhete"] = model_tools.get("bilhete", 0) * 0.2
        else:
            fields = combinar(text, [], regras)
            if eng is None:
                aviso = FRASE["sem_modelo"]
        quando = resolver_quando(text, fields, agora)
        self._etapa_quando(cid, quando, hoje)
        for f in fields:                                    # o que o modelo (ou a regra) achou de nome
            if f.campo in ("entidade", "lugar", "evento ou compromisso", "aplicativo") and f.valor and f.fonte == "modelo":
                self._etapa(cid, ETAPA["achou"].format(x=F.nome(f.valor) if f.campo == "entidade" else f.valor), "ok")
        pis = P.pistas(text, fields, context)
        d_regras = P.decidir(None, pis, texto=text)          # o que as regras sozinhas diriam
        if ui is not None:                                  # comando de interface reconhecido: sempre regras
            dec, por = {"tool": "interface", "confianca": 0.99, "motivo": "comando de interface", "decidido": True,
                        "perguntar": [], "alternativas_uteis": []}, "regras"
        elif eng is None:
            dec, por = d_regras, "regras"
        else:
            dec_m = P.decidir_modelo(model_tools, pis, texto=text, can=can)
            forte = d_regras["decidido"] and d_regras["tool"] != "nenhuma"
            if pis.get("explicito"):                         # atalho #destino &texto&, conta pura, contexto: certeza maior
                dec, por = d_regras, "regras"
            elif pref["prioridade"] == "regras" and forte and d_regras["confianca"] >= 0.5:
                dec, por = d_regras, "regras"
            elif pref["prioridade"] == "equilibrado" and forte and d_regras["confianca"] >= 0.8 \
                    and d_regras["confianca"] > dec_m["confianca"]:
                dec, por = d_regras, "regras"
            else:
                dec, por = dec_m, dec_m.get("por", "modelo")
        tool = dec["tool"]
        out = {"id": cid, "text": text, "kind": "pedido", "tool": tool, "tool_p": dec["confianca"],
               "confianca": dec["confianca"], "motivo": dec["motivo"], "can_do_p": can, "model": eng is not None,
               "decidido_por": por, "prioridade": pref["prioridade"], "agente": agente, "aviso": aviso,
               "fields": [{"campo": f.campo, "texto": f.texto, "valor": f.valor, "p": round(f.p, 4), "fonte": f.fonte,
                           "tipo": (f.extra or {}).get("tipo"), "ini": f.ini, "fim": f.fim} for f in fields],
               "quando": quando, "corpo": corpo.valor if corpo else None, "atalho": atalho, "reply": "", "titulo": "",
               "dicas": [], "perguntas": [], "proposal": {"action": "none"}, "authorization": dict(SEGUIR),
               "needs_confirm": False, "results": [], "context": context}
        hl = pedido_limpar_historico(text, hoje) if ui is None and not context.get("tool") else None
        al = _alertas(text, agora) if hl is None and ui is None and not context.get("tool") and \
            tool not in ("cards", "listas", "calcular", "regra") else None
        if al:
            antes_foco = context.get("selected_card") and al["alertas"][0].get("antes") is not None
            if antes_foco or (al.get("card") and (not dec["decidido"] or tool in ("nenhuma", "buscar", "datas_horas")
                                                  or re.match(r"(?i)\s*me\s+(?:lembr|avis)", text))):                       # "me avisa de 2 em 2 horas…": é alerta, sem perguntar
                tool = out["tool"] = "cards"
                dec = dict(dec, tool="cards", decidido=True, perguntar=[])
        if hl is not None:
            out["tool"] = "historico"
            out["proposal"] = dict({"action": "history_clear"}, **hl)
            out["needs_confirm"] = True
            out["reply"] = ("Posso apagar o histórico de hoje?" if hl.get("hoje") else
                            f"Posso apagar o histórico anterior a {_data_pt(hl['antes_de'])}?" if hl.get("antes_de")
                            else "Posso apagar todo o histórico?")
        elif ui is not None:
            self._etapa(cid, ETAPA["entendeu"].format(intencao=ui["descricao"]))
            self._ui(out, ui)
        elif not dec["decidido"] and dec["perguntar"]:
            out["perguntas"] = dec["perguntar"]
            ops = [o.get("fala") or o["rotulo"].lower() for o in dec["perguntar"][0]["opcoes"]]
            out["reply"] = FRASE["perguntar"].format(opcoes=" ou ".join([", ".join(ops[:-1]), ops[-1]] if len(ops) > 1 else ops))
            out["tool"] = "perguntar"
        else:
            s_ = _sem_acento(text.casefold())
            if tool in ("buscar", "nenhuma") and self._mundo(pis):   # "quem foi Silvio Santos": a internet sabe
                tool = out["tool"] = "web"
            if tool == "cards":
                intencao = INTENCAO_CARD.get(self._tipo_card(s_, quando.get("hora"), quando.get("recorrencia"),
                                                             quando.get("prazo")), "criar um card")
            else:
                intencao = INTENCAO.get(tool)
            if intencao:
                self._etapa(cid, ETAPA["entendeu"].format(intencao=intencao), "ok")
            if tool not in ("nenhuma", "web"):
                self._etapa(cid, ETAPA["ferramenta"].format(agente=agente, nome=P.NOME.get(tool, tool)))
            if tool == "calcular":
                self._calc(out, text)
            elif tool == "datas_horas":
                self._time(out, text, fields, hoje)
            elif tool == "proximas_tarefas":
                self._agenda(out, text, fields, hoje)
            elif tool in ("cards", "listas"):
                self._card(out, text, fields, hoje, tool, eng, quando)
            elif tool == "bilhete":
                out["kind"] = "mensagem"
                self._msg(out, text, fields, atalho, corpo, eng, cid)
            elif tool == "buscar":
                self._search(out, text, fields, eng, cid)
            elif tool == "web":
                self._web(out, text, fields, cid)
            elif tool == "abrir":
                self._abrir(out, text, fields, corpo, cid)
            elif tool == "regra":
                if not self._automacao(out, text, eng):
                    if eng is None:
                        raise ModeloAusente("regra")
                    self._rule(out, text)
            else:
                out["reply"] = FRASE["nenhuma"]
                out["dicas"] = P.dicas(text, pis)
                if dec["alternativas_uteis"] and len(dec["alternativas_uteis"]) >= 2:
                    out["perguntas"] = [P.pergunta(dec["alternativas_uteis"])]
        # o aviso "(Sem o modelo…)" vai só no campo `aviso` (a UI mostra à parte); não entra no reply nem no histórico
        F.resposta(out, hoje)
        out["timing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        with self.lock:
            self.pending[out["id"]] = out
            while len(self.pending) > 200:
                self.pending.popitem(last=False)
        try:
            prop = out.get("proposal") or {}
            res0 = (out.get("results") or [None])[0]
            alvo = {"tipo": "card", "id": res0["id"]} if isinstance(res0, dict) and res0.get("id") and \
                prop.get("action") in ("create_card", "update_card") else (
                {"tipo": "card", "id": prop["id"]} if prop.get("id") else None)
            self.app.hist.add({"type": "command", "id": out["id"], "text": text[:200], "tool": out["tool"],
                               "action": prop.get("action"), "alvo": alvo,
                               "resultado": "executado" if out.get("executed") else
                               ("aguardando confirmação" if out.get("needs_confirm") else "respondido")})
        except Exception:
            pass
        return out

    def _etapa_quando(self, cid, quando: Dict, hoje):
        if not quando.get("achou"):
            return
        if quando.get("recorrencia_txt"):
            self._etapa(cid, ETAPA["repete"].format(rec=quando["recorrencia_txt"]), "ok")
        elif quando.get("inicio"):
            w = quando["inicio"]
            txt = F.intervalo(w, quando["termino"], hoje) if quando.get("fim") and quando.get("termino") else \
                F.data(w, hoje, relativo=False)
            self._etapa(cid, ETAPA["prazo" if quando.get("prazo") else "data"].format(data=txt), "ok")

    @staticmethod
    def _mundo(pis: Dict) -> bool:
        """Pergunta sobre o mundo ("quem foi…", "o que é…", "notícias…"): resposta está na internet, não nos dados."""
        return any(x["tool"] == "web" for x in pis.get("sinais") or [])

    @staticmethod
    def _consulta(text: str, fields) -> (str, bool):
        """O que buscar: a entidade que o MODELO extraiu (pessoa, empresa, lugar, evento) ou, sem ela, o pedido sem o
        verbo e sem a forma de pergunta. Devolve (consulta, veio_do_modelo)."""
        ents = [f for f in fields if f.fonte == "modelo" and f.valor and f.campo in
                ("entidade", "lugar", "evento ou compromisso", "aplicativo", "lista")]
        if ents:
            f = max(ents, key=lambda f: (f.p, f.fim - f.ini))
            v = f.valor.strip(" ,.!?:;\"'“”")
            return (F.nome(v) if f.campo == "entidade" else v), True
        q = _BUSCA_VERBO.sub("", text, count=1)
        sem = _PERGUNTA.sub("", q, count=1)
        pessoa = sem != q and re.match(r"(?i)\s*quem\b", q)
        q = re.sub(r"(?i)\b(?:na internet|na web|no google|na net|online|em sites?)\b", "", sem)
        q = " ".join(q.split()).strip(" ?.!,:;\"'“”") or " ".join(text.split()).strip(" ?.!")
        return (F.nome(q) if pessoa and len(q.split()) <= 5 else q), False

    # ------------------------------------------------------------------ ferramentas --
    def _calc(self, out, text):
        from djorge.runtime.tools import safe_math
        s = _sem_acento(text.casefold())
        for a, b in ((r"\bvezes\b", "*"), (r"\bmais\b", "+"), (r"\bmenos\b", "-"), (r"\bdividido por\b", "/"),
                     (r"\bsobre\b", "/"), (r"\bpor cento\b|\bporcento\b", "%"), (r"\belevado a\b", "^")):
            s = re.sub(a, b, s)
        cands = re.findall(r"[\d(][\d\s.,+\-*/x×÷^%()]*(?:\s*de\s*[\d(][\d\s.,+\-*/x×÷^%()]*)*", s)
        cands = sorted((c.strip() for c in cands if re.search(r"\d", c)), key=len, reverse=True)
        for c in cands:
            try:
                r = safe_math(c, "pt")
            except Exception:
                continue
            out["proposal"] = {"action": "calc", "expression": c, "value": r["value"]}
            out["results"] = [{"expression": c, "value": r["value"], "texto": _fmt_num(r["value"])}]
            out["reply"] = FRASE["calc"].format(expr=c, valor=_fmt_num(r["value"]))
            return
        out["proposal"] = {"action": "calc", "error": "expressão"}
        out["reply"] = FRASE["calc_erro"]
        out["dicas"] = ["Tente: “quanto é 18% de 2450”", "Tente: “1250 dividido por 3”"]

    def _time(self, out, text, fields, hoje):
        now = _now(self.app.cfg)
        s = _sem_acento(text.casefold())
        datas = [f for f in fields if f.campo == "data" and f.valor]
        res = {"now": now.isoformat(timespec="minutes"), "date": hoje.isoformat(), "weekday": SEMANA[hoje.weekday()]}
        if "que horas" in s or re.search(r"\bhoras? (sao|e)\b", s):
            out["reply"] = FRASE["hora"].format(hora=now.strftime("%H:%M"))
        elif datas and not (len(datas) == 1 and datas[0].valor == hoje.isoformat() and "quantos" not in s):
            d = _dt.date.fromisoformat(datas[0].valor)
            dias = (d - hoje).days
            res.update(target=d.isoformat(), days=dias, target_weekday=SEMANA[d.weekday()])
            txt = datas[0].texto.strip()
            txt = txt[:1].upper() + txt[1:]
            if "quant" in s or "falta" in s or "desde" in s:
                key = "dias_hoje" if dias == 0 else "dias_ate" if dias > 0 else "dias_desde"
                out["reply"] = FRASE[key].format(dias=abs(dias), data_txt=txt, data=_data_pt(d.isoformat()))
            else:
                out["reply"] = FRASE["data_de"].format(data_txt=txt, semana_de=("num " if d.weekday() >= 5 else "numa ") + SEMANA[d.weekday()],
                                                       data=_data_pt(d.isoformat()))
        else:
            out["reply"] = FRASE["data"].format(semana=SEMANA[hoje.weekday()], data=_data_pt(hoje.isoformat()))
        out["proposal"] = dict(res, action="time")
        out["results"] = [res]

    def _agenda(self, out, text, fields, hoje):
        s = _sem_acento(text.casefold())
        datas = sorted(f.valor for f in fields if f.campo == "data" and f.valor)
        if "semana" in s and not datas:
            frm, to, per = hoje, hoje + _dt.timedelta(6 - hoje.weekday()), "nesta semana"
        elif datas:
            frm = to = _dt.date.fromisoformat(datas[0])
            per = "hoje" if frm == hoje else "amanhã" if frm == hoje + _dt.timedelta(1) else f"em {_data_pt(datas[0])}"
            if len(datas) > 1:
                to = _dt.date.fromisoformat(datas[-1]); per = f"de {_data_pt(datas[0])} a {_data_pt(datas[-1])}"
        elif "hoje" in s:
            frm = to = hoje; per = "hoje"
        else:
            frm, to, per = hoje, hoje + _dt.timedelta(7), "nos próximos 7 dias"
        cards = [c for c in self.app.cards.agenda(frm.isoformat(), to.isoformat()) if not c["done"]]
        out["proposal"] = {"action": "show_agenda", "from": frm.isoformat(), "to": to.isoformat()}
        out["results"] = cards
        out["titulo"] = f"Agenda {per}"
        if not cards:
            out["reply"] = FRASE["agenda_vazia"].format(periodo=per)
        else:
            c = cards[0]
            out["reply"] = FRASE["agenda"].format(n=len(cards), itens="item" if len(cards) == 1 else "itens",
                                                  periodo=per, primeiro=f"{F.titulo(c['title'])}{_quando_pt(c)}")

    @staticmethod
    def _tipo_card(s: str, hora, rec=None, prazo=False) -> str:
        evento = re.search(r"\b(reuniao|consulta|encontro|jantar|call|aula|evento|compromisso|festa|aniversario|"
                           r"entrevista|voo|dentista|medic[oa]|exame|viagem|show|casamento)\b", s)
        if rec and not evento and not re.search(r"\b(anota|anote|nota|tarefa)\b", s):
            return "lembrete"
        if prazo and not rec and not re.search(r"\blembr", s):        # "entregar o relatório até sexta"
            return "tarefa"
        if not hora and evento and not re.search(r"\b(lembr|tarefa|pagar)", s):
            return "evento"
        if hora and (re.search(r"\b(reuniao|consulta|encontro|almoco|jantar|call|aula|evento|compromisso|festa|"
                               r"aniversario|entrevista|voo|dentista|medic[oa]|exame)\b", s) or not re.search(r"\blembr", s)):
            return "evento"
        if re.search(r"\blembr|\balarme\b|\bnao (me )?deixa esquecer\b", s):
            return "lembrete"
        if re.search(r"\b(anota|anote|nota)\b", s):
            return "nota"
        return "tarefa"

    @staticmethod
    def _datas_card(card: Dict, kind: str, q: Dict, hoje) -> Dict:
        """Põe início/fim/prazo no card conforme o tipo (evento/lembrete usam start; tarefa/nota usam due)."""
        inicio = q.get("inicio")
        for k in ("start", "end", "due"):
            card.pop(k, None)
        if not inicio:
            return card
        if kind in ("evento", "lembrete"):
            card["start"] = inicio
            card["all_day"] = len(inicio) == 10
            if kind == "evento" and len(inicio) > 10:
                card["end"] = q.get("termino") or (_dt.datetime.fromisoformat(inicio) + _dt.timedelta(hours=1)
                                                   ).isoformat(timespec="minutes")
        else:
            card["due"] = inicio
        if inicio[:10] == hoje.isoformat():
            card["column"] = "hoje"
        return card

    def _card(self, out, text, fields, hoje, tool, eng, quando):
        s = _sem_acento(text.casefold())
        get = lambda campo: [f for f in fields if f.campo == campo]
        people = []
        for f in fields:
            if f.campo == "entidade" and (f.extra or {}).get("tipo") in ("pessoa", None):
                people.append({"nome": F.nome(f.valor), "tipo": (f.extra or {}).get("tipo") or "pessoa", "email": None})
        for f in get("endereço de e-mail"):
            for p in people:
                if f.ini <= (next((x.ini for x in fields if x.campo == "entidade" and F.nome(x.valor) == p["nome"]), -1)) < f.fim:
                    p["email"] = f.valor
            if not any(p["email"] == f.valor for p in people):
                people.append({"nome": None, "tipo": "pessoa", "email": f.valor})
        if tool == "listas":
            kind = "lista"
            m = re.search(r"\blista\s+(?:de\s+|do\s+|da\s+)?([\wÀ-ÿ ]{2,40}?)(?=[:,.]|\s+(?:com|o|a|os|as)\b|$)", text, re.I)
            titulo = ("Lista de " + m.group(1).strip()) if m else "Lista"
            itens = []
            mi = re.search(r"(?::|\b(?:com|adiciona|adicione|coloca|coloque|bota|põe|poe|inclui|inclua)\b)\s*(.+)$", text, re.I)
            if mi:
                corpo = re.sub(r"\s+(?:n[ao]|à|a)\s+lista\b.*$", "", mi.group(1), flags=re.I)
                itens = [x.strip(" .") for x in re.split(r",|;|\be\b", corpo) if x.strip(" .")
                         and not re.match(r"(?i)^(uma\s+)?lista\b", x.strip())]
            exist = [c for c in self.app.cards.list(kind="lista") if c["title"].casefold() == titulo.casefold()]
            card = {"kind": "lista", "title": titulo, "checklist": [{"text": x, "done": False} for x in itens]}
            if exist:
                card = {"kind": "lista", "title": titulo,
                        "checklist": exist[0]["checklist"] + [x for x in card["checklist"]
                                                              if x["text"].casefold() not in {y["text"].casefold() for y in exist[0]["checklist"]}]}
            action = "update_card" if exist else "create_card"
        else:
            kind = self._tipo_card(s, quando.get("hora"), quando.get("recorrencia"), quando.get("prazo"))
            nomes = [p["nome"] for p in people if p.get("nome")] + [
                f.valor for f in fields if f.valor and (f.campo in ("lugar", "empresa ou estabelecimento", "pessoa")
                                                        or (f.campo == "entidade" and (f.extra or {}).get("tipo") != "pessoa"))]
            base = _sem_campos(text, fields)
            ev = [f for f in fields if f.campo == "evento ou compromisso" and f.fonte == "modelo" and f.valor]
            if ev and (len(ev[0].valor.split()) >= 2 or not base):     # o texto central vem do resolve do modelo
                base = _sem_campos(ev[0].valor, []) or ev[0].valor
                for nm in nomes[:1]:
                    if nm.casefold() not in base.casefold() and re.search(rf"(?i)\bcom\s+(?:o\s+|a\s+)?{re.escape(nm)}", text):
                        base = f"{base} com {nm}"
            titulo = F.titulo(base or text[:80], nomes)
            if not titulo:
                titulo = {"evento": "Compromisso", "lembrete": "Lembrete", "nota": "Nota"}.get(kind, "Tarefa")
            card = {"kind": kind, "title": titulo, "people": people, "recurrence": quando.get("recorrencia_txt") or "",
                    "priority": 3 if re.search(r"\burgent", s) else 2 if "importante" in s else 0}
            self._datas_card(card, kind, quando, hoje)
            sel = (out.get("context") or {}).get("selected_card")
            al = _alertas(text, _now(self.app.cfg))
            if al:
                spec = al["alertas"]
                if al.get("card"):                                  # alerta com hora própria: lembrete
                    card["kind"] = kind = "lembrete"
                    card["alertas"] = spec
                    if al["card"].get("start"):
                        card["start"] = card.get("start") or al["card"]["start"]
                    if card.get("title") in ("Tarefa", "Lembrete", "Compromisso", "Nota") and al.get("titulo"):
                        card["title"] = titulo = al["titulo"]
                elif sel:                                           # "avisa 30 min antes": no card em foco
                    card = {"alertas": spec}
                    out["proposal"] = {"action": "update_card", "card": card,
                                       "id": sel if isinstance(sel, str) else sel.get("id")}
            if card.get("alertas") is not None and "kind" not in card:
                action, exist = "update_card", [{"id": out["proposal"]["id"]}]
                kind = "lembrete"
            elif sel and re.search(r"\b(muda|mude|altera|altere|passa|passe|move|mova|remarca|remarque|adia|adie)\b", s):
                action = "update_card"
                card = {k: v for k, v in card.items() if k in ("start", "end", "due", "all_day", "priority") and v}
                exist = [{"id": sel if isinstance(sel, str) else sel.get("id")}]
            else:
                action, exist = "create_card", []
        # pedido do próprio usuário: sem portão de segurança
        out["authorization"] = dict(SEGUIR)
        out["needs_confirm"] = False
        out["proposal"] = {"action": action, "card": card}
        if quando.get("recorrencia"):
            out["proposal"]["recorrencia"] = quando["recorrencia"]
        if exist:
            out["proposal"]["id"] = exist[0]["id"]
        out["titulo"] = card.get("title") or titulo
        art, tipo = TIPO_PT[card.get("kind") or kind]
        res = self._execute(out, {})
        c = res.get("card") or card
        out["executed"] = True
        out["results"] = [c]
        if kind == "lista":
            n = len(c.get("checklist") or [])
            out["reply"] = FRASE["lista_criada"].format(titulo=c["title"], n=n, itens="item" if n == 1 else "itens")
        else:
            rec = FRASE["repete"].format(rec=c["recurrence"]) if c.get("recurrence") else ""
            out["reply"] = FRASE["card_criado" if action == "create_card" else "card_atualizado"].format(
                artigo=art, tipo=tipo, titulo=c.get("title") or titulo, quando=_quando_pt(c) + rec)
            if not quando.get("achou") and kind in ("tarefa", "evento", "lembrete"):
                out["dicas"] = [f"Tente: “{F.frase(titulo).lower()} amanhã às 9h”" if len(titulo) < 50 else
                                "Tente: “… amanhã às 9h”", "Tente: “… todo dia 20 às 9h”"]

    def _channels(self) -> List[Dict]:
        integ = getattr(self.app, "integrations", None)
        if integ is None:
            return []
        try:
            return [i for i in integ.list() if i.get("connected") and i.get("id") in ("google", "microsoft", "whatsapp")]
        except Exception:
            return []

    # ------------------------------------------------------------------ contatos --
    def contato(self, nome: Optional[str]) -> Optional[Dict]:
        """Acha {nome, telefone, email} pelo nome: primeiro nos contatos de T (djorge_local.contatos, se existir),
        depois nas pessoas dos cards."""
        if not nome:
            return None
        alvo = _fold(nome).strip()
        cands = []
        fonte = getattr(self.app, "contatos", None)
        if fonte is None:
            try:
                from . import contatos as mod                   # módulo de T (opcional)
                fonte = mod
            except Exception:
                fonte = None
        if fonte is not None:
            for nome_fn in ("buscar", "search", "listar", "list"):
                fn = getattr(fonte, nome_fn, None)
                if not callable(fn):
                    continue
                try:
                    try:
                        r = fn(nome)
                    except TypeError:
                        r = fn(self.app, nome)
                except Exception:
                    continue
                if isinstance(r, dict):
                    r = r.get("contatos") or r.get("results") or []
                for c in r or []:
                    if isinstance(c, dict):
                        cands.append({"nome": c.get("nome") or c.get("name"),
                                      "telefone": c.get("telefone") or c.get("phone"),
                                      "email": c.get("email")})
                break
        try:
            for c in self.app.cards.list(person=nome, limit=200):
                for p in c.get("people") or []:
                    cands.append({"nome": p.get("nome"), "telefone": p.get("telefone") or p.get("phone"),
                                  "email": p.get("email")})
        except Exception:
            pass
        best, bs = None, 0
        for c in cands:
            n = _fold(c.get("nome") or "")
            e = _fold(c.get("email") or "")
            sc = 3 if n == alvo else 2 if n.startswith(alvo) or alvo in n.split() else 1 if alvo in n or alvo in e else 0
            sc += 0.5 if c.get("telefone") else 0
            sc += 0.25 if c.get("email") else 0
            if sc > bs:
                best, bs = c, sc
        if best and bs >= 1:
            return {"nome": F.nome(best.get("nome")) if best.get("nome") else F.nome(nome),
                    "telefone": re.sub(r"\D", "", str(best.get("telefone") or "")) or None, "email": best.get("email")}
        return None

    def _msg(self, out, text, fields, atalho, corpo, eng, cid=None):
        from djorge.runtime.autorizacao import autorizar
        destino, nome = None, None
        if atalho:
            destino, nome = atalho.get("destino"), atalho.get("entidade")
            body = atalho.get("corpo")
        else:
            body = corpo.valor if corpo else None
            em = next((f for f in fields if f.campo == "endereço de e-mail"), None)
            tel = next((f for f in fields if f.campo == "telefone"), None)
            ent = next((f for f in fields if f.campo == "entidade"), None)
            destino = em.valor if em else tel.valor if tel else ent.valor if ent else None
            nome = ent.valor if ent else None
            if not destino:
                m = re.search(r"\b(?:pra|para|pro|ao|à)\s+(?:o\s+|a\s+)?([A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+)?)", text)
                if m:
                    destino = nome = m.group(1)
        if destino and "@" not in destino and not re.sub(r"\D", "", destino):
            self._etapa(cid, ETAPA["contato"].format(agente=self._prefs()["agente"], nome=F.nome(destino)))
            c = self.contato(destino)
            if c and (c.get("email") or c.get("telefone")):
                nome = c["nome"]
                destino = c.get("email") or c.get("telefone")
        if nome:
            nome = F.nome(nome)
        chans = self._channels()
        canal = None
        if destino:
            if "@" in destino:
                canal = next((c for c in chans if c["id"] in ("google", "microsoft")), None)
            elif re.sub(r"\D", "", destino) and len(re.sub(r"\D", "", destino)) >= 8:
                canal = next((c for c in chans if c["id"] == "whatsapp"), None)
            canal = canal or (chans[0] if chans else None)
        cname = {"google": "Gmail", "microsoft": "Outlook", "whatsapp": "WhatsApp"}.get((canal or {}).get("id"),
                                                                                        (canal or {}).get("name"))
        shown = destino if not nome or nome == destino else f"{nome} ({destino})"
        if body and not atalho:                          # no atalho #destino &texto& o texto vai como veio
            body = F.frase(body)
        out["proposal"] = {"action": "send_message", "to": destino, "name": nome, "body": body,
                           "channel": (canal or {}).get("id"), "channels": [c["id"] for c in chans],
                           "subject": None}
        out["titulo"] = f"Mensagem para {nome or destino}" if destino else "Mensagem"
        out["needs_confirm"] = True                      # quem envia é sempre a pessoa
        known = any(destino and destino.casefold() == (p.get("email") or "").casefold()
                    for c in self.app.cards.list(limit=500) for p in c["people"]) if destino else False
        # o texto é da própria pessoa: sem o modelo de segurança, só as regras do portão (vazio, dinheiro, contato novo)
        out["authorization"] = autorizar("enviar", {"texto": body or "", "destino": destino,
                                                    "contato_conhecido": known}, engine=None)
        if not destino:
            out["reply"] = FRASE["msg_sem_destino"]
            out["dicas"] = P.dicas(text, tool="bilhete")
        elif not body:
            out["reply"] = FRASE["msg_sem_corpo"].format(destino=shown)
            out["dicas"] = [f"Tente: “manda pra {nome or destino} dizendo que chego às 10h”"]
        elif not canal:
            out["reply"] = FRASE["msg_sem_canal"].format(destino=shown, corpo=body)
        else:
            out["reply"] = FRASE["msg_confirmar"].format(destino=shown, canal=cname, corpo=body)

    def _search(self, out, text, fields, eng, cid=None):
        q, do_modelo = self._consulta(text, fields)
        if not do_modelo:
            q = re.sub(r"(?i)^\s*(?:o\s+|a\s+|os\s+|as\s+)", "", q) or q
        srch = self.app.search
        r = srch.search(q, engine=eng, limit=10)               # só local: a internet é perguntada antes
        out["proposal"] = {"action": "search", "q": q}
        out["results"] = r["results"]
        out["titulo"] = f"Busca: {q}"
        n = len(r["results"])
        if n:
            out["reply"] = FRASE["busca"].format(n=n, itens="resultado" if n == 1 else "resultados", q=q)
            return
        out["reply"] = FRASE["busca_vazia"].format(q=q)
        out["dicas"] = [f"Tente: “pesquisa na internet {q}”", "Tente: “procura o boleto da escola”"]
        if q.strip():                                            # nada local: oferece a internet (com confirmação)
            out["tool"] = "web"
            self._web(out, text, fields, cid, q=q, depois_local=True)

    def _web(self, out, text, fields=(), cid=None, q: Optional[str] = None, depois_local: bool = False):
        """Não sai do computador sem perguntar: devolve a proposta web_search; a busca roda no confirm."""
        if q is None:
            q, _ = self._consulta(text, fields)
        out["proposal"] = {"action": "web_search", "q": q}
        out["results"] = []
        out["needs_confirm"] = True
        out["sai_do_computador"] = True
        out["titulo"] = f"Na internet: {q}"
        out["reply"] = FRASE["web_depois_local" if depois_local else "web_perguntar"].format(q=q)
        if not depois_local:
            out["dicas"] = []

    def _buscar_web(self, q: str, n: int = 6) -> Dict:
        from . import web
        r = web.buscar(q, n, transporte=self.web_transporte or getattr(self.app, "web_transporte", None))
        res = [dict(x, source="web", title=x["titulo"], snippet=x["trecho"], sai_do_computador=True)
               for x in r["resultados"]]
        out = {"ok": True, "q": q, "results": res, "sai_do_computador": True}
        if res:
            out["reply"] = FRASE["busca_web"].format(q=q)
        else:
            out["ok"] = not r.get("erro")
            out["reply"] = r.get("erro") or FRASE["web_vazia"].format(q=q)
            out["dicas"] = ["Tente palavras mais curtas, ex.: “previsão do tempo hoje”"]
        return out

    def _abrir(self, out, text, fields, corpo, cid=None):
        s = _sem_acento(text.casefold())
        url_f = next((f for f in fields if f.campo == "url"), None)
        em = next((f for f in fields if f.campo == "endereço de e-mail"), None)
        tel = next((f for f in fields if f.campo == "telefone"), None)
        if re.search(r"\b(whats(app)?|zap)\b", s):
            alvo = "whatsapp"
        elif em or re.search(r"\b(e-?mail|gmail|outlook)\b", s) and not url_f:
            alvo = "email"
        else:
            alvo = "navegador"
        # o texto pronto: "dizendo que ...", entre aspas, ou "com ..."
        texto = corpo.valor if corpo else None
        if not texto:
            m = re.search(r"(?i)\b(?:dizendo|avisando|falando|perguntando|contando)\s+(?:que|se)\s+(.+)$", text)
            texto = F.frase(m.group(1).strip().rstrip(".")) if m else None
        if not texto:
            m = re.search(r"[“\"']([^”\"']{1,500})[”\"']", text) or re.search(r"(?i)\b(?:com a mensagem|com o texto|escrito)\s*:?\s*(.+)$", text)
            texto = m.group(1).strip() if m else None
        nome = None
        m = re.search(r"\b(?:d[aoe]|pra|para|pro|com)\s+(?:o\s+|a\s+)?([A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+(?:d[aoe]s?\s+)?[A-ZÀ-Ý][\wÀ-ÿ]+)?)", text)
        if m and _fold(m.group(1)) not in ("whatsapp", "zap", "gmail", "outlook"):
            nome = m.group(1)
        contato = {"nome": F.nome(nome) if nome else None, "telefone": tel.valor if tel else None,
                   "email": em.valor if em else None}
        if nome and not (contato["telefone"] or contato["email"]):
            self._etapa(cid, ETAPA["contato"].format(agente=self._prefs()["agente"], nome=F.nome(nome)))
            c = self.contato(nome)
            if c:
                contato.update({k: v for k, v in c.items() if v})
        q = urllib.parse.quote(texto or "")
        if alvo == "whatsapp":
            fone = contato.get("telefone") or ""
            if fone and len(fone) <= 11:
                fone = "55" + fone
            url = f"https://wa.me/{fone}" + (f"?text={q}" if texto else "")
            app_url = "whatsapp://send?" + urllib.parse.urlencode({k: v for k, v in (("phone", fone), ("text", texto or "")) if v})
            nome_alvo = "o WhatsApp"
        elif alvo == "email":
            params = urllib.parse.urlencode({"body": texto}, quote_via=urllib.parse.quote) if texto else ""
            url = f"mailto:{contato.get('email') or ''}" + (f"?{params}" if params else "")
            app_url, nome_alvo = url, "o e-mail"
        else:
            if url_f:
                url = url_f.texto if url_f.texto.startswith("http") else "https://" + url_f.texto
            else:
                site = next((k for k in SITES if re.search(rf"\b{k}\b", s)), None)
                dom = re.search(r"\b([\w-]+\.(?:com|net|org|gov|edu|io|app|me)(?:\.br)?|[\w-]+\.br)\b", text)
                url = ("https://" + dom.group(1)) if dom else SITES.get(site or "navegador")
            app_url, nome_alvo = url, "o navegador"
        out["proposal"] = {"action": "abrir_app", "alvo": alvo, "url": url, "app_url": app_url, "texto": texto,
                           "contato": contato}
        out["needs_confirm"] = True
        out["titulo"] = f"Abrir {nome_alvo[2:]}"
        com = f" com {contato['nome']}" if contato.get("nome") else ""
        out["reply"] = FRASE["abrir"].format(alvo=nome_alvo, com=com, texto=f" e o texto “{texto}”" if texto else
                                             (f" em {url}" if alvo == "navegador" else ""))
        if alvo == "whatsapp" and contato.get("nome") and not contato.get("telefone"):
            out["dicas"] = [f"Não achei o telefone de {contato['nome']}: o WhatsApp vai abrir para você escolher o contato."]

    def _automacao(self, out, text, eng) -> bool:
        """Pedido condicional -> automação (automacoes.de_texto) proposta com resumo legível; salva ao confirmar."""
        sec = getattr(self.app, "secretario", None)
        if sec is None or not P.condicional(text):
            return False
        from .automacoes import compilar, de_texto
        marcar = None
        if eng is not None and getattr(self.app, "rules", None) is not None:
            marcar = lambda t: self.app.rules.marcar(t)
        try:
            r = de_texto(text, marcar=marcar)
            arvore = compilar(r["automacao"])
        except Exception:
            return False
        graph = None
        try:
            from . import treegraph
            graph = treegraph.tree_to_graph(arvore)
        except Exception:
            pass
        out["proposal"] = {"action": "create_automacao", "automacao": r["automacao"], "summary": r["resumo_curto"],
                           "resumo": r["resumo"], "notes": r["notas"], "tree": arvore, "graph": graph}
        out["needs_confirm"] = True
        out["authorization"] = {"decisao": "confirmar", "motivos": ["automação nova age sozinha no que chegar"], "p": None}
        out["reply"] = FRASE["automacao"].format(resumo=r["resumo_curto"])
        return True

    def _rule(self, out, text):
        r = self.app.rules.from_text(text)
        graph = None
        try:
            from . import treegraph
            graph = treegraph.tree_to_graph(r["tree"])
        except Exception:
            pass
        out["proposal"] = {"action": "create_tree", "tree": r["tree"], "summary": r["summary"], "graph": graph,
                           "questions": r.get("questions", []), "notes": r.get("notes", []),
                           "confidence": r.get("confidence")}
        out["needs_confirm"] = True
        out["authorization"] = {"decisao": "confirmar", "motivos": ["regra nova age sozinha no que chegar"], "p": None}
        out["reply"] = FRASE["regra"].format(resumo=r["summary"])

    def _ui(self, out, ui):
        out["proposal"] = {"action": "ui", "patch": ui["patch"], "descricao": ui["descricao"]}
        res = self._execute(out, {})
        out["executed"] = True
        out["results"] = [res.get("ui")]
        out["reply"] = FRASE["ui"].format(descricao=ui["descricao"])

    # ------------------------------------------------------------------ edições na confirmação --
    def _converter(self, rec: Dict, edits: Dict) -> Dict:
        """Aplica `edits` à proposta, podendo trocar o tipo (mensagem <-> card/tarefa/lembrete/evento), título,
        datas (aceita formatos humanos) e campos. Devolve a proposta nova (já formatada)."""
        p = dict(rec["proposal"])
        e = dict(edits or {})
        agora = _now(self.app.cfg)
        hoje = agora.date()
        acao = e.pop("action", None) or p.get("action")
        card_e = dict(e.pop("card", None) or {})
        kind = e.pop("kind", None) or e.pop("tipo", None) or card_e.get("kind")
        if kind == "mensagem":
            acao = "send_message"
        elif kind in KINDS and acao == "send_message":
            acao = "create_card"
        for k in ("title", "titulo"):
            if k in e:
                card_e["title"] = e.pop(k)
        quando = None
        for k in ("data", "quando", "hora"):
            if k in e:
                v = e.pop(k)
                if v:
                    quando = (quando + " " + str(v)) if quando else str(v)
        from .cards import FIELDS
        for k in list(e):
            if k in FIELDS and k not in ("id", "created", "updated") and acao != "send_message":
                card_e[k] = e.pop(k)
        if acao in ("create_card", "update_card"):
            if p.get("action") == "send_message":
                corpo = p.get("body") or ""
                pessoa = {"nome": p.get("name") or (p.get("to") if p.get("to") and "@" not in p["to"] else None),
                          "tipo": "pessoa", "email": p.get("to") if p.get("to") and "@" in p["to"] else None}
                base = {"kind": "tarefa", "title": (f"Falar com {pessoa['nome']}: {corpo}" if pessoa["nome"] else corpo)[:200]
                        or "Mensagem", "body": corpo, "people": [pessoa] if pessoa["nome"] or pessoa["email"] else []}
                if corpo:
                    q = resolver_quando(corpo, agora=agora)
                    if q["achou"]:
                        self._datas_card(base, "tarefa", q, hoje)
            else:
                base = dict(p.get("card") or {})
            base.pop("quando_fmt", None)
            antigo = base.get("kind") or "tarefa"
            novo = kind if kind in KINDS and kind != "mensagem" else antigo
            base["kind"] = novo
            if novo != antigo:                                   # start <-> due conforme o tipo
                w = base.get("start") or base.get("due")
                if w:
                    base = self._datas_card(base, novo, {"inicio": w, "termino": base.get("end")}, hoje)
            if quando:
                q = resolver_quando(quando, agora=agora)
                if not q["achou"]:
                    raise ValueError(f"não entendi a data “{quando}”. Tente “amanhã às 9h” ou “03/11/2026 14h”")
                base = self._datas_card(base, novo, q, hoje)
                if q.get("recorrencia_txt"):
                    base["recurrence"] = q["recorrencia_txt"]
            for k in ("start", "end", "due"):
                if k in card_e and card_e[k] not in (None, ""):
                    v = data_humana(card_e[k], agora)
                    if v is None:
                        raise ValueError(f"não entendi a data “{card_e[k]}”. Tente “amanhã às 9h” ou “03/11/2026 14h”")
                    card_e[k] = v
            base.update(card_e)
            novo_p = {"action": acao, "card": {k: v for k, v in F.card(base, hoje).items() if k != "quando_fmt"}}
            if p.get("id") and acao == "update_card":
                novo_p["id"] = p["id"]
            return novo_p
        if acao == "send_message":
            if p.get("action") == "send_message":
                q = dict(p)
            else:
                c = p.get("card") or {}
                pessoa = next((x for x in c.get("people") or [] if x.get("email") or x.get("nome")), {})
                q = {"action": "send_message", "to": pessoa.get("email") or pessoa.get("nome"),
                     "name": pessoa.get("nome"), "body": c.get("body") or c.get("title"), "subject": None}
                chans = self._channels()
                q["channels"] = [x["id"] for x in chans]
                q["channel"] = next((x["id"] for x in chans if ("@" in (q["to"] or "")) == (x["id"] != "whatsapp")),
                                    chans[0]["id"] if chans else None)
            for k in ("to", "body", "channel", "subject", "name", "template", "lang", "params"):
                if k in e:
                    q[k] = e.pop(k)
            if card_e.get("title") and "body" not in (edits or {}):
                q["body"] = card_e["title"]
            if q.get("body"):
                q["body"] = F.frase(q["body"])
            if q.get("name"):
                q["name"] = F.nome(q["name"])
            q["action"] = "send_message"
            return q
        q = dict(p)
        q.update(e)
        return q

    # ------------------------------------------------------------------ confirmação --
    def _execute(self, rec: Dict, edits: Dict) -> Dict:
        p = rec["proposal"]
        a = p.get("action")
        if a == "create_card":
            card = dict(p["card"], **(edits or {}))
            card.pop("quando_fmt", None)
            c = self.app.cards.create(card)
            return {"ok": True, "card": F.card(c)}
        if a == "update_card":
            card = dict(p["card"], **(edits or {}))
            card.pop("quando_fmt", None)
            c = self.app.cards.update(p["id"], card)
            return {"ok": True, "card": F.card(c)}
        if a == "history_clear":
            h = getattr(self.app, "hist", None) or getattr(self.app, "history", None)
            if p.get("hoje"):
                ids = [r.get("id") for r in list(h.items) if str(r.get("t", "")).startswith(p["hoje"])]
                n = h.clear(ids=ids) if ids else 0
            elif p.get("antes_de"):
                n = h.clear(antes_de=p["antes_de"])
            else:
                n = h.clear(tudo=True)
            return {"ok": True, "apagados": n,
                    "reply": "Apaguei " + (f"{n} registro" if n == 1 else f"{n} registros") + " do histórico."}
        if a == "ui":
            return {"ok": True, "ui": self.app.ui.set(dict(p["patch"], **(edits or {})))}
        if a == "send_message":
            q = dict(p, **{k: v for k, v in (edits or {}).items() if k in ("to", "body", "channel", "subject", "template", "lang", "params")})
            if not q.get("to") or not q.get("body"):
                raise ValueError("falta o destino ou o texto da mensagem")
            if not q.get("channel"):
                raise ValueError("nenhuma integração conectada para enviar")
            integ = self.app.service("integrations")
            kw = {k: q[k] for k in ("template", "lang", "params") if q.get(k)}
            r = integ.send(q["channel"], q["to"], q["body"], subject=q.get("subject"), **kw) or {}
            if r.get("error"):
                return {"ok": False, "sent": r, "error": r["error"], "aviso": r.get("aviso"),
                        "reply": r.get("aviso") or f"Não consegui enviar: {r['error']}."}
            return {"ok": bool(r.get("ok", True)), "sent": r,
                    "reply": FRASE["msg_enviada"].format(destino=F.nome(q.get("name")) if q.get("name") else q["to"])}
        if a == "abrir_app":
            q = dict(p, **{k: v for k, v in (edits or {}).items() if k in ("url", "texto", "alvo", "app_url", "contato")})
            if (edits or {}).get("texto") is not None and q.get("alvo") == "whatsapp":
                base = (q.get("url") or "https://wa.me/").split("?")[0]
                q["url"] = base + "?text=" + urllib.parse.quote(q["texto"])
            alvo = {"whatsapp": "o WhatsApp", "email": "o e-mail"}.get(q.get("alvo"), "o navegador")
            acts = getattr(self.app, "actions", None)
            fn = getattr(acts, "abrir_app", None)
            if callable(fn):                                   # T executa (abre o app local)
                r = fn(q) or {}
                return dict({"ok": True, "reply": FRASE["abrir_ok"].format(alvo=alvo)}, **r, abrir=q)
            return {"ok": True, "abrir": q, "url": q.get("url"), "reply": FRASE["abrir_ok"].format(alvo=alvo)}
        if a == "web_search":
            q = " ".join(str((edits or {}).get("q") or p.get("q") or "").split())
            if not q:
                raise ValueError("escreva o que buscar na internet")
            res = self._buscar_web(q)
            rec["results"] = res["results"]
            return res
        if a == "create_automacao":
            dados = dict(p["automacao"])
            ed = edits or {}
            if isinstance(ed.get("automacao"), dict):
                dados.update(ed["automacao"])
            salvo = self.app.secretario.automacoes.salvar(dados)
            return {"ok": True, "automacao": salvo, "reply": FRASE["automacao_criada"].format(nome=salvo["nome"])}
        if a == "create_tree":
            tree = (edits or {}).get("tree") or p["tree"]
            if (edits or {}).get("graph"):
                from . import treegraph
                tree = treegraph.graph_to_tree(edits["graph"])
            r = self.app.add_rule(tree, (edits or {}).get("answers"))
            return dict(r, ok=True, reply=FRASE["regra_criada"].format(nome=r["added"]))
        return {"ok": True}

    _EDIT_TIPO = {"action", "card", "kind", "tipo", "title", "titulo", "data", "quando", "hora"}

    def confirm(self, cid: str, approve: bool, edits: Optional[Dict] = None) -> Dict:
        with self.lock:
            rec = self.pending.get(cid)
        if not rec:
            raise KeyError(f"pedido não encontrado ou expirado: {cid}")
        if rec.get("done_result") is not None and not edits:
            return rec["done_result"]
        if approve and rec.get("executed") and not edits:
            res = {"ok": True, "already": True, "results": rec.get("results"), "reply": rec["reply"]}
            rec["done_result"] = res
            return res
        if not approve:
            res = {"ok": True, "cancelled": True, "reply": FRASE["cancelado"]}
            if rec.get("executed") and rec["proposal"].get("action") == "create_card" and rec.get("results"):
                try:
                    self.app.cards.delete(rec["results"][0]["id"])       # desfaz o card criado sozinho
                    res["undone"] = True
                except Exception:
                    pass
            rec["done_result"] = res
            return res
        if rec["authorization"].get("decisao") == "bloquear" and not (edits or {}).get("body"):
            raise PermissionError(FRASE["bloqueado"].format(motivos=", ".join(rec["authorization"]["motivos"])))
        edits = dict(edits or {})
        antes = rec["proposal"]
        criado = (rec.get("results") or [{}])[0].get("id") if rec.get("executed") and antes.get("action") in (
            "create_card", "update_card") else None
        if antes.get("action") in ("create_card", "update_card", "send_message") and edits:
            nova = self._converter(rec, edits)
            if criado and nova.get("action") == "create_card":            # o card já existe: atualiza no lugar
                nova = {"action": "update_card", "card": nova["card"], "id": criado}
            rec["proposal"] = nova
            res = self._execute(rec, {})
            if criado and nova.get("action") == "send_message" and res.get("ok"):
                try:
                    self.app.cards.delete(criado)                         # virou mensagem: some o card
                    res["undone"] = True
                except Exception:
                    pass
            res["proposal"] = nova
            if nova.get("action") in ("create_card", "update_card") and res.get("card"):
                c = res["card"]
                art, tipo = TIPO_PT.get(c.get("kind") or "tarefa", ("o", "card"))
                key = "card_criado" if nova["action"] == "create_card" else "card_atualizado"
                rec_txt = FRASE["repete"].format(rec=c["recurrence"]) if c.get("recurrence") else ""
                res["reply"] = FRASE[key].format(artigo=art, tipo=tipo, titulo=c.get("title"),
                                                 quando=_quando_pt(c) + rec_txt)
                rec["results"], rec["executed"] = [c], True
        else:
            res = self._execute(rec, edits)
        res.setdefault("reply", "Feito.")
        res["reply"] = F.frase(res["reply"])
        rec["done_result"] = res
        return res

    # ------------------------------------------------------------------ triagem --
    def triage(self, text: str, remetente=None, assunto=None) -> Dict:
        from djorge.runtime.triagem import triar
        eng = self.engine()
        r = triar(str(text or "")[:8000], remetente=remetente, assunto=assunto, engine=eng,
                  hoje=_now(self.app.cfg).date())
        r["model"] = eng is not None
        try:
            from .secretario import intencao
            r["intencao"] = intencao({}, self._prefs()["agente"], r, _now(self.app.cfg).date())
        except Exception:
            pass
        return r
