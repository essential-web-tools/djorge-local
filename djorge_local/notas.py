"""Bloco de notas (rodada 4): notas em Markdown leve, pastas, etiquetas, fixar, buscar, autosave e versões.

SQLite em HOME/notas.db. O modelo participa sem gerar texto: tudo o que ele devolve é TRECHO da nota
(título sugerido, resumo extrativo, tarefas, pessoas) ou uma ESCOLHA entre etiquetas que já existem.
Com o modelo carregado, uma chamada em lote ao DOFL: NOUL "esta frase é uma tarefa a fazer?" por frase,
CHOICE entre as etiquetas existentes e resolve ENTITY "pessoa". Sem modelo, regras.

Rotas (ver `rotas`):
  GET  /api/notas?q=&pasta=&etiqueta=        lista (fixadas primeiro) + pastas + etiquetas
  POST /api/notas                            cria {titulo, corpo, pasta, etiquetas, fixada}
  GET  /api/notas/<id>                       nota completa
  POST /api/notas/<id>                       atualização parcial (autosave); {versao:true} força uma versão
  POST /api/notas/<id>/delete
  GET  /api/notas/<id>/versoes               versões anteriores
  POST /api/notas/<id>/versoes {restaurar}   volta a uma versão (a atual vira versão)
  POST /api/notas/<id>/analisar              {titulo_sugerido, etiquetas, tarefas, datas, pessoas, resumo, fonte}
  POST /api/notas/<id>/perguntar {q}         {trechos:[{texto, ini, fim, score}], resposta?}
  POST /api/notas/<id>/tarefas {indices}     cria os cards das tarefas escolhidas
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from djorge.runtime.hag import BM25, Document, _fold, _terms, sentences

MAX_CORPO = 200_000
MAX_VERSOES = 50
VERSAO_INTERVALO_S = 120            # autosave: no máximo uma versão a cada 2 min (salvo pedido explícito)
NOUL_TAREFA = "Esta frase é uma tarefa a fazer."
ASK_ETIQUETA = "Qual etiqueta combina com esta nota?"
SEM_ETIQUETA = "nenhuma destas"

_VERBOS = ("ligar", "comprar", "enviar", "mandar", "pagar", "marcar", "agendar", "lembrar", "fazer", "levar",
           "buscar", "pegar", "responder", "revisar", "entregar", "terminar", "preparar", "falar", "avisar",
           "reservar", "cancelar", "renovar", "resolver", "organizar", "escrever", "conferir", "verificar",
           "confirmar", "imprimir", "estudar", "limpar", "consertar", "trocar", "devolver", "cobrar", "agendar")
_RX_TAREFA = re.compile(
    r"(?i)^\s*(?:[-*+]\s*\[\s\]|todo\b|a fazer\b|\[\s\])|\b(?:preciso|precisamos|tenho que|temos que|tenho de|"
    r"não esquecer|nao esquecer|lembrar de|devo|vou ter que)\b|^\s*(?:[-*+]|\d+[.)])?\s*(?:" + "|".join(_VERBOS) + r")\b")
_RX_FEITA = re.compile(r"^\s*[-*+]\s*\[[xX]\]")
_RX_PESSOA = re.compile(r"\b(?:com|para|pra|pro|ao|à|a|o|da|do|de|ligar para|falar com)\s+"
                        r"((?:[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+)(?:\s+(?:de |da |do |dos |das )?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+){0,2})")
_NAO_NOME = {"segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo", "janeiro", "fevereiro", "março",
             "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro", "hoje",
             "amanhã", "reunião", "deus"}


def _agora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _limpa_md(s: str) -> str:
    """Tira as marcas do Markdown leve de uma linha/frase (o texto continua sendo da nota)."""
    s = re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s*\[[ xX]\]\s*|[-*+]\s+|\d+[.)]\s+|>\s*)", "", s)
    s = re.sub(r"(\*\*|__|~~|`)", "", s)
    return " ".join(s.split()).strip(" -–—:;")


def _tags(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in re.split(r"[,;]", v)]
    out = []
    for x in v:
        x = " ".join(str(x).strip().lstrip("#").split())[:40]
        if x and x.casefold() not in {o.casefold() for o in out}:
            out.append(x)
    return out[:20]


class Notas:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS notas (id TEXT PRIMARY KEY, titulo TEXT, corpo TEXT, pasta TEXT,
                etiquetas TEXT, fixada INTEGER, cards TEXT, criada TEXT, atualizada TEXT);
            CREATE TABLE IF NOT EXISTS versoes (id INTEGER PRIMARY KEY AUTOINCREMENT, nota TEXT, t TEXT,
                titulo TEXT, corpo TEXT);
            CREATE INDEX IF NOT EXISTS versoes_nota ON versoes(nota);
        """)
        self.db.commit()

    # ---------------------------------------------------------------- CRUD --
    def _row(self, r) -> Dict:
        d = dict(r)
        d["etiquetas"] = json.loads(d.get("etiquetas") or "[]")
        d["cards"] = json.loads(d.get("cards") or "[]")
        d["fixada"] = bool(d.get("fixada"))
        return d

    def get(self, nid: str) -> Optional[Dict]:
        with self.lock:
            r = self.db.execute("SELECT * FROM notas WHERE id=?", (str(nid),)).fetchone()
        return self._row(r) if r else None

    def _exige(self, nid) -> Dict:
        n = self.get(nid)
        if not n:
            raise KeyError(f"nota não encontrada: {nid}")
        return n

    @staticmethod
    def _campos(b: Dict, base: Dict) -> Dict:
        n = dict(base)
        b = dict(b)
        for sin in ("texto", "conteudo", "conteúdo"):
            if sin in b and "corpo" not in b:
                b["corpo"] = b[sin]
        if "titulo" in b:
            n["titulo"] = " ".join(str(b["titulo"] or "").split())[:200]
        if "corpo" in b:
            c = str(b["corpo"] or "")
            if len(c) > MAX_CORPO:
                raise ValueError(f"nota longa demais (máx. {MAX_CORPO} caracteres)")
            n["corpo"] = c
        if "pasta" in b:
            n["pasta"] = " ".join(str(b["pasta"] or "").split())[:80]
        if "etiquetas" in b:
            n["etiquetas"] = _tags(b["etiquetas"])
        if "fixada" in b:
            n["fixada"] = bool(b["fixada"])
        return n

    def _gravar(self, n: Dict):
        self.db.execute("INSERT OR REPLACE INTO notas VALUES (?,?,?,?,?,?,?,?,?)",
                        (n["id"], n["titulo"], n["corpo"], n["pasta"], json.dumps(n["etiquetas"], ensure_ascii=False),
                         int(n["fixada"]), json.dumps(n.get("cards") or []), n["criada"], n["atualizada"]))
        self.db.commit()

    def criar(self, b: Dict) -> Dict:
        if not isinstance(b, dict):
            raise ValueError("nota: objeto esperado")
        t = _agora()
        n = self._campos(b, {"id": uuid.uuid4().hex[:12], "titulo": "", "corpo": "", "pasta": "", "etiquetas": [],
                             "fixada": False, "cards": [], "criada": t, "atualizada": t})
        with self.lock:
            self._gravar(n)
        return n

    def atualizar(self, nid: str, b: Dict) -> Dict:
        """Parcial (autosave). Guarda a versão anterior quando o texto mudou e a última versão é antiga."""
        with self.lock:
            cur = self._exige(nid)
            n = self._campos(b, cur)
            mudou = n["titulo"] != cur["titulo"] or n["corpo"] != cur["corpo"]
            if mudou and (cur["corpo"] or cur["titulo"]):
                ult = self.db.execute("SELECT t FROM versoes WHERE nota=? ORDER BY id DESC LIMIT 1",
                                      (nid,)).fetchone()
                velha = not ult or (_dt.datetime.now() - _dt.datetime.fromisoformat(ult["t"])).total_seconds() \
                    >= VERSAO_INTERVALO_S
                if b.get("versao") or velha:
                    self._versao(cur)
            n["atualizada"] = _agora()
            self._gravar(n)
            return n

    def _versao(self, n: Dict):
        self.db.execute("INSERT INTO versoes (nota, t, titulo, corpo) VALUES (?,?,?,?)",
                        (n["id"], _agora(), n["titulo"], n["corpo"]))
        ids = [r[0] for r in self.db.execute("SELECT id FROM versoes WHERE nota=? ORDER BY id DESC", (n["id"],))]
        for vid in ids[MAX_VERSOES:]:
            self.db.execute("DELETE FROM versoes WHERE id=?", (vid,))
        self.db.commit()

    def apagar(self, nid: str) -> bool:
        with self.lock:
            k = self.db.execute("DELETE FROM notas WHERE id=?", (str(nid),)).rowcount
            self.db.execute("DELETE FROM versoes WHERE nota=?", (str(nid),))
            self.db.commit()
        if not k:
            raise KeyError(f"nota não encontrada: {nid}")
        return True

    def versoes(self, nid: str) -> List[Dict]:
        self._exige(nid)
        with self.lock:
            rs = self.db.execute("SELECT * FROM versoes WHERE nota=? ORDER BY id DESC", (str(nid),)).fetchall()
        return [{"id": r["id"], "t": r["t"], "titulo": r["titulo"], "corpo": r["corpo"],
                 "trecho": " ".join((r["corpo"] or "").split())[:160]} for r in rs]

    def restaurar(self, nid: str, vid) -> Dict:
        with self.lock:
            cur = self._exige(nid)
            r = self.db.execute("SELECT * FROM versoes WHERE nota=? AND id=?", (str(nid), int(vid))).fetchone()
            if not r:
                raise KeyError(f"versão não encontrada: {vid}")
            self._versao(cur)
            n = dict(cur, titulo=r["titulo"], corpo=r["corpo"], atualizada=_agora())
            self._gravar(n)
            return n

    def ligar_cards(self, nid: str, ids: List[str]) -> Dict:
        with self.lock:
            n = self._exige(nid)
            n["cards"] = list(dict.fromkeys(list(n.get("cards") or []) + list(ids)))
            self._gravar(n)
            return n

    def todas(self) -> List[Dict]:
        with self.lock:
            return [self._row(r) for r in self.db.execute("SELECT * FROM notas")]

    def etiquetas(self) -> List[str]:
        return sorted({t for n in self.todas() for t in n["etiquetas"]}, key=str.casefold)

    def listar(self, q: str = None, pasta: str = None, etiqueta: str = None) -> Dict:
        todas = self.todas()
        ns = todas
        if pasta is not None and pasta != "":
            ns = [n for n in ns if n["pasta"] == pasta]
        if etiqueta:
            ns = [n for n in ns if etiqueta.casefold() in {t.casefold() for t in n["etiquetas"]}]
        score = {}
        if q and q.strip():
            docs = [_terms(" ".join([n["titulo"]] * 2 + [n["corpo"], " ".join(n["etiquetas"])])) for n in ns]
            sc = BM25(docs).score(_terms(q)) if ns else []
            fq = _fold(q.strip())
            for n, s in zip(ns, sc):
                if fq in _fold(n["titulo"] + " " + n["corpo"]):
                    s += 1.0
                if s > 0:
                    score[n["id"]] = s
            ns = sorted([n for n in ns if n["id"] in score], key=lambda n: -score[n["id"]])
        else:
            ns = sorted(ns, key=lambda n: n["atualizada"], reverse=True)
        ns = sorted(ns, key=lambda n: not n["fixada"])            # estável: fixadas primeiro
        out = []
        for n in ns:
            d = {k: n[k] for k in ("id", "titulo", "pasta", "etiquetas", "fixada", "criada", "atualizada")}
            d["trecho"] = _trecho(n["corpo"], q)
            if n["id"] in score:
                d["score"] = round(score[n["id"]], 4)
            out.append(d)
        return {"notas": out, "pastas": sorted({n["pasta"] for n in todas if n["pasta"]}, key=str.casefold),
                "etiquetas": sorted({t for n in todas for t in n["etiquetas"]}, key=str.casefold)}

    def close(self):
        with self.lock:
            self.db.close()


