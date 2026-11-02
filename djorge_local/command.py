"""O card de entrada estilo IA: texto -> ferramenta do app -> proposta -> (confirmação) -> execução.

    limpeza (clean) -> regras (reconhecer, atalho_envio, corpo_mensagem)
    -> DOFL, quando carregado (NOUL "o app consegue", CHOICE das 9 ferramentas, resolve dos campos do app)
    -> combinar -> proposta por ferramenta -> autorizar -> resposta curta por MODELOS DE FRASE.

O modelo só decide; toda frase mostrada sai das tabelas FRASE abaixo. Sem modelo, palavras-chave escolhem a
ferramenta e a resposta avisa disso; o que só o modelo faz (regra automática) levanta ModeloAusente (HTTP 409).
"""
from __future__ import annotations

import collections
import datetime as _dt
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

from djorge.runtime.recognizers import (Trecho, _sem_acento, atalho_envio, combinar, corpo_mensagem, reconhecer)
from djorge.training.flash_data import ASK_TOOL, CAN, TOOL_PT, TOOLS

CAMPOS = ["data", "hora", "período do dia", "pessoa", "evento ou compromisso", "lugar", "lista",
          "endereço de e-mail", "empresa ou estabelecimento", "frequência ou repetição"]
SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]

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
    "lista_criada": "Criei a lista “{titulo}” com {n} {itens}.",
    "msg_confirmar": "Enviar para {destino} pelo {canal}: “{corpo}”?",
    "msg_sem_canal": "Mensagem para {destino}: “{corpo}”. Conecte o Gmail, o Outlook ou o WhatsApp para enviar.",
    "msg_sem_destino": "Para quem devo mandar? Use, por exemplo, #ana@email.com &texto&.",
    "msg_sem_corpo": "O que devo dizer para {destino}?",
    "msg_enviada": "Mensagem enviada para {destino}.",
    "busca_vazia": "Não achei nada para “{q}”.",
    "busca": "Achei {n} {itens} para “{q}”.",
    "regra": "Montei a regra: {resumo} Confira no editor antes de ativar.",
    "regra_criada": "Regra “{nome}” ativada.",
    "ui": "Pronto: {descricao}.",
    "nenhuma": "Isso eu não consigo fazer por aqui. Posso criar cards, lembretes, listas, mandar mensagens, "
               "buscar, fazer contas, ver datas e montar regras.",
    "cancelado": "Tudo bem, não fiz nada.",
    "bloqueado": "Não vou fazer isso: {motivos}.",
}
TIPO_PT = {"tarefa": ("a", "tarefa"), "evento": ("o", "evento"), "lembrete": ("o", "lembrete"),
           "nota": ("a", "nota"), "lista": ("a", "lista"), "mensagem": ("a", "mensagem")}


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
    d = _dt.date.fromisoformat(w[:10])
    hoje = _dt.date.today()
    dia = "hoje" if d == hoje else "amanhã" if d == hoje + _dt.timedelta(1) else \
        f"{SEMANA[d.weekday()].split('-')[0]}, {_data_pt(w)}"
    hora = f" às {w[11:16]}" if len(w) > 10 else ""
    return f" para {dia}{hora}"


def _now(cfg):
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo(cfg.get("timezone") or "America/Sao_Paulo")).replace(tzinfo=None)
    except Exception:
        return _dt.datetime.now()


