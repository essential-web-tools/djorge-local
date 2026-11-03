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
    "busca_vazia": "Não achei nada para “{q}”.",
    "busca": "Achei {n} {itens} para “{q}”.",
    "busca_web": "Achei {n} {itens} na internet para “{q}”.",
    "web_vazia": "Não achei nada na internet para “{q}”.",
    "abrir": "Abrir {alvo}{com}{texto}?",
    "abrir_ok": "Abrindo {alvo}.",
    "regra": "Montei a regra: {resumo} Confira no editor antes de ativar.",
    "regra_criada": "Regra “{nome}” ativada.",
    "ui": "Pronto: {descricao}.",
    "nenhuma": "Não entendi bem o que fazer com isso. Posso criar cards e lembretes, montar listas, mandar mensagens, "
               "buscar, fazer contas, ver datas e montar regras.",
    "perguntar": "Quer que eu {opcoes}?",
    "cancelado": "Tudo bem, não fiz nada.",
    "bloqueado": "Não vou fazer isso: {motivos}.",
}
TIPO_PT = {"tarefa": ("a", "tarefa"), "evento": ("o", "evento"), "lembrete": ("o", "lembrete"),
           "nota": ("a", "nota"), "lista": ("a", "lista"), "mensagem": ("a", "mensagem")}
ETAPA = {"ler": "Lendo o pedido…", "datas": "Achando data e hora…", "modelo": "Entendendo o pedido com o modelo…",
         "escolher": "Escolhendo a ferramenta…", "web": "Buscando na web…", "contato": "Procurando o contato…",
         "pronto": "Pronto"}
SITES = {"youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
         "outlook": "https://outlook.live.com", "maps": "https://maps.google.com", "mapa": "https://maps.google.com",
         "spotify": "https://open.spotify.com", "navegador": "https://www.google.com", "chrome": "https://www.google.com",
         "edge": "https://www.bing.com", "firefox": "https://www.google.com"}
SEGUIR = {"decisao": "seguir", "motivos": [], "p": None}


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