def _trecho(corpo: str, q: Optional[str], n: int = 160) -> str:
    plano = " ".join(_limpa_md(l) for l in (corpo or "").splitlines() if l.strip())
    if q:
        f = _fold(plano)
        for t in _terms(q):
            i = f.find(t)
            if i >= 0:
                a = max(0, i - 50)
                return ("…" if a else "") + plano[a:a + n]
    return plano[:n]


# -------------------------------------------------------------------------------------------- análise --
def _frases(corpo: str) -> List[Dict]:
    out = []
    for i, s in enumerate(sentences(corpo or "")):
        limpa = _limpa_md(s["text"])
        if len(limpa) < 3:
            continue
        out.append(dict(s, limpa=limpa, indice=len(out)))
    return out


def _e_tarefa_regra(s: Dict) -> bool:
    if s.get("heading") or _RX_FEITA.match(s["text"]):
        return False
    return bool(_RX_TAREFA.search(s["text"]))


def _pessoas_regras(corpo: str) -> List[str]:
    out = []
    for m in _RX_PESSOA.finditer(corpo or ""):
        nome = m.group(1).strip()
        if nome.split()[0].casefold() in _NAO_NOME:
            continue
        out.append(nome)
    try:
        from djorge.runtime.recognizers import nome_do_email
        for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", corpo or ""):
            r = nome_do_email(e)
            if r.get("tipo") == "pessoa" and r.get("entidade"):
                out.append(r["entidade"])
    except Exception:
        pass
    return out


