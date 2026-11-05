"""O secretário: para cada notificação ou mensagem recebida faz a triagem (modelo + pistas, ou só regras), guarda no
store de notificações, roda as automações que casam e decide as ações conforme o modo:
  sugerir (padrão)  propõe; a pessoa aprova num clique (POST /api/notificacoes/<id>/acao {"acao":"aprovar"})
  agir              faz e avisa; tudo o que fez pode ser desfeito ({"acao":"desfazer"})
Respeita apps ignorados, VIPs e horário de silêncio (sem toasts nesse horário). Nada forçado: sem automação que
case, só age quando a triagem tem sinais claros (conta com valor/prazo, convite com data e hora, código, VIP,
urgente, ameaça). Resumo diário por toast na hora escolhida. Só roda em segundo plano quando ativo.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .tree import NOVAS_ACOES, fold

FONTES = ("notifications", "email", "imap", "whatsapp", "mensagem", "http", "integracao")
LISTAS = ("apps_ignorados", "vips", "remetentes_ignorados", "palavras_ignoradas", "categorias_ignoradas")
DEFAULT = {"ativo": False, "modo": "sugerir", "apps_ignorados": [], "vips": [],
           "remetentes_ignorados": [], "palavras_ignoradas": [], "categorias_ignoradas": [],
           "ignorar_como": "arquivar",          # "arquivar" (guarda arquivada, sem avisos nem ações) | "descartar"
           "horario_silencio": {"ativo": False, "inicio": "22:00", "fim": "07:00"},
           "resumo_diario_hora": "18:00", "toasts": True}
SEM = ["seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom."]
MES = ["jan.", "fev.", "mar.", "abr.", "maio", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."]
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def data_fmt(iso: str, hora: str = None) -> str:
    """'2026-11-03' -> 'ter., 3 de nov.' (+ ' às 14:00')."""
    try:
        d = _dt.date.fromisoformat(str(iso)[:10])
    except Exception:
        return str(iso or "")
    s = f"{SEM[d.weekday()]}, {d.day} de {MES[d.month - 1]}"
    if d.year != _dt.date.today().year:
        s += f" de {d.year}"
    return s + (f" às {hora}" if hora else "")


def valor_fmt(v) -> str:
    try:
        x = float(str(v).replace(",", "."))
    except Exception:
        return str(v or "")
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _hora_norm(h) -> Optional[str]:
    m = re.match(r"^(\d{1,2})(?::|h)?(\d{2})?", str(h or "").strip())
    if not m or int(m.group(1)) > 23:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}"


APPS_CONVERSA = ("whatsapp", "telegram", "signal", "messenger", "instagram", "teams", "slack", "discord",
                 "mensagens", "messages", "sms", "skype", "zap")


def remetente_de(ev: Dict) -> str:
    """Quem mandou: from/sender/remetente/autor; notificação de app de conversa = o título (nome da pessoa);
    de outros apps (banco, loja, sistema) = o nome do app, nunca o título ("Fatura")."""
    for k in ("from", "sender", "remetente", "autor"):
        v = ev.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("nome") or v.get("email") or v.get("address")
        if v and str(v).strip():
            v = str(v).strip()
            if ev.get("source") == "notifications" and not _de_conversa(ev) and v == (ev.get("title") or "").strip():
                break                                     # o conector pôs o título no sender: não é pessoa
            return v
    if ev.get("source") == "notifications":
        return (ev.get("title") or "").strip() if _de_conversa(ev) else (ev.get("app") or "").strip()
    return ""


def _de_conversa(ev: Dict) -> bool:
    a = fold(ev.get("app"))
    return any(x in a for x in APPS_CONVERSA)


def pedido_no_card(card: Dict, ped: Dict) -> Dict:
    """Card de um pedido: título = nome da tarefa (ou título curto), corpo = o texto central do pedido + quem/prazo/valor."""
    c = dict(card)
    c["title"] = (ped.get("tarefa") or ped.get("titulo") or c.get("title") or "")[:200]
    linhas = [ped.get("texto_central") or ""]
    extra = [f"Pedido por: {ped['quem']}" if ped.get("quem") else "",
             f"Prazo: {data_fmt(ped['prazo'])}" if ped.get("prazo") else "",
             f"Valor: {valor_fmt(ped['valor'])}" if ped.get("valor") else ""]
    extra = [x for x in extra if x]
    c["body"] = "\n".join([x for x in linhas if x] + ([""] + extra if extra else []))
    if ped.get("prazo") and not c.get("due"):
        c["due"] = ped["prazo"]
    return c


class Secretario:
    def __init__(self, app):
        from .automacoes import Automacoes
        from .notificacoes import Notificacoes
        from .config import HOME as _H
        from . import config as _c
        home = Path(getattr(_c, "HOME", _H))
        self.app = app
        self.path = home / "secretario.json"
        self.store = Notificacoes(home / "notificacoes.db")
        self.automacoes = Automacoes(home / "automacoes.json")
        self.lock = threading.Lock()
        self.conf = copy.deepcopy(DEFAULT)
        if self.path.is_file():
            try:
                self.conf.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass
        self._stop = threading.Event()
        self._thread = None
        self._ultimo_resumo = None
        self.agora = lambda: _dt.datetime.now()          # os testes trocam o relógio
        a = app.actions
        for k in ("criar_card", "tarefa", "alerta"):
            a.hooks[k] = self._h_card
        a.hooks["etiquetar"] = self._h_etiquetar
        a.hooks["arquivar"] = self._h_arquivar
        a.hooks["notificar"] = self._h_notificar

    # ------------------------------------------------------------------------------------ configuração --
    def config(self) -> Dict:
        return dict(copy.deepcopy(self.conf), em_silencio=self.em_silencio(), rodando=self.rodando())

    def configurar(self, patch: Dict) -> Dict:
        c = copy.deepcopy(self.conf)
        for k, v in (patch or {}).items():
            if k == "ativo":
                c[k] = bool(v)
            elif k == "toasts":
                c[k] = bool(v)
            elif k == "modo":
                if v not in ("sugerir", "agir"):
                    raise ValueError("modo: sugerir ou agir")
                c[k] = v
            elif k == "ignorar_como":
                if v not in ("arquivar", "descartar"):
                    raise ValueError("ignorar_como: arquivar ou descartar")
                c[k] = v
            elif k in LISTAS:
                if isinstance(v, str):
                    v = [x.strip() for x in re.split(r"[\n,;]", v) if x.strip()]
                if not isinstance(v, list):
                    raise ValueError(f"{k}: lista esperada")
                c[k] = [str(x).strip()[:120] for x in v if str(x).strip()][:200]
            elif k == "horario_silencio":
                if isinstance(v, str):                   # "22:00-07:00" ou "" (desliga)
                    if not v.strip():
                        v = dict(c[k], ativo=False)
                    else:
                        ini, _, fim = v.partition("-")
                        v = {"ativo": True, "inicio": ini.strip(), "fim": fim.strip()}
                if not isinstance(v, dict):
                    raise ValueError("horario_silencio: {ativo, inicio, fim}")
                h = dict(c[k], **v)
                h["inicio"], h["fim"] = _hora_norm(h.get("inicio")), _hora_norm(h.get("fim"))
                if not h["inicio"] or not h["fim"]:
                    raise ValueError("horário de silêncio: use HH:MM, ex.: 22:00 e 07:00")
                h["ativo"] = bool(h.get("ativo", True))
                c[k] = {"ativo": h["ativo"], "inicio": h["inicio"], "fim": h["fim"]}
            elif k == "resumo_diario_hora":
                if v in (None, "", False):
                    c[k] = ""
                else:
                    hv = _hora_norm(v)
                    if not hv:
                        raise ValueError("resumo diário: use HH:MM, ex.: 18:00")
                    c[k] = hv
            else:
                raise ValueError(f"campo desconhecido: {k}")
        self.conf = c
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
        if c["ativo"]:
            self.start()
        else:
            self.stop()
        return self.config()

    def em_silencio(self, agora: _dt.datetime = None) -> bool:
        h = self.conf.get("horario_silencio") or {}
        if not h.get("ativo"):
            return False
        t = (agora or self.agora()).strftime("%H:%M")
        ini, fim = h.get("inicio", "22:00"), h.get("fim", "07:00")
        return (ini <= t < fim) if ini <= fim else (t >= ini or t < fim)

    # ------------------------------------------------------------------------------------- triagem --
    def _engine(self):
        try:
            return self.app.command.engine()
        except Exception:
            return None

    def triar(self, ev: Dict) -> Dict:
        from djorge.runtime.triagem import triar
        eng = self._engine() if self.conf.get("ativo") else None   # desligado: só lista e etiqueta por regras
        try:
            hoje = self.agora().date()
        except Exception:
            hoje = None
        tri = triar(str(ev.get("text") or "")[:8000], remetente=remetente_de(ev) or None,
                    assunto=ev.get("title") or None, engine=eng, vips=self.conf.get("vips") or (), hoje=hoje)
        tri["model"] = eng is not None
        try:                                             # pistas de R (opcional)
            from . import pistas as P
            from djorge.runtime.recognizers import reconhecer
            tri["pistas"] = P.pistas(f"{ev.get('title') or ''}\n{ev.get('text') or ''}",
                                     reconhecer(str(ev.get("text") or ""), hoje))
        except Exception:
            pass
        return tri

    def eh_vip(self, ev: Dict) -> bool:
        quem = fold(" ".join([remetente_de(ev), str(ev.get("remetente") or "")]))
        return any(fold(v) and fold(v) in quem for v in self.conf.get("vips") or [])

    def enriquecer(self, ev: Dict, tri: Dict) -> Dict:
        from .contatos import _do_remetente, nome_fmt, fone_fmt
        e = dict(ev)
        d = tri.get("dados") or {}
        sender = remetente_de(e)
        r = _do_remetente(sender) if sender else {"nome": "", "email": None, "telefone": None}
        empresa = next((x["nome"] for x in tri.get("entidades") or [] if x.get("tipo") == "empresa"), "")
        if r["email"]:
            try:
                from djorge.runtime.recognizers import nome_do_email
                ne = nome_do_email(r["email"])
                empresa = ne.get("empresa") or empresa
            except Exception:
                pass
        e["remetente"] = nome_fmt(r["nome"]) if r["nome"] else (empresa or "")
        e["remetente_email"] = r["email"] or ((d.get("emails") or [None])[0])
        e["telefone"] = r["telefone"] or (fone_fmt(d["telefones"][0]) if d.get("telefones") else None)
        e["empresa"] = empresa or e["remetente"]
        e["valor"] = valor_fmt(d["valores"][0]) if d.get("valores") else ""
        e["prazo_iso"] = tri.get("prazo")
        e["prazo"] = data_fmt(tri["prazo"]) if tri.get("prazo") else ""
        hora = _hora_norm(d["horas"][0]) if d.get("horas") else None
        e["hora"] = hora or ""
        e["data"] = data_fmt(d["datas"][0]) if d.get("datas") else ""
        e["inicio_iso"] = f"{d['datas'][0]}T{hora}" if d.get("datas") and hora else (d["datas"][0] if d.get("datas") else None)
        e["link"] = (d.get("reuniao") or d.get("links") or [""])[0]
        e["codigo"] = (d.get("codigo") or d.get("boleto") or [""])[0]
        e["código"] = e["codigo"]
        e["categoria"], e["prioridade"] = tri.get("categoria"), tri.get("prioridade")
        e["vip"] = self.eh_vip(e)
        if tri.get("pedido"):
            e["pedido"] = dict(tri["pedido"])
            if not e["pedido"].get("quem_do_texto") and e.get("remetente"):
                e["pedido"]["quem"] = e["remetente"]       # quem pediu = o remetente já formatado
        return e

    # ----------------------------------------------------------------------------------- processar --
    CAMPOS_EV = ("remetente", "remetente_email", "telefone", "empresa", "valor", "prazo", "prazo_iso", "hora",
                 "data", "inicio_iso", "link", "codigo", "categoria", "prioridade", "vip", "pedido")

    def _sinais(self, tri: Dict, ev: Dict) -> List[Dict]:
        """Ações padrão quando nenhuma automação casou — só com sinais claros."""
        out, acoes = [], tri.get("acoes") or []
        if tri.get("categoria") == "segurança":
            return [{"action": "etiquetar", "etiqueta": "segurança"},
                    {"action": "notificar", "titulo": "Cuidado: possível golpe", "mensagem": "{app}: {title}"}]
        if tri.get("categoria") == "promoções":
            return [{"action": "arquivar"}]
        if "pagar" in acoes and (ev.get("valor") or ev.get("prazo")):
            out.append({"action": "tarefa", "titulo": "Pagar {empresa}", "campos": ["valor", "prazo", "codigo"],
                        "coluna": "hoje", "etiqueta": "contas", "pedido": False})
        if "agendar" in acoes and ev.get("data") and ev.get("hora"):
            out.append({"action": "criar_card", "tipo": "evento", "titulo": "{title}",
                        "campos": ["data", "hora", "link", "remetente"]})
        if "usar código" in acoes and ev.get("codigo"):
            out.append({"action": "notificar", "titulo": "Código {codigo}", "mensagem": "{app}: {title}"})
        ped = tri.get("pedido")
        if ped and "criar tarefa" in acoes and not out:
            hoje = (ev.get("prazo_iso") or "")[:10] == self.agora().date().isoformat()
            out.append({"action": "tarefa", "titulo": ped["tarefa"].replace("{", "(").replace("}", ")"),
                        "pedido": True, "campos": ["remetente", "prazo", "valor"],
                        "coluna": "hoje" if hoje or tri.get("prioridade") == "urgente" else "entrada"})
        if (ev.get("vip") or tri.get("prioridade") == "urgente") and not out:
            out.append({"action": "alerta", "titulo": "{remetente} — {title}", "campos": ["prazo", "valor", "link"],
                        "prioridade": 3 if ev.get("vip") else 2})
        return out

    def ignorada(self, ev: Dict) -> Optional[str]:
        """Motivo se a notificação casa com algo que a pessoa mandou ignorar (app, remetente ou palavras)."""
        c = self.conf
        app_nome = fold(ev.get("app"))
        for x in c.get("apps_ignorados") or []:
            if fold(x) and app_nome and fold(x) in app_nome:
                return f"app ignorado: {x}"
        rem = fold(" ".join(str(ev.get(k) or "") for k in ("remetente", "from", "sender", "autor", "title")))
        for x in c.get("remetentes_ignorados") or []:
            if fold(x) and fold(x) in rem:
                return f"remetente ignorado: {x}"
        texto = fold(" ".join(str(ev.get(k) or "") for k in ("title", "titulo", "assunto", "text", "texto", "body")))
        for x in c.get("palavras_ignoradas") or []:
            if fold(x) and re.search(r"(?<!\w)" + re.escape(fold(x)) + r"(?!\w)", texto):
                return f"palavra ignorada: {x}"
        return None

    def ignorar_parecidas(self, notif: Dict, por: str) -> Dict:
        """'Ignorar notificações como esta': por app, remetente ou tipo (categoria)."""
        mapa = {"app": ("apps_ignorados", notif.get("app")),
                "remetente": ("remetentes_ignorados", notif.get("remetente")),
                "tipo": ("categorias_ignoradas", notif.get("etiqueta")),
                "categoria": ("categorias_ignoradas", notif.get("etiqueta"))}
        if por not in mapa or not mapa[por][1]:
            raise ValueError(f"não dá para ignorar por {por} nesta notificação")
        chave, valor = mapa[por]
        lista = list(self.conf.get(chave) or [])
        if fold(valor) not in [fold(x) for x in lista]:
            lista.append(str(valor))
        self.configurar({chave: lista})
        return {"ok": True, "chave": chave, "valor": valor, "config": self.config()}

    def processar(self, ev: Dict) -> Optional[Dict]:
        if ev.get("skipped") or ev.get("source") not in FONTES or ev.get("id") == "teste":
            return None
        motivo = self.ignorada(ev)
        if motivo and self.conf.get("ignorar_como") == "descartar":
            return {"ignorado": True, "motivo": motivo}
        tri = self.triar(ev) if not motivo else {"categoria": "", "prioridade": "baixa", "nota": 0,
                                                  "motivos": [motivo]}
        if not motivo:
            cat = fold(tri.get("categoria"))
            if cat and any(fold(x) == cat for x in self.conf.get("categorias_ignoradas") or []):
                motivo = f"tipo ignorado: {tri.get('categoria')}"
                if self.conf.get("ignorar_como") == "descartar":
                    return {"ignorado": True, "motivo": motivo}
        if motivo:                                        # guarda arquivada, sem avisos nem ações
            e = dict(ev)
            n = self.store.add(e, etiqueta=tri.get("categoria") or "ignorada", prioridade="baixa", nota=0,
                               motivos=[motivo], dados={})
            try:
                self.store.update(n["id"], arquivada=True, lida=True)
            except Exception:
                pass
            return {"ignorado": True, "motivo": motivo, "notificacao": n["id"]}
        e = self.enriquecer(ev, tri)
        dados = {k: e.get(k) for k in self.CAMPOS_EV if e.get(k) not in (None, "")}
        n = self.store.add(e, etiqueta=tri.get("categoria") or "", prioridade=tri.get("prioridade") or "normal",
                           nota=int(tri.get("nota") or 0), motivos=tri.get("motivos") or [], dados=dados)
        e["notif_id"] = n["id"]
        out = {"notificacao": n["id"], "triagem": {k: tri.get(k) for k in ("categoria", "prioridade", "nota",
                                                                           "motivos", "acoes", "prazo")},
               "regras": [], "acoes": [], "sugestoes": []}
        if not self.conf.get("ativo"):
            return out
        mode = "sugerir" if self.conf.get("modo", "sugerir") == "sugerir" else None
        feitos = []                                       # (origem, resultado)
        for a, tree in self.automacoes.ativas():
            try:
                r = self.app.runner.run(tree, e, mode=mode)
            except Exception as ex:
                out.setdefault("erros", []).append(f"{a['nome']}: {type(ex).__name__}: {ex}")
                continue
            res = [s for s in r["trail"] if s.get("action") in NOVAS_ACOES]
            if res:
                out["regras"].append(a["nome"])
                self.automacoes.contar(a["id"])
                feitos += [(a["nome"], s) for s in res]
        if not out["regras"]:
            ctx = {"event": e, "results": {}, "mode": mode}
            for spec in self._sinais(tri, e):
                feitos.append(("secretário", self.app.actions.run(spec, ctx, "secretario", "sinais")))
        sug, acoes, card_id = [], [], None
        for origem, s in feitos:
            item = {k: v for k, v in s.items() if k not in ("node", "next", "ms")}
            item["origem"] = origem
            if s.get("status") == "sugerido":
                item["i"] = len(sug)
                sug.append(item)
            else:
                acoes.append(item)
                card_id = card_id or s.get("card_id")
        upd = {"regras": out["regras"], "acoes": acoes, "sugestoes": sug}
        if card_id:
            upd["card_id"] = card_id
        self.store.update(n["id"], **upd)
        out.update(acoes=acoes, sugestoes=sug, card_id=card_id)
        try:
            self.app.hist.add({"type": "secretario", "notificacao": n["id"], "regras": out["regras"],
                               "acoes": [x.get("action") for x in acoes], "sugestoes": len(sug)})
        except Exception:
            pass
        return out

    # ------------------------------------------------------------------------------- ações (hooks) --
    def _toast(self, titulo, msg) -> str:
        if not self.conf.get("toasts", True):
            return "toasts desligados"
        if self.em_silencio():
            return "silenciado"
        from . import toasts
        return "ok" if toasts.mostrar(titulo, msg) else "sem toast"

    def _h_card(self, spec, ctx, prep):
        card = dict(prep["card"])
        ev = ctx.get("event") or {}
        ped = ev.get("pedido") if isinstance(ev.get("pedido"), dict) else None
        if ped and spec.get("pedido") is not False:     # pedido: título = tarefa, corpo = texto central
            card = pedido_no_card(card, ped)
        nid = ev.get("notif_id")
        if nid:
            card["source"] = {"integration": "secretario", "external_id": nid, "url": None}
        c = self.app.cards.create(card)
        r = {"status": "ok", "card_id": c["id"], "desfazer": {"tipo": "card", "id": c["id"]}}
        if nid:
            try:
                self.store.update(nid, card_id=c["id"])
            except Exception:
                pass
        if spec["action"] == "alerta":
            r["toast"] = self._toast(c["title"], (card.get("body") or "")[:200])
        return r

    def _h_etiquetar(self, spec, ctx, prep):
        nid = (ctx.get("event") or {}).get("notif_id")
        if not nid or not prep.get("etiqueta"):
            return {"status": "sem notificação"}
        antes = (self.store.get(nid) or {}).get("etiqueta")
        self.store.update(nid, etiqueta=prep["etiqueta"])
        return {"status": "ok", "desfazer": {"tipo": "etiqueta", "id": nid, "antes": antes}}

    def _h_arquivar(self, spec, ctx, prep):
        nid = (ctx.get("event") or {}).get("notif_id")
        if not nid:
            return {"status": "sem notificação"}
        self.store.update(nid, arquivada=True, lida=True)
        return {"status": "ok", "desfazer": {"tipo": "arquivar", "id": nid}}

    def _h_notificar(self, spec, ctx, prep):
        return {"status": self._toast(prep["titulo"], prep["mensagem"])}

    # --------------------------------------------------------------------------- aprovar / desfazer --
    def _ev_de(self, n: Dict) -> Dict:
        ev = {"id": n["id"], "source": n["fonte"], "app": n["app"], "title": n["titulo"], "text": n["texto"],
              "sender": n["remetente"], "time": n["t"], "notif_id": n["id"]}
        ev.update(n.get("dados") or {})
        ev["código"] = ev.get("codigo", "")
        return ev

    def acao(self, nid: str, acao: str, b: Dict = None) -> Dict:
        b = b or {}
        n = self.store.get(nid)
        if not n:
            raise KeyError("notificação não encontrada")
        if acao == "aprovar":
            idx = b.get("i")
            sug = n["sugestoes"]
            escolhidas = [s for s in sug if idx is None or s.get("i") == int(idx)]
            ctx = {"event": self._ev_de(n), "results": {}, "mode": None}
            feitos = []
            for s in escolhidas:
                r = self.app.actions.executar_nova(s["spec"], ctx)
                feitos.append(dict({k: v for k, v in r.items()}, origem=s.get("origem")))
            resto = [s for s in sug if s not in escolhidas]
            n2 = self.store.get(nid)
            card = next((f.get("card_id") for f in feitos if f.get("card_id")), None)
            upd = {"sugestoes": resto, "acoes": n2["acoes"] + feitos, "lida": True}
            if card:
                upd["card_id"] = card
            return {"ok": True, "feitas": feitos, "notificacao": self.store.update(nid, **upd)}
        if acao == "recusar":
            idx = b.get("i")
            resto = [s for s in n["sugestoes"] if idx is not None and s.get("i") != int(idx)]
            return {"ok": True, "notificacao": self.store.update(nid, sugestoes=resto)}
        if acao == "desfazer":
            return self.desfazer(nid)
        if acao in ("arquivar", "desarquivar"):
            return {"ok": True, "notificacao": self.store.update(nid, arquivada=acao == "arquivar",
                                                                **({"lida": True} if acao == "arquivar" else {}))}
        if acao in ("lida", "nao_lida"):
            return {"ok": True, "notificacao": self.store.update(nid, lida=acao == "lida")}
        if acao in ("abrir_app", "responder"):
            spec = {"action": "abrir_app", "app": b.get("app") or "whatsapp", "para": b.get("para"),
                    "texto": b.get("texto") or "", "url": b.get("url")}
            ctx = {"event": self._ev_de(n), "results": {}, "mode": None}
            return self.app.actions.executar_nova(spec, ctx)
        if acao == "criar_card":
            ctx = {"event": self._ev_de(n), "results": {}, "mode": None}
            r = self.app.actions.executar_nova({"action": b.get("tipo_acao") or "criar_card",
                                                "titulo": b.get("titulo") or "{title}",
                                                "campos": b.get("campos") or ["remetente", "prazo", "valor", "link"]}, ctx)
            if r.get("card_id"):
                self.store.update(nid, acoes=self.store.get(nid)["acoes"] + [r])
            return r
        if acao == "ignorar_parecidas":                   # {por: app | remetente | tipo}
            r = self.ignorar_parecidas(n, b.get("por") or "app")
            self.store.update(nid, arquivada=True, lida=True)
            return r
        raise ValueError("ação: aprovar, recusar, desfazer, arquivar, desarquivar, lida, nao_lida, abrir_app, "
                         "criar_card, ignorar_parecidas")

    def desfazer(self, nid: str) -> Dict:
        n = self.store.get(nid)
        if not n:
            raise KeyError("notificação não encontrada")
        desfeitas, resto = [], []
        for a in n["acoes"]:
            u = a.get("desfazer")
            if not u or a.get("desfeita"):
                resto.append(a); continue
            try:
                if u["tipo"] == "card":
                    self.app.cards.delete(u["id"])
                elif u["tipo"] == "etiqueta":
                    self.store.update(nid, etiqueta=u.get("antes") or "")
                elif u["tipo"] == "arquivar":
                    self.store.update(nid, arquivada=False)
                desfeitas.append(a.get("action"))
                resto.append(dict(a, desfeita=True))
            except Exception as e:
                resto.append(dict(a, erro_desfazer=f"{type(e).__name__}: {e}"))
        upd = {"acoes": resto}
        if any(a.get("desfazer", {}).get("tipo") == "card" for a in n["acoes"]):
            upd["card_id"] = None
        return {"ok": True, "desfeitas": desfeitas, "notificacao": self.store.update(nid, **upd)}

    # ------------------------------------------------------------------------------------- resumo --
    def resumo_diario(self, dia: _dt.date = None) -> Dict:
        dia = dia or self.agora().date()
        c = self.store.contar(desde=dia.isoformat())
        hoje = [n for n in self.store.list(desde=dia.isoformat(), limit=1000, arquivada="todas")]
        cards = sum(1 for n in hoje for a in n["acoes"] if a.get("card_id") and not a.get("desfeita"))
        sug = sum(len(n["sugestoes"]) for n in hoje)
        tot = c["total"]
        if not tot:
            frase = "Hoje não chegou nada novo."
        else:
            frase = f"Hoje chegaram {tot} {'mensagem' if tot == 1 else 'mensagens'}"
            frase += f", {c['importantes']} {'importante' if c['importantes'] == 1 else 'importantes'}" if c["importantes"] else ""
            frase += "."
            if cards:
                frase += f" Criei {cards} {'card' if cards == 1 else 'cards'}."
            if sug:
                frase += f" {sug} {'sugestão espera' if sug == 1 else 'sugestões esperam'} você."
        return {"dia": dia.isoformat(), "frase": frase, "total": tot, "importantes": c["importantes"],
                "nao_lidas": c["nao_lidas"], "cards": cards, "sugestoes": sug}

    # ----------------------------------------------------------------------------- segundo plano --
    def rodando(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if not self.conf.get("ativo") or self.rodando():
            return
        self._stop.clear()
        h, now = self.conf.get("resumo_diario_hora"), self.agora()
        if h and now.strftime("%H:%M") >= h:             # já passou da hora hoje: o próximo resumo é amanhã
            self._ultimo_resumo = now.date()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="secretario")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(30):
            try:
                h = self.conf.get("resumo_diario_hora")
                now = self.agora()
                if h and now.strftime("%H:%M") >= h and self._ultimo_resumo != now.date():
                    self._ultimo_resumo = now.date()
                    r = self.resumo_diario(now.date())
                    if r["total"]:
                        self._toast("Resumo do dia", r["frase"])
            except Exception:
                pass

    def close(self):
        self.stop()
        self.store.close()
