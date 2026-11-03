"""Busca unificada: cards, histórico e integrações conectadas (Gmail/Outlook via integrations.search).

BM25 de djorge.runtime.hag, realce de termos, entidades e datas (regras de recognizers) e, com o modelo carregado,
reordenação por NOUL "este item responde a busca?". O modelo só pontua; nada de texto é gerado.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from djorge.runtime.hag import BM25, _fold, _terms, sentences
from djorge.runtime.recognizers import reconhecer

SCOPES = ("all", "cards", "history", "integrations", "web")
ASK = "Este item responde a busca."


def _snippet(text: str, q_terms: List[str], width: int = 220) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= width:
        return text
    best, bs = None, -1
    for s in sentences(text):
        n = sum(1 for t in _terms(s["text"]) if t in q_terms)
        if n > bs:
            best, bs = s, n
    if not best:
        return text[:width] + "…"
    a = max(0, best["start"] - 40)
    out = text[a:a + width]
    return ("…" if a else "") + out + ("…" if a + width < len(text) else "")


def highlights(snippet: str, q_terms: List[str]) -> List[Dict]:
    """Trechos para realçar: termos da busca, entidades, datas, horas e afins (com o valor normalizado)."""
    out = []
    import re
    for m in re.finditer(r"[^\W_]+", snippet):
        if _fold(m.group()) in q_terms:
            out.append({"ini": m.start(), "fim": m.end(), "campo": "termo", "texto": m.group()})
    for t in reconhecer(snippet):
        out.append({"ini": t.ini, "fim": t.fim, "campo": t.campo, "texto": t.texto, "valor": t.valor})
    return sorted(out, key=lambda h: h["ini"])


class Search:
    def __init__(self, app):
        self.app = app
        self.web_transporte = None                     # testes injetam um transporte falso (djorge_local.web)

    def _web(self, q: str, limit: int, avisos: List[str]) -> List[Dict]:
        """Resultados da internet (sai do computador) no mesmo formato dos locais."""
        try:
            from . import web
            r = web.buscar(q, min(limit, 8), transporte=self.web_transporte)
        except Exception as e:
            avisos.append(f"web: {type(e).__name__}: {e}"[:200])
            return []
        if r.get("erro"):
            avisos.append(r["erro"])
        qt = _terms(q)
        out = []
        for i, x in enumerate(r.get("resultados") or []):
            snip = x.get("trecho") or ""
            out.append({"id": f"w{i}", "source": "web", "title": x.get("titulo"), "snippet": snip,
                        "highlights": highlights(snip, qt), "score": round(1 - i * 0.05, 4), "bm25": 0.0,
                        "date": None, "url": x.get("url"), "fonte": x.get("fonte"), "sai_do_computador": True})
        return out

    def _docs(self, scope: str, q: str) -> (List[Dict], List[str]):
        docs, avisos = [], []
        if scope in ("all", "cards"):
            for c in self.app.cards.list(limit=5000):
                text = " ".join([c["title"], c["body"], " ".join(c["tags"]),
                                 " ".join(str(p.get("nome") or "") for p in c["people"]),
                                 " ".join(x["text"] for x in c["checklist"])])
                docs.append({"id": c["id"], "source": "cards", "title": c["title"] or c["body"][:60], "text": text,
                             "date": c["start"] or c["due"], "card": c, "url": (c.get("source") or {}).get("url")})
        if scope in ("all", "history"):
            for i, h in enumerate(self.app.hist.last(1000)):
                ev = h.get("event") or {}
                if h.get("type") == "command":
                    text, title = h.get("text", ""), h.get("reply", "")[:80]
                else:
                    text = " ".join(str(ev.get(k) or "") for k in ("title", "text", "sender", "app"))
                    title = ev.get("title") or ev.get("sender") or ev.get("source") or "evento"
                if text.strip():
                    docs.append({"id": f"h{i}", "source": "history", "title": title, "text": text, "date": h.get("t")})
        if scope in ("all", "integrations"):
            integ = getattr(self.app, "integrations", None)
            if integ is not None and hasattr(integ, "search"):
                try:
                    for i, r in enumerate(integ.search(q) or []):
                        r = r if isinstance(r, dict) else {"text": str(r)}
                        text = " ".join(str(r.get(k) or "") for k in ("title", "subject", "snippet", "text", "body", "from"))
                        docs.append({"id": r.get("id") or f"i{i}", "source": r.get("integration") or r.get("source") or "integracao",
                                     "title": r.get("title") or r.get("subject") or text[:60], "text": text,
                                     "date": r.get("date"), "url": r.get("url"), "external": True})
                except Exception as e:
                    avisos.append(f"integrações: {type(e).__name__}: {e}"[:200])
        return docs, avisos

    def search(self, q: str, scope: str = "all", limit: int = 20, engine=None, web=False) -> Dict:
        """web: False (padrão, nada sai do computador) | True (junta a internet) | "auto" (só se nada local).
        scope="web" busca só na internet."""
        q = " ".join(str(q or "").split())[:300]
        if scope not in SCOPES:
            raise ValueError(f"scope: use {', '.join(SCOPES)}")
        if not q:
            return {"q": q, "results": [], "avisos": []}
        if scope == "web":
            avisos = []
            res = self._web(q, limit, avisos)
            return {"q": q, "results": res[:limit], "avisos": avisos, "web": True}
        r = self._local(q, scope, limit, engine)
        if web is True or (web == "auto" and not r["results"]):
            extra = self._web(q, limit, r["avisos"])
            if extra:
                r["results"] = (r["results"] + extra)[:limit]
                r["web"] = True
        return r

    def _local(self, q: str, scope: str, limit: int, engine) -> Dict:
        docs, avisos = self._docs(scope, q)
        qt = _terms(q)
        datas = {t.valor for t in reconhecer(q) if t.campo == "data" and t.valor}
        if not docs:
            return {"q": q, "results": [], "avisos": avisos}
        bm = BM25([_terms(d["text"]) for d in docs]).score(qt) if qt else [0.0] * len(docs)
        ql = _fold(q)
        scored = []
        for d, s in zip(docs, bm):
            if ql and ql in _fold(d["text"]):
                s += 2.0                                       # frase exata
            if datas and d.get("date") and str(d["date"])[:10] in datas:
                s += 3.0                                       # a data pedida
            if d.get("external") and s == 0 and qt:
                s = 0.5                                        # a integração já filtrou pela busca
            if s > 0:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        scored = scored[:max(limit * 2, limit)]
        top = max([s for s, _ in scored] or [1.0])
        res = []
        for s, d in scored:
            snip = _snippet(d["text"], qt)
            r = {"id": d["id"], "source": d["source"], "title": d["title"], "snippet": snip,
                 "highlights": highlights(snip, qt), "score": round(s / top, 4), "bm25": round(s, 4),
                 "date": d.get("date"), "url": d.get("url")}
            if d.get("card"):
                r["card"] = d["card"]
            res.append(r)
        if engine is not None and res:
            try:
                from djorge.runtime.semantic import SemReq
                out = engine.run([SemReq("NOUL", f"Busca: {q}\nItem: {r['title']}. {r['snippet']}"[:1200], options=[ASK])
                                  for r in res])
                for r, o in zip(res, out):
                    p = float(o.get("p_true", o.get("probability", 0)))
                    r["p_modelo"] = round(p, 4)
                    r["score"] = round(0.5 * r["score"] + 0.5 * p, 4)
                res.sort(key=lambda r: -r["score"])
            except Exception as e:
                avisos.append(f"modelo: {type(e).__name__}: {e}"[:200])
        return {"q": q, "results": res[:limit], "avisos": avisos, "reordenado": engine is not None}