def _uniq(xs) -> List[str]:
    seen, out = set(), []
    for x in xs:
        k = _fold(x).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _datas(corpo: str, agora: _dt.datetime) -> List[Dict]:
    from djorge.runtime.recognizers import reconhecer
    out = []
    for t in reconhecer(corpo or "", agora.date(), agora):
        if t.campo in ("data", "hora") and t.valor:
            out.append({"texto": t.texto, "campo": t.campo, "valor": t.valor, "ini": t.ini, "fim": t.fim})
    return out


def _proposta(s: Dict, nota: Dict, agora: _dt.datetime, p: float, fonte: str) -> Dict:
    from djorge.runtime.recognizers import descrever_recorrencia, reconhecer, resolver_quando
    txt = s["limpa"]
    ts = reconhecer(txt, agora.date(), agora)
    q = resolver_quando(txt, ts, agora)
    titulo = txt
    for t in sorted([t for t in ts if t.campo in ("data", "hora", "período do dia", "frequência ou repetição")],
                    key=lambda t: -t.ini):                 # o título fica sem a data (ela vai para o card)
        titulo = titulo[:t.ini] + titulo[t.fim:]
    titulo = re.sub(r"(?i)^(?:todo:?|a fazer:?|preciso|tenho que|tenho de|não esquecer de|nao esquecer de|"
                    r"lembrar de)\s+", "", " ".join(titulo.split())).strip(" ,.-–:;") or txt
    titulo = titulo[:1].upper() + titulo[1:]
    card = {"kind": "lembrete" if q.get("hora") else "tarefa", "title": titulo[:200],
            "body": f"Da nota: {nota.get('titulo') or 'sem título'}\n\n{txt}", "column": "entrada",
            "tags": list(nota.get("etiquetas") or [])}
    if q.get("inicio"):
        card["due"] = q["inicio"]
        if q.get("hora"):
            card["start"] = q["inicio"]
    if q.get("recorrencia"):
        card["recurrence"] = descrever_recorrencia(q["recorrencia"])
    return {"indice": s["indice"], "frase": txt, "ini": s["start"], "fim": s["end"], "p": round(float(p), 4),
            "fonte": fonte, "quando": {k: q.get(k) for k in ("data", "hora", "inicio", "recorrencia_txt")},
            "card": card}