# ------------------------------------------------------------------ palavras-chave (sem modelo) --
KW = [
    ("regra", r"\b(sempre que|toda vez que|quando chegar|quando receber|regra|automaticamente|faca com que|faz com que)\b"),
    ("datas_horas", r"\b(que horas|que dia|qual (a|e a) data|data de hoje|dia da semana|quantos dias|faltam? quantos|"
                    r"que dia (cai|e)|hoje e que dia|em que dia)\b"),
    ("calcular", r"\b(quanto e|quanto da|quanto fica|calcula|calcule|soma|some|multiplica|divide|porcento|"
                 r"por cento)\b.*\d|^[\d\s.,()+\-*/x×÷^%=?]+$|\d\s*[-+*x×÷^]\s*\d|\d\s*%\s*(de|do|da)\s*\d"),
    ("bilhete", r"(^|\s)#\S|\b(manda|mande|envia|envie|avisa|avise|escreve|escreva|responde|responda)\b.*"
                r"\b(pra|para|pro|ao|a|à)\b|\b(mensagem|e-?mail|zap|whatsapp)\s+(pra|para|pro)\b"),
    ("proximas_tarefas", r"\b(o que (eu )?tenho|minha agenda|meus compromissos|proxim[oa]s? (compromisso|tarefa|evento|"
                         r"reuniao|lembrete)s?|o que vem|tenho algo|tenho alguma|como esta (a|minha) (agenda|semana|dia))\b"),
    ("buscar", r"\b(procura|procure|busca|busque|acha|ache|encontra|encontre|pesquisa|pesquise|onde (esta|ta|guardei))\b"),
    ("listas", r"\blistas?\b"),
    ("cards", r"\b(lembra|lembre|lembrete|marca|marque|agenda|agende|reuniao|tarefa|compromisso|anota|anote|card|"
              r"alarme|consulta|evento|criar?|crie|adiciona|adicione|coloca|coloque|preciso)\b"),
]
_KW = [(t, re.compile(rx)) for t, rx in KW]
PREFIXO = re.compile(r"^\s*(?:(?:por favor|pf|ei|djorge)[,\s]+)*(?:(?:me\s+)?(?:lembra|lembre|lembrar)(?:-me)?\s+(?:de\s+|que\s+)?|"
                     r"(?:marca|marque|marcar|agenda|agende|agendar|anota|anote|anotar|cria|crie|criar|adiciona|"
                     r"adicione|coloca|coloque|bota|poe|põe)\s+(?:um[a]?\s+|o\s+|a\s+)?(?:(?:card|tarefa|lembrete|"
                     r"evento|nota|compromisso|alarme)\s+(?:de\s+|para\s+|pra\s+)?)?|(?:preciso|tenho que)\s+)", re.I)
BORDAS = {"às", "as", "a", "à", "de", "do", "da", "no", "na", "em", "para", "pra", "pro", "dia", "e", "o", "que",
          "com", "até", "ate", "hoje", "por", "volta", "lá", "la", "pelas", "das", "dos"}


def keyword_tool(text: str) -> (str, float):
    s = _sem_acento(text.casefold())
    for t, rx in _KW:
        if rx.search(s):
            return t, 0.6
    if any(x.campo in ("data", "hora") for x in reconhecer(text)):
        return "cards", 0.5
    return "nenhuma", 0.4


