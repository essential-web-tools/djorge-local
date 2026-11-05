"""Decision trees: each event walks a tree of decisions; each answer chooses the next decisions (or actions).

Node kinds (one key decides the kind):
  choice:  [opções]  (+ ask)          -> CHOICE  ; branches by option
  check:   "afirmação"                -> NOUL    ; branches true / false
  score:   [escala]  (+ ask)          -> SCORE   ; branches by label or ">= 2" on the index
  multi:   [rótulos] (+ ask)          -> SET     ; every selected label's branch runs (several follow-ups)
  select:  "TOTAL AGID limão"         -> any selector (FROM text is added); branches like ">= 10", "== 0"
  query:   "<DSL completo>"           -> raw DJORGE query (use FROM text)
  if:      {contains_any, contains_all, regex, source_in, app_in, sender_has, seguranca_in}   -> branches true / false
  action:  notify | log | write_file | webhook | command | stop   (see actions.py)
           v2.1 (secretário): criar_card | tarefa | alerta | notificar | etiquetar | arquivar | abrir_app |
           responder_sugestao — params: titulo, campos [valor, prazo, remetente, empresa, data, hora, link, codigo],
           coluna, prioridade, etiqueta, app, para, texto, url
  check + keywords: [..]               -> palavras-chave casam primeiro (regra); sem elas, o modelo (NOUL) julga
                                          se houver modelo carregado; sem modelo e sem palavra -> false
  if também aceita: categoria_in, prioridade_min (baixa|normal|alta|urgente), vip (true)

Common keys: model (origo | flash), min_probability (below it -> branch 'abstain'), branches {answer: next},
then (next for actions), next can be a node id or a list of ids (all run, in order).
Every event is checked first (security.py): {seguranca} = seguro|spam|golpe|phishing|malware, {seguranca_p}.
Text placeholders in strings: {text} {title} {source} {app} {sender} {time} (+ campos do secretário:
{valor} {prazo} {remetente} {empresa} {data} {hora} {link} {codigo} {categoria} {prioridade}) {<node>.answer} {<node>.probability}
{<node>.value}; {text:80} truncates. Placeholders are plain substitution (no code is evaluated).
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List

PH = re.compile(r"\{([a-zA-Z_][\w]*(?:\.[a-zA-Z_]\w*)?)(?::(\d{1,5}))?\}")
CMP = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:[.,]\d+)?)\s*$")


class TreeError(Exception):
    pass


def render(s: Any, ctx: Dict) -> Any:
    if not isinstance(s, str):
        return s

    def sub(m):
        key, n = m.group(1), m.group(2)
        if "." in key:
            node, f = key.split(".", 1)
            v = (ctx["results"].get(node) or {}).get(f, "")
        else:
            v = ctx["event"].get(key, "")
        v = "" if v is None else str(v)
        return v[: int(n)] + ("…" if n and len(v) > int(n) else "") if n else v
    return PH.sub(sub, s)


def validate(trees: List[dict]) -> List[dict]:
    names = set()
    for t in trees:
        if not t.get("name") or t["name"] in names:
            raise TreeError("cada árvore precisa de um 'name' único")
        names.add(t["name"])
        nodes = t.get("nodes") or {}
        if t.get("start") not in nodes:
            raise TreeError(f"{t['name']}: 'start' não existe em nodes")
        for nid, n in nodes.items():
            if isinstance(n.get("if"), dict) and n["if"].get("regex"):
                try:
                    regex_segura(n["if"]["regex"])
                except TreeError as e:
                    raise TreeError(f"{t['name']}.{nid}: {e}")
            if n.get("action") == "tratar" and n.get("tipo") not in TRATAMENTOS:
                raise TreeError(f"{t['name']}.{nid}: tratamento desconhecido '{n.get('tipo')}'")
            for nxt in _nexts(n):
                if nxt not in nodes:
                    raise TreeError(f"{t['name']}.{nid}: próximo nó inexistente '{nxt}'")
    return trees


def _nexts(n):
    out = []
    for v in list((n.get("branches") or {}).values()) + [n.get("then")]:
        if v is None:
            continue
        out += v if isinstance(v, list) else [v]
    return out


def matches(tree: dict, ev: dict) -> bool:
    w = tree.get("when") or {}
    if w.get("source_in") and ev.get("source") not in w["source_in"]:
        return False
    if w.get("app_in") and not any(a.casefold() in (ev.get("app") or "").casefold() for a in w["app_in"]):
        return False
    return _cond(w, ev) if any(k in w for k in ("contains_any", "contains_all", "regex", "sender_has",
                                                "seguranca_in")) else True


def _cond(c: dict, ev: dict) -> bool:
    t = f"{ev.get('title', '')}\n{ev.get('text', '')}".casefold()
    ok = True
    if c.get("contains_any"):
        ok &= any(x.casefold() in t for x in c["contains_any"])
    if c.get("contains_all"):
        ok &= all(x.casefold() in t for x in c["contains_all"])
    if c.get("regex"):
        ok &= re.search(regex_segura(c["regex"]), t[:20000], re.I) is not None
    if c.get("sender_has"):
        quem = fold(" ".join(str(ev.get(k) or "") for k in ("sender", "remetente")) + (
            " " + str(ev.get("title") or "") if ev.get("source") == "notifications" else ""))
        ok &= any(fold(x) in quem for x in c["sender_has"] if fold(x))
    if c.get("source_in"):
        ok &= ev.get("source") in c["source_in"]
    if c.get("seguranca_in"):                       # verdict of the automatic security check
        ok &= str(ev.get("seguranca") or "").casefold() in [str(x).casefold() for x in c["seguranca_in"]]
    if c.get("app_in"):
        ok &= any(a.casefold() in (ev.get("app") or "").casefold() for a in c["app_in"])
    if c.get("categoria_in"):                       # categoria da triagem (secretário)
        ok &= str(ev.get("categoria") or "").casefold() in [str(x).casefold() for x in c["categoria_in"]]
    if c.get("prioridade_min"):
        ok &= PRIORIDADES.get(str(ev.get("prioridade") or "").casefold(), 0) >= PRIORIDADES.get(
            str(c["prioridade_min"]).casefold(), 0)
    if c.get("vip"):
        ok &= bool(ev.get("vip"))
    if not ok:
        return False
    # v2.5 (automações com mais condições)
    if c.get("not_contains"):
        ok &= not any(fold(x) and fold(x) in fold(t) for x in c["not_contains"])
    if c.get("has_attachment"):
        ok &= tem_anexo(ev)
    if c.get("has_link"):
        ok &= bool(ev.get("link")) or re.search(r"https?://|\bwww\.", t) is not None
    if c.get("has_code"):
        ok &= bool(ev.get("codigo")) or re.search(r"(codigo|code|senha|token|otp|pin)\D{0,25}\d{4,8}\b", fold(t)) is not None
    if c.get("valor_gt") not in (None, "") or c.get("valor_lt") not in (None, ""):
        v = valor_num(ev)
        if v is None:
            ok = False
        else:
            if c.get("valor_gt") not in (None, ""):
                ok &= v > float(c["valor_gt"])
            if c.get("valor_lt") not in (None, ""):
                ok &= v < float(c["valor_lt"])
    if c.get("hora_entre"):
        ok &= hora_entre(_quando(ev), *list(c["hora_entre"])[:2])
    if c.get("dias_semana"):
        ok &= _quando(ev).weekday() in [int(d) for d in c["dias_semana"]]
    if c.get("tag_in"):
        tags = [fold(x) for x in (ev.get("tags") or [])] + [fold(ev.get("etiqueta"))]
        ok &= any(fold(x) in tags for x in c["tag_in"])
    if c.get("column_in"):
        ok &= fold(ev.get("column")) in [fold(x) for x in c["column_in"]]
    if c.get("all"):
        ok &= all(_cond(x, ev) for x in c["all"])
    if c.get("any"):
        ok &= any(_cond(x, ev) for x in c["any"])
    if c.get("not"):
        ok &= not _cond(c["not"], ev)
    return bool(ok)


COND_KEYS = ("contains_any", "contains_all", "regex", "source_in", "app_in", "sender_has", "seguranca_in",
             "categoria_in", "prioridade_min", "vip", "not_contains", "has_attachment", "has_link", "has_code",
             "valor_gt", "valor_lt", "hora_entre", "dias_semana", "tag_in", "column_in", "all", "any", "not",
             "count_min")
_REGEX_PERIGOSA = re.compile(r"\([^)]*[+*][^)]*\)\s*[+*{]|\\\d|\(\?[<=!P]")


def regex_segura(p: str) -> str:
    """Regex simples e segura: curta, sem quantificador aninhado ((a+)+), sem retrovisor nem lookaround."""
    p = str(p or "")
    if len(p) > 200:
        raise TreeError("regex longa demais (máx. 200 caracteres)")
    if _REGEX_PERIGOSA.search(p):
        raise TreeError("regex insegura: evite repetição dentro de repetição, retrovisores e lookarounds")
    try:
        re.compile(p)
    except re.error as e:
        raise TreeError(f"regex inválida: {e}")
    return p


def tem_anexo(ev: dict) -> bool:
    if ev.get("anexos") or ev.get("attachments") or ev.get("tem_anexo"):
        return True
    t = fold(f"{ev.get('title', '')} {ev.get('text', '')}")
    return re.search(r"\b(em anexo|anexad[oa]s?|segue anexo|anexo)\b|\.(pdf|docx?|xlsx?|zip|jpe?g|png)\b", t) is not None


def valor_num(ev: dict):
    """Valor em número (R$ 1.250,00 -> 1250.0) a partir do campo valor ou do texto."""
    for v in (ev.get("valor_num"), ev.get("valor")):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str) and v.strip():
            n = _num(v)
            if n is not None:
                return n
    m = re.search(r"r\$\s*([\d.]+(?:,\d{1,2})?|\d+(?:\.\d{1,2})?)", fold(f"{ev.get('title', '')} {ev.get('text', '')}"))
    return _num(m.group(1)) if m else None


def _num(s: str):
    s = re.sub(r"[^\d.,-]", "", str(s))
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1 or re.search(r"\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _quando(ev: dict):
    import datetime as _dt
    t = ev.get("time")
    if isinstance(t, str) and t:
        try:
            return _dt.datetime.fromisoformat(t[:19])
        except ValueError:
            pass
    return _dt.datetime.now()


def hora_entre(agora, de: str, ate: str) -> bool:
    hm = agora.strftime("%H:%M")
    de, ate = str(de or "00:00")[:5], str(ate or "23:59")[:5]
    return de <= hm <= ate if de <= ate else (hm >= de or hm <= ate)     # 22:00-06:00 atravessa a meia-noite


PRIORIDADES = {"baixa": 1, "normal": 2, "alta": 3, "urgente": 4}
NOVAS_ACOES = ("criar_card", "tarefa", "alerta", "notificar", "etiquetar", "arquivar", "abrir_app",
               "responder_sugestao", "destacar",
               # v2.5
               "lembrete", "nota", "atualizar_card", "mover_card", "concluir_card", "prioridade", "abrir_site",
               "ignorar", "encadear", "adicionar_lista", "registrar")
TRATAMENTOS = ("extrair", "classificar", "resumir", "formatar", "agrupar")


def fold(s) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return s.casefold()


def keyword_hit(keywords, ev: dict) -> bool:
    t = fold(f"{ev.get('title', '')}\n{ev.get('text', '')}")
    return any(fold(k) and fold(k) in t for k in keywords or [])


def _quote(s):
    from djorge.lang.quote import quote
    return quote(s)


def build_query(n: dict) -> str:
    ask = f" ASK {_quote(n['ask'])}" if n.get("ask") else ""
    lst = lambda xs: "[" + ", ".join(_quote(x) for x in xs) + "]"
    if "choice" in n:
        return f"CHOICE {lst(n['choice'])} FROM text{ask}"
    if "check" in n:
        return f"NOUL {_quote(n['check'])} FROM text"
    if "score" in n:
        return f"SCORE {lst(n['score'])} FROM text{ask}"
    if "multi" in n:
        return f"SET {lst(n['multi'])} FROM text{ask}"
    if "select" in n:
        q = n["select"].strip()
        return q if re.search(r"\bFROM\b", q, re.I) else q + " FROM text"
    if "query" in n:
        return n["query"]
    raise TreeError("nó sem tipo")


def _branch(n: dict, res: dict) -> List[str]:
    br = {str(k).casefold(): v for k, v in (n.get("branches") or {}).items()}
    keys: List[str] = []
    if res.get("abstain"):
        keys = ["abstain"]
    elif "selected" in res:
        keys = [str(s).casefold() for s in res["selected"]] or ["none"]
    else:
        a = res.get("answer", res.get("value"))
        keys = [str(a).casefold()]
        if isinstance(res.get("answer"), int) and res.get("label") is not None:
            keys.insert(0, str(res["label"]).casefold())
        num = res.get("value") if isinstance(res.get("value"), (int, float)) else (
            res.get("answer") if isinstance(res.get("answer"), (int, float)) and not isinstance(res.get("answer"), bool)
            else None)
        if num is not None:
            for k in br:
                m = CMP.match(k)
                if m:
                    x = float(m.group(2).replace(",", "."))
                    if {">=": num >= x, "<=": num <= x, "==": num == x, "!=": num != x, ">": num > x,
                        "<": num < x}[m.group(1)]:
                        keys.append(k); break
    out = []
    for k in keys:
        if k in br:
            v = br[k]; out += v if isinstance(v, list) else [v]
    if not out and "default" in br and not (res.get("abstain") and "abstain" in br):
        v = br["default"]; out = v if isinstance(v, list) else [v]
    return out


class Runner:
    def __init__(self, cfg, models, actions):
        self.cfg, self.models, self.actions = cfg, models, actions
        self.contagens: Dict[tuple, List[float]] = {}     # (árvore, nó) -> horários (condição "mais de N em X min")
        self.grupos: Dict[tuple, Dict] = {}               # (árvore, nó) -> {inicio, itens, tree} (tratamento agrupar)
        self.relogio = time.time                          # os testes trocam o relógio

    def _modelo(self):
        try:
            loaded = [x for x in self.models.loaded() if x in self.models.decision_models()]
        except Exception:
            return None
        return self.models.get(loaded[0]) if loaded else None

    def _contar(self, n: dict, ctx) -> dict:
        c = n["if"]["count_min"] or {}
        lim, jan = int(c.get("n") or 1), float(c.get("minutos") or 10) * 60
        agora = self.relogio()
        k = (ctx.get("tree"), ctx.get("nid"))
        xs = [x for x in self.contagens.get(k, []) if agora - x <= jan]
        if ctx.get("mode") != "testar":
            xs.append(agora)
            self.contagens[k] = xs[-500:]
            total = len(xs)
        else:
            total = len(xs) + 1
        return {"answer": total > lim, "value": total, "probability": 1.0, "engine": "regra"}

    def _tratar(self, n: dict, ctx) -> dict:
        """Tratamentos antes das ações; mudam o evento (campos novos viram {placeholders})."""
        from . import automacoes as T
        return T.tratar(self, n, ctx)

    def _decide(self, n: dict, ev: dict, ctx) -> dict:
        if "if" in n and isinstance(n["if"], dict) and n["if"].get("count_min"):
            return self._contar(n, ctx)
        if "if" in n:
            return {"answer": _cond(n["if"], ev), "probability": 1.0, "engine": "regra"}
        if "check" in n and "keywords" in n:            # palavra-chave primeiro; o modelo só se estiver carregado
            if keyword_hit(n["keywords"], ev):
                return {"answer": True, "probability": 0.9, "engine": "regra"}
            loaded = [x for x in self.models.loaded() if x in self.models.decision_models()]
            if not loaded:
                return {"answer": False, "probability": 0.5, "engine": "regra (sem modelo)"}
            z = self.models.get(loaded[0])
            r = z.execute(build_query(n), sources={"text": f"{ev.get('title') or ''}\n{ev.get('text') or ''}"},
                          lang=self.cfg["language"])["result"]
            p = float(r.get("probability") or 0)
            ans = bool(r.get("answer")) and p >= float(n.get("min_probability", 0.6))
            return {"answer": ans, "probability": p, "engine": "modelo", "model": loaded[0]}
        model = n.get("model") or ([x for x in self.models.loaded() if x in self.models.decision_models()]
                                     or self.models.decision_models() or ["flash"])[0]
        z = self.models.get(model)
        q = render(build_query(n), ctx) if n.get("render_query") else build_query(n)
        r = z.execute(q, sources={"text": ev.get("text") or ""}, lang=self.cfg["language"])["result"]
        out = {k: r.get(k) for k in ("answer", "value", "label", "probability", "selected", "abstain", "grounding",
                                     "totals", "unit") if k in r}
        out["model"], out["query"] = model, q
        mp = n.get("min_probability")
        if mp is not None and isinstance(out.get("probability"), (int, float)) and out["probability"] < mp:
            out["abstain"] = True
        return out

    def run(self, tree: dict, ev: dict, mode: str = None, start: str = None, cadeia: int = 0) -> dict:
        """mode: None (executa), "sugerir" (ações do secretário viram sugestões), "testar" (nada é executado).
        start: começar de outro nó (o agrupar continua dali); cadeia: profundidade de automações encadeadas."""
        nodes = tree["nodes"]
        ev = dict(ev)                                   # tratamentos mudam o evento só nesta árvore
        ctx = {"event": ev, "results": {}, "mode": mode, "tree": tree["name"], "cadeia": cadeia, "runner": self}
        trail, queue, steps = [], [start or tree["start"]], 0
        seen: Dict[str, int] = {}
        while queue:
            nid = queue.pop(0)
            steps += 1
            seen[nid] = seen.get(nid, 0) + 1
            if steps > self.cfg["max_steps"] or seen[nid] > 3:
                trail.append({"node": nid, "error": "limite de passos/ciclo atingido"}); break
            n = nodes[nid]
            ctx["nid"] = nid
            t0 = time.perf_counter()
            if n.get("action") == "tratar":
                try:
                    res = self._tratar(n, ctx)
                except Exception as e:
                    res = {"action": "tratar", "tipo": n.get("tipo"), "status": "erro", "error": f"{type(e).__name__}: {e}"}
                ctx["results"][nid] = res
                nxt = [] if res.get("parar") else (n["then"] if isinstance(n.get("then"), list)
                                                   else ([n["then"]] if n.get("then") else []))
            elif "action" in n:
                res = self.actions.run(n, ctx, tree["name"], nid)
                if res.get("card_id"):
                    ev.setdefault("card_id", res["card_id"])
                    ctx["ultimo_card"] = res["card_id"]
                nxt = [] if n["action"] == "stop" else (n["then"] if isinstance(n.get("then"), list)
                                                        else ([n["then"]] if n.get("then") else []))
            else:
                try:
                    res = self._decide(n, ev, ctx)
                except Exception as e:
                    res = {"error": f"{type(e).__name__}: {e}", "abstain": True}
                ctx["results"][nid] = res
                nxt = _branch(n, res)
            res["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            trail.append({"node": nid, **res, "next": nxt})
            if n.get("action") == "stop":
                break
            queue += nxt
        return {"tree": tree["name"], "trail": trail, "results": ctx["results"], "event": ev}

    def esvaziar_grupos(self, arvores: Dict[str, dict], mode: str = None, forcar: bool = False) -> List[dict]:
        """Grupos (tratamento agrupar) cujo tempo acabou: roda o resto da árvore com o resumo das mensagens."""
        from . import automacoes as T
        out = []
        for k, g in list(self.grupos.items()):
            tree = arvores.get(k[0])
            n = (tree or {}).get("nodes", {}).get(k[1])
            if tree is None or n is None:
                self.grupos.pop(k, None); continue
            if not forcar and self.relogio() - g["inicio"] < float(n.get("minutos") or 10) * 60:
                continue
            self.grupos.pop(k, None)
            ev = T.juntar(g["itens"])
            nxt = n.get("then")
            nxt = nxt[0] if isinstance(nxt, list) and nxt else nxt
            if nxt:
                out.append(self.run(tree, ev, mode=mode, start=nxt))
        return out