def analisar(nota: Dict, engine=None, etiquetas_existentes: List[str] = (), agora: _dt.datetime = None) -> Dict:
    agora = agora or _dt.datetime.now().replace(second=0, microsecond=0)
    corpo = nota.get("corpo") or ""
    frases = _frases(corpo)
    corpo_frases = [s for s in frases if not s.get("heading")]
    fonte = "regras"
    p_tarefa = {s["indice"]: (0.8 if _e_tarefa_regra(s) else 0.0) for s in frases}
    pessoas = _pessoas_regras(corpo)
    existentes = [t for t in etiquetas_existentes if t]
    etiq = [t for t in existentes if _fold(t) in _fold(corpo + " " + (nota.get("titulo") or ""))]

    if engine is not None and corpo.strip():
        try:
            from djorge.runtime.semantic import SemReq
            cands = corpo_frases[:60]
            reqs = [SemReq("NOUL", s["limpa"], options=[NOUL_TAREFA]) for s in cands]
            reqs.append(SemReq("resolve", corpo[:4000], mode="ENTITY", target="pessoa", threshold=0.5))
            com_etq = bool(existentes)
            if com_etq:
                reqs.append(SemReq("CHOICE", corpo[:4000], options=list(existentes[:15]) + [SEM_ETIQUETA],
                                   ask=ASK_ETIQUETA))
            out = engine.run(reqs)
            for s, r in zip(cands, out[:len(cands)]):
                pt = float(r.get("p_true", r.get("probability", 0)) or 0)
                if _RX_FEITA.match(s["text"]):
                    pt = 0.0                                  # já marcada como feita
                p_tarefa[s["indice"]] = max(pt, 0.55 if _e_tarefa_regra(s) and pt >= 0.35 else 0.0)
            ms = out[len(cands)]
            for m in getattr(ms, "matches", []) or []:
                if (getattr(m, "attributes", None) or {}).get("polarity") == "negated":
                    continue
                pessoas.insert(0, corpo[m.start:m.end].strip())
            if com_etq:
                dist = out[len(cands) + 1].get("distribution") or {}
                escolhidas = [o for o, p in sorted(dist.items(), key=lambda x: -x[1])
                              if o != SEM_ETIQUETA and p >= 0.3]
                etiq = escolhidas + etiq
            fonte = "modelo"
        except Exception:
            fonte = "regras"

    tarefas = [_proposta(s, nota, agora, p_tarefa[s["indice"]], fonte) for s in frases
               if p_tarefa.get(s["indice"], 0) >= 0.5]
    heads = [s for s in frases if s.get("heading")]
    resumo = []
    if corpo_frases:
        try:
            doc = Document(corpo)
            cen = doc._central([s for s in doc.sents if not s.get("heading")] or doc.sents, k=3)
            resumo = [_limpa_md(c["text"]) for c in cen if _limpa_md(c["text"])]
        except ValueError:
            resumo = [s["limpa"] for s in corpo_frases[:3]]
    if heads:
        titulo = heads[0]["limpa"]
    elif resumo:
        titulo = resumo[0]
    else:
        titulo = (corpo_frases[0]["limpa"] if corpo_frases else "")
    titulo = re.split(r"(?<=[.!?])\s", titulo)[0].rstrip(".!? ")
    if len(titulo) > 60:
        titulo = titulo[:60].rsplit(" ", 1)[0] + "…"
    return {"titulo_sugerido": titulo, "etiquetas": _uniq(etiq)[:5], "tarefas": tarefas,
            "datas": _datas(corpo, agora), "pessoas": _uniq(pessoas)[:10], "resumo": resumo, "fonte": fonte}