def _sem_campos(text: str, fields: List[Trecho], drop=("data", "hora", "período do dia", "frequência ou repetição",
                                                       "endereço de e-mail", "link de reunião")) -> str:
    keep = list(text)
    for f in fields:
        if f.campo in drop:
            for i in range(f.ini, min(f.fim, len(keep))):
                keep[i] = " "
    t = PREFIXO.sub("", "".join(keep))
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
        tools = sorted(({"tool": back[k], "p": round(float(v), 4)} for k, v in dist.items() if k in back),
                       key=lambda x: -x["p"])
        trechos = []
        for campo, ms in zip(CAMPOS, out[2:]):
            for m in getattr(ms, "matches", []) or []:
                trechos.append(Trecho(m.start, m.end, campo, text[m.start:m.end], valor=text[m.start:m.end].strip(),
                                      p=float(m.probability), fonte="modelo"))
        return {"can": can, "tools": tools, "trechos": trechos}

    # ------------------------------------------------------------------ comando --
    def run(self, text: str, context: Optional[Dict] = None) -> Dict:
        t0 = time.perf_counter()
        raw = str(text or "").strip()[:4000]
        if not raw:
            raise ValueError("escreva um pedido")
        from djorge.runtime.clean import clean
        try:
            text = clean(raw).text or raw
        except Exception:
            text = raw
        if "#" in raw and "&" in raw:                   # o atalho usa símbolos que a limpeza pode mexer
            text = raw
        hoje = _now(self.app.cfg).date()
        regras = reconhecer(text, hoje)
        atalho = atalho_envio(text) if "#" in text else None
        corpo = corpo_mensagem(text)
        eng = self.engine()
        aviso = None
        ui = self.app.ui.parse(text, hoje)
        if eng is not None:
            md = self._model(eng, text)
            fields = combinar(text, md["trechos"], regras)
            tools, can = md["tools"], round(md["can"], 4)
            tool, tool_p = (tools[0]["tool"], tools[0]["p"]) if tools else ("nenhuma", 0.0)
            if ui is not None:
                from djorge.runtime.semantic import SemReq
                r = eng.run([SemReq("NOUL", text, options=["Este pedido muda a aparência ou as preferências do app."])])[0]
                if float(r.get("p_true", r.get("probability", 0))) < 0.35:
                    ui = None
        else:
            fields = combinar(text, [], regras)
            tool, tool_p = keyword_tool(text)
            tools, can = [{"tool": tool, "p": tool_p}], None
            aviso = FRASE["sem_modelo"]
        if atalho and atalho.get("destino"):
            tool, tool_p = "bilhete", 1.0
            tools = [{"tool": "bilhete", "p": 1.0}] + [t for t in tools if t["tool"] != "bilhete"]
        out = {"id": uuid.uuid4().hex[:12], "text": text, "kind": "pedido", "tool": tool, "tool_p": tool_p,
               "tools": tools, "can_do_p": can, "model": eng is not None, "aviso": aviso,
               "fields": [{"campo": f.campo, "texto": f.texto, "valor": f.valor, "p": round(f.p, 4), "fonte": f.fonte,
                           "tipo": (f.extra or {}).get("tipo"), "ini": f.ini, "fim": f.fim} for f in fields],
               "corpo": corpo.valor if corpo else None, "atalho": atalho, "reply": "",
               "proposal": {"action": "none"}, "authorization": {"decisao": "seguir", "motivos": [], "p": None},
               "needs_confirm": False, "results": [], "context": context or {}}
        if ui is not None:
            out["tool"] = "interface"
            self._ui(out, ui)
        elif tool == "calcular":
            self._calc(out, text)
        elif tool == "datas_horas":
            self._time(out, text, fields, hoje)
        elif tool == "proximas_tarefas":
            self._agenda(out, text, fields, hoje)
        elif tool in ("cards", "listas"):
            self._card(out, text, fields, hoje, tool, eng)
        elif tool == "bilhete":
            out["kind"] = "mensagem"
            self._msg(out, text, fields, atalho, corpo, eng)
        elif tool == "buscar":
            self._search(out, text, fields, eng)
        elif tool == "regra":
            if eng is None:
                raise ModeloAusente("regra")
            self._rule(out, text)
        else:
            out["reply"] = FRASE["nenhuma"]
        if aviso and out["reply"]:
            out["reply"] = out["reply"] + " " + aviso
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
                out["reply"] = FRASE["data_de"].format(data_txt=txt, semana_de="num " + SEMANA[d.weekday()],
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
        if not cards:
            out["reply"] = FRASE["agenda_vazia"].format(periodo=per)
        else:
            c = cards[0]
            out["reply"] = FRASE["agenda"].format(n=len(cards), itens="item" if len(cards) == 1 else "itens",
                                                  periodo=per, primeiro=f"{c['title']}{_quando_pt(c)}")

    def _card(self, out, text, fields, hoje, tool, eng):
        from djorge.runtime.autorizacao import autorizar
        s = _sem_acento(text.casefold())
        get = lambda campo: [f for f in fields if f.campo == campo]
        data = next((f.valor for f in get("data") if f.valor), None)
        hora = next((f.valor for f in get("hora") if f.valor), None)
        per = get("período do dia")
        if not hora and per:
            hora = {"manha": "09:00", "tarde": "14:00", "noite": "19:00", "madrugada": "06:00", "cedinho": "07:00",
                    "almoco": "12:00"}.get(next((k for k in ("manha", "tarde", "noite", "madrugada", "cedinho", "almoco")
                                                 if k in _sem_acento(per[0].texto.casefold())), ""), None)
        if hora and not data:
            now = _now(self.app.cfg)
            data = hoje.isoformat() if hora > now.strftime("%H:%M") else (hoje + _dt.timedelta(1)).isoformat()
        freq = next((f.texto for f in get("frequência ou repetição")), "")
        people = []
        for f in fields:
            if f.campo == "entidade" and (f.extra or {}).get("tipo") in ("pessoa", None):
                people.append({"nome": f.valor, "tipo": (f.extra or {}).get("tipo") or "pessoa", "email": None})
        for f in get("endereço de e-mail"):
            for p in people:
                if f.ini <= (next((x.ini for x in fields if x.campo == "entidade" and x.valor == p["nome"]), -1)) < f.fim:
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
            if hora and (re.search(r"\b(reuniao|consulta|encontro|almoco|jantar|call|aula|evento|compromisso|festa|"
                                   r"aniversario|entrevista|voo)\b", s) or not re.search(r"\blembr", s)):
                kind = "evento"
            elif re.search(r"\blembr|\balarme\b", s):
                kind = "lembrete"
            elif re.search(r"\b(anota|anote|nota)\b", s):
                kind = "nota"
            else:
                kind = "tarefa"
            titulo = _sem_campos(text, fields) or text[:80]
            card = {"kind": kind, "title": titulo, "people": people, "recurrence": freq,
                    "priority": 3 if re.search(r"\burgent", s) else 2 if "importante" in s else 0}
            if data:
                if kind in ("evento", "lembrete"):
                    card["start"] = f"{data}T{hora}" if hora else data
                    card["all_day"] = not hora
                    if kind == "evento" and hora:
                        h, mi_ = map(int, hora.split(":"))
                        e = _dt.datetime.fromisoformat(f"{data}T{hora}") + _dt.timedelta(hours=1)
                        card["end"] = e.isoformat(timespec="minutes")
                else:
                    card["due"] = f"{data}T{hora}" if hora else data
                if data == hoje.isoformat():
                    card["column"] = "hoje"
            sel = (out.get("context") or {}).get("selected_card")
            if sel and re.search(r"\b(muda|mude|altera|altere|passa|passe|move|mova|remarca|remarque|adia|adie)\b", s):
                action = "update_card"
                card = {k: v for k, v in card.items() if k in ("start", "end", "due", "all_day", "priority") and v}
                exist = [{"id": sel if isinstance(sel, str) else sel.get("id")}]
            else:
                action, exist = "create_card", []
        auth = autorizar("criar", {"texto": text}, engine=eng)
        out["authorization"] = auth
        out["needs_confirm"] = auth["decisao"] != "seguir"
        out["proposal"] = {"action": action, "card": card}
        if exist:
            out["proposal"]["id"] = exist[0]["id"]
        art, tipo = TIPO_PT[card.get("kind") or kind]
        if not out["needs_confirm"]:
            res = self._execute(out, {})
            c = res.get("card") or card
            out["executed"] = True
            out["results"] = [c]
            if kind == "lista":
                n = len(c.get("checklist") or [])
                out["reply"] = FRASE["lista_criada"].format(titulo=c["title"], n=n, itens="item" if n == 1 else "itens")
            else:
                out["reply"] = FRASE["card_criado"].format(artigo=art, tipo=tipo, titulo=c.get("title") or titulo,
                                                           quando=_quando_pt(c))
        else:
            out["reply"] = FRASE["card_confirmar"].format(artigo=art, tipo=tipo, titulo=card.get("title") or titulo,
                                                          quando=_quando_pt(card))

    def _channels(self) -> List[Dict]:
        integ = getattr(self.app, "integrations", None)
        if integ is None:
            return []
        try:
            return [i for i in integ.list() if i.get("connected") and i.get("id") in ("google", "microsoft", "whatsapp")]
        except Exception:
            return []

    def _msg(self, out, text, fields, atalho, corpo, eng):
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
        out["proposal"] = {"action": "send_message", "to": destino, "name": nome, "body": body,
                           "channel": (canal or {}).get("id"), "channels": [c["id"] for c in chans],
                           "subject": None}
        out["needs_confirm"] = True
        known = any(destino and destino.casefold() == (p.get("email") or "").casefold()
                    for c in self.app.cards.list(limit=500) for p in c["people"]) if destino else False
        out["authorization"] = autorizar("enviar", {"texto": body or "", "destino": destino,
                                                    "contato_conhecido": known}, engine=eng)
        if out["authorization"]["decisao"] == "bloquear" and body:
            out["reply"] = FRASE["bloqueado"].format(motivos=", ".join(out["authorization"]["motivos"]))
        elif not destino:
            out["reply"] = FRASE["msg_sem_destino"]
        elif not body:
            out["reply"] = FRASE["msg_sem_corpo"].format(destino=shown)
        elif not canal:
            out["reply"] = FRASE["msg_sem_canal"].format(destino=shown, corpo=body)
        else:
            out["reply"] = FRASE["msg_confirmar"].format(destino=shown, canal=cname, corpo=body)

    def _search(self, out, text, fields, eng):
        q = re.sub(r"(?i)^\s*(?:me\s+)?(?:procura|procure|busca|busque|acha|ache|encontra|encontre|pesquisa|pesquise)"
                   r"(?:r)?\s+(?:por\s+|pelo\s+|pela\s+|o\s+|a\s+|os\s+|as\s+)?", "", text).strip(" ?.!") or text
        r = self.app.search.search(q, engine=eng, limit=10)
        out["proposal"] = {"action": "search", "q": q}
        out["results"] = r["results"]
        n = len(r["results"])
        out["reply"] = FRASE["busca"].format(n=n, itens="resultado" if n == 1 else "resultados", q=q) if n \
            else FRASE["busca_vazia"].format(q=q)

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

    # ------------------------------------------------------------------ confirmação --
    def _execute(self, rec: Dict, edits: Dict) -> Dict:
        p = rec["proposal"]
        a = p.get("action")
        if a == "create_card":
            card = dict(p["card"], **(edits or {}))
            c = self.app.cards.create(card)
            return {"ok": True, "card": c}
        if a == "update_card":
            c = self.app.cards.update(p["id"], dict(p["card"], **(edits or {})))
            return {"ok": True, "card": c}
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
                    "reply": FRASE["msg_enviada"].format(destino=q.get("name") or q["to"])}
        if a == "create_tree":
            tree = (edits or {}).get("tree") or p["tree"]
            if (edits or {}).get("graph"):
                from . import treegraph
                tree = treegraph.graph_to_tree(edits["graph"])
            r = self.app.add_rule(tree, (edits or {}).get("answers"))
            return dict(r, ok=True, reply=FRASE["regra_criada"].format(nome=r["added"]))
        return {"ok": True}

    def confirm(self, cid: str, approve: bool, edits: Optional[Dict] = None) -> Dict:
        with self.lock:
            rec = self.pending.get(cid)
        if not rec:
            raise KeyError(f"pedido não encontrado ou expirado: {cid}")
        if rec.get("done_result") is not None:
            return rec["done_result"]
        if rec.get("executed") and not edits:
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
        else:
            if rec["authorization"].get("decisao") == "bloquear":
                raise PermissionError(FRASE["bloqueado"].format(motivos=", ".join(rec["authorization"]["motivos"])))
            res = self._execute(rec, edits or {})
            res.setdefault("reply", "Feito.")
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