PREFIXO = re.compile(r"^\s*(?:(?:por favor|pf|ei|djorge)[,\s]+)*(?:(?:me\s+)?(?:lembra|lembre|lembrar)(?:-me)?\s+(?:de\s+|que\s+)?|"
                     r"(?:marca|marque|marcar|agenda|agende|agendar|anota|anote|anotar|cria|crie|criar|adiciona|"
                     r"adicione|coloca|coloque|bota|poe|põe)\s+(?:um[a]?\s+|o\s+|a\s+)?(?:(?:card|tarefa|lembrete|"
                     r"evento|nota|compromisso|alarme)\s+(?:de\s+|para\s+|pra\s+)?)?|(?:preciso|tenho que)\s+)", re.I)
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
    t = re.sub(r"(?i)^\s*(?:um[a]?\s+)?(?:lembrete|tarefa|evento|nota|card|compromisso|alarme)\s+(?:de\s+|para\s+|pra\s+)?(?=\w)", "", t)
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

    def _model(self, eng, text: str) -> Dict:
        from djorge.runtime.semantic import SemReq
        names = list(TOOLS)
        reqs = [SemReq("NOUL", text, options=[CAN["pt"]]),
                SemReq("CHOICE", text, options=[TOOL_PT[t] for t in names], descriptions=[TOOLS[t] for t in names],
                       ask=ASK_TOOL["pt"])]
        reqs += [SemReq("resolve", text, mode="ENTITY", target=c.lower(), threshold=0.5) for c in CAMPOS]
        out = eng.run(reqs)
        can = float(out[0].get("p_true", out[0].get("probability", 0)))
        back = {TOOL_PT[t]: t for t in names}
        dist = out[1].get("distribution") or {}
        tools = {back[k]: round(float(v), 4) for k, v in dist.items() if k in back}
        trechos = []
        for campo, ms in zip(CAMPOS, out[2:]):
            for m in getattr(ms, "matches", []) or []:
                trechos.append(Trecho(m.start, m.end, campo, text[m.start:m.end], valor=text[m.start:m.end].strip(),
                                      p=float(m.probability), fonte="modelo"))
        return {"can": can, "tools": tools, "trechos": trechos}

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
        regras = reconhecer(text, hoje, agora)
        if any(r.campo in ("data", "hora", "período do dia", "frequência ou repetição", "duração") for r in regras):
            self._etapa(cid, ETAPA["datas"])
        atalho = atalho_envio(text) if "#" in text else None
        corpo = corpo_mensagem(text)
        eng = self.engine()
        aviso = None
        ui = self.app.ui.parse(text, hoje) if not context.get("tool") else None
        model_tools, can = None, None
        if eng is not None:
            self._etapa(cid, ETAPA["modelo"])
            md = self._model(eng, text)
            fields = combinar(text, md["trechos"], regras)
            model_tools, can = md["tools"], round(md["can"], 4)
            if ui is not None:
                from djorge.runtime.semantic import SemReq
                r = eng.run([SemReq("NOUL", text, options=["Este pedido muda a aparência ou as preferências do app."])])[0]
                if float(r.get("p_true", r.get("probability", 0))) < 0.35:
                    ui = None
        else:
            fields = combinar(text, [], regras)
            aviso = FRASE["sem_modelo"]
        self._etapa(cid, ETAPA["escolher"])
        pis = P.pistas(text, fields, context)
        dec = P.decidir(model_tools, pis, texto=text)
        tool = dec["tool"]
        quando = resolver_quando(text, fields, agora)
        out = {"id": cid, "text": text, "kind": "pedido", "tool": tool, "tool_p": dec["confianca"],
               "confianca": dec["confianca"], "motivo": dec["motivo"], "can_do_p": can, "model": eng is not None,
               "aviso": aviso,
               "fields": [{"campo": f.campo, "texto": f.texto, "valor": f.valor, "p": round(f.p, 4), "fonte": f.fonte,
                           "tipo": (f.extra or {}).get("tipo"), "ini": f.ini, "fim": f.fim} for f in fields],
               "quando": quando, "corpo": corpo.valor if corpo else None, "atalho": atalho, "reply": "", "titulo": "",
               "dicas": [], "perguntas": [], "proposal": {"action": "none"}, "authorization": dict(SEGUIR),
               "needs_confirm": False, "results": [], "context": context}
        if ui is not None:
            out["tool"] = "interface"
            self._etapa(cid, "Mudando a aparência…")
            self._ui(out, ui)
        elif not dec["decidido"] and dec["perguntar"]:
            out["perguntas"] = dec["perguntar"]
            ops = [o["rotulo"].lower() for o in dec["perguntar"][0]["opcoes"]]
            out["reply"] = FRASE["perguntar"].format(opcoes=" ou ".join([", ".join(ops[:-1]), ops[-1]] if len(ops) > 1 else ops))
            out["tool"] = "perguntar"
        else:
            self._etapa(cid, ETAPA["web"] if tool == "web" else f"Usando a ferramenta {P.NOME.get(tool, tool)}…")
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
                self._web(out, text)
            elif tool == "abrir":
                self._abrir(out, text, fields, corpo, cid)
            elif tool == "regra":
                if eng is None:
                    raise ModeloAusente("regra")
                self._rule(out, text)
            else:
                out["reply"] = FRASE["nenhuma"]
                out["dicas"] = P.dicas(text, pis)
                if dec["alternativas_uteis"] and len(dec["alternativas_uteis"]) >= 2:
                    out["perguntas"] = [P.pergunta(dec["alternativas_uteis"])]
        if aviso and out["reply"]:
            out["reply"] = out["reply"] + " " + aviso
        F.resposta(out, hoje)
        out["timing_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        with self.lock:
            self.pending[out["id"]] = out
            while len(self.pending) > 200:
                self.pending.popitem(last=False)
        try:
            self.app.hist.add({"type": "command", "text": text[:500], "tool": out["tool"], "reply": out["reply"],
                               "action": out["proposal"].get("action")})
        except Exception:
            pass
        return out

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
    def _tipo_card(s: str, hora, rec=None) -> str:
        evento = re.search(r"\b(reuniao|consulta|encontro|jantar|call|aula|evento|compromisso|festa|aniversario|"
                           r"entrevista|voo|dentista|medic[oa]|exame|viagem|show|casamento)\b", s)
        if rec and not evento and not re.search(r"\b(anota|anote|nota|tarefa)\b", s):
            return "lembrete"
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
            kind = self._tipo_card(s, quando.get("hora"), quando.get("recorrencia"))
            titulo = F.titulo(_sem_campos(text, fields) or text[:80], [p["nome"] for p in people if p.get("nome")])
            if not titulo:
                titulo = {"evento": "Compromisso", "lembrete": "Lembrete", "nota": "Nota"}.get(kind, "Tarefa")
            card = {"kind": kind, "title": titulo, "people": people, "recurrence": quando.get("recorrencia_txt") or "",
                    "priority": 3 if re.search(r"\burgent", s) else 2 if "importante" in s else 0}
            self._datas_card(card, kind, quando, hoje)
            sel = (out.get("context") or {}).get("selected_card")
            if sel and re.search(r"\b(muda|mude|altera|altere|passa|passe|move|mova|remarca|remarque|adia|adie)\b", s):
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
            self._etapa(cid, ETAPA["contato"])
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
        q = re.sub(r"(?i)^\s*(?:me\s+)?(?:procura|procure|busca|busque|acha|ache|encontra|encontre|pesquisa|pesquise)"
                   r"(?:r)?\s+(?:por\s+|pelo\s+|pela\s+|o\s+|a\s+|os\s+|as\s+)?", "", text).strip(" ?.!") or text
        srch = self.app.search
        if self.web_transporte is not None and getattr(srch, "web_transporte", None) is None:
            srch.web_transporte = self.web_transporte
        r = srch.search(q, engine=eng, limit=10, web="auto")
        out["proposal"] = {"action": "search", "q": q}
        out["results"] = r["results"]
        out["titulo"] = f"Busca: {q}"
        n = len(r["results"])
        if r.get("web"):
            out["sai_do_computador"] = True
            out["proposal"]["web"] = True
            self._etapa(cid, ETAPA["web"])
        out["reply"] = (FRASE["busca_web" if r.get("web") else "busca"].format(
            n=n, itens="resultado" if n == 1 else "resultados", q=q) if n else FRASE["busca_vazia"].format(q=q))
        if not n:
            out["dicas"] = [f"Tente: “pesquisa na internet {q}”", "Tente: “procura o boleto da escola”"]

    def _web(self, out, text):
        from . import web
        q = re.sub(r"(?i)\b(?:me\s+)?(?:pesquis\w*|busc\w*|procur\w*|v[eê]|veja|olha|olhe)\b\s*(?:por\s+|sobre\s+)?", "", text)
        q = re.sub(r"(?i)\b(?:na internet|na web|no google|na net|online|em sites?)\b", "", q)
        q = " ".join(q.split()).strip(" ?.!,:") or text
        r = web.buscar(q, 6, transporte=self.web_transporte or getattr(self.app, "web_transporte", None))
        res = [dict(x, source="web", title=x["titulo"], snippet=x["trecho"]) for x in r["resultados"]]
        out["proposal"] = {"action": "search", "q": q, "web": True}
        out["results"] = res
        out["sai_do_computador"] = True
        out["titulo"] = f"Na internet: {q}"
        n = len(res)
        if n:
            out["reply"] = FRASE["busca_web"].format(n=n, itens="resultado" if n == 1 else "resultados", q=q)
        else:
            out["reply"] = r.get("erro") or FRASE["web_vazia"].format(q=q)
            out["dicas"] = [f"Tente: “procura {q}” (nos seus dados)", "Tente palavras mais curtas, ex.: “previsão do tempo hoje”"]

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
            self._etapa(cid, ETAPA["contato"])
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
        return r