def perguntar(nota: Dict, q: str, engine=None, k: int = 3) -> Dict:
    """Busca extrativa na nota: as frases que melhor respondem (BM25 por frase) + resposta-trecho do modelo."""
    q = " ".join(str(q or "").split())
    if not q:
        raise ValueError("escreva a pergunta")
    frases = [s for s in _frases(nota.get("corpo") or "")]
    tq = _terms(q)
    if not frases:
        return {"q": q, "trechos": []}
    sc = BM25([_terms(s["limpa"]) for s in frases]).score(tq)
    top = sorted(range(len(frases)), key=lambda i: -sc[i])[:k]
    trechos = [{"texto": frases[i]["limpa"], "ini": frases[i]["start"], "fim": frases[i]["end"],
                "score": round(sc[i], 4)} for i in top if sc[i] > 0]
    out = {"q": q, "trechos": trechos}
    if engine is not None and trechos:
        try:
            from djorge.runtime.semantic import SemReq
            ctx = " ".join(t["texto"] for t in trechos)
            ms = engine.run([SemReq("answer", ctx, target=q)])[0]
            best = max(getattr(ms, "matches", []) or [], key=lambda m: m.probability, default=None)
            if best is not None:
                out["resposta"] = {"texto": ctx[best.start:best.end], "p": round(float(best.probability), 4)}
        except Exception:
            pass
    return out


# -------------------------------------------------------------------------------------------- rotas --
def _engine(app):
    try:
        return app.command.engine()
    except Exception:
        return None


def _agora_app(app) -> _dt.datetime:
    try:
        from .command import _now
        return _now(app.cfg)
    except Exception:
        return _dt.datetime.now().replace(second=0, microsecond=0)


def criar_tarefas(app, nid: str, b: Dict) -> Dict:
    N = app.notas
    nota = N._exige(nid)
    an = analisar(nota, _engine(app), N.etiquetas(), _agora_app(app))
    por_indice = {t["indice"]: t for t in an["tarefas"]}
    escolhidas = []
    if isinstance(b.get("tarefas"), list):                    # a UI pode mandar as propostas já editadas
        escolhidas = [t.get("card", t) for t in b["tarefas"] if isinstance(t, dict)]
    else:
        idx = b.get("indices")
        idx = list(por_indice) if idx is None else [int(i) for i in idx]
        falta = [i for i in idx if i not in por_indice]
        if falta:
            raise ValueError(f"tarefa não encontrada: {falta}")
        escolhidas = [por_indice[i]["card"] for i in idx]
    from .historico import consumir_alteracoes
    consumir_alteracoes()
    cards = [app.cards.create(c) for c in escolhidas]
    if cards:
        N.ligar_cards(nid, [c["id"] for c in cards])
        try:
            app.hist.add({"pedido": f"Tarefas da nota: {nota.get('titulo') or 'sem título'}"[:200],
                          "ferramenta": "notas", "acao": "criar_cards",
                          "alvo": {"tipo": "nota", "id": nid, "cards": [c["id"] for c in cards]},
                          "resultado": f"{len(cards)} card(s) criado(s)"})
        except Exception:
            pass
    n = len(cards)
    return {"cards": cards, "criados": n,
            "reply": "Nenhuma tarefa criada." if not n else f"Criei {n} card{'s' if n > 1 else ''} da nota."}


def rotas(app, metodo: str, path: str, corpo) -> Optional[tuple]:
    N = app.notas
    b = corpo if isinstance(corpo, dict) else {}
    parts = path.rstrip("/").split("/")                    # ['', 'api', 'notas', id, sub]
    if len(parts) == 3:
        if metodo == "GET":
            return 200, N.listar(b.get("q"), b.get("pasta"), b.get("etiqueta"))
        return 200, N.criar(b)
    nid = parts[3]
    sub = parts[4] if len(parts) > 4 else None
    if len(parts) > 5:
        return None
    if sub is None:
        if metodo == "GET":
            n = N.get(nid)
            return (200, n) if n else (404, {"error": "nota não encontrada"})
        return 200, N.atualizar(nid, b)
    if metodo == "GET":
        if sub == "versoes":
            return 200, {"versoes": N.versoes(nid)}
        return None
    if sub == "delete":
        return 200, {"ok": N.apagar(nid)}
    if sub == "versoes":
        if b.get("restaurar") is None:
            raise ValueError("diga qual versão restaurar")
        return 200, N.restaurar(nid, b["restaurar"])
    if sub == "analisar":
        nota = N._exige(nid)
        if "corpo" in b:                                     # analisar o que está na tela antes do autosave
            nota = dict(nota, corpo=str(b["corpo"] or ""), titulo=b.get("titulo", nota["titulo"]))
        return 200, analisar(nota, _engine(app), N.etiquetas(), _agora_app(app))
    if sub == "perguntar":
        return 200, perguntar(N._exige(nid), b.get("q", ""), _engine(app))
    if sub == "tarefas":
        return 200, criar_tarefas(app, nid, b)
    return None
