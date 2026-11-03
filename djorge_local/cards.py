"""Cards (tarefas, eventos, lembretes, notas, listas, mensagens) guardados em SQLite (HOME/cards.db).

Cada card segue o contrato de docs/V2_SPEC.md. As colunas usadas em filtros e ordenação ficam em colunas próprias;
listas e objetos (tags, pessoas, checklist, origem) ficam em JSON. Só biblioteca padrão.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Dict, List, Optional

KINDS = ("tarefa", "evento", "lembrete", "nota", "lista", "mensagem")
COLUMNS = ("entrada", "hoje", "fazendo", "aguardando", "feito")
FIELDS = ("id", "kind", "title", "body", "column", "start", "end", "due", "all_day", "priority", "tags", "people",
          "checklist", "recurrence", "color", "pinned", "done", "source", "order", "created", "updated")
_JSON = ("tags", "people", "checklist", "source")
_SQLCOL = {"column": "col", "order": "ord", "end": "end_", "start": "start_"}   # nomes reservados no SQL


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


NOME_CAMPO = {"due": "prazo", "start": "início", "end": "fim", "from": "de", "to": "até"}


class DataInvalida(ValueError):
    """Data que não deu para entender. A mensagem já é amigável e traz `dica` com exemplos."""

    def __init__(self, campo: str, valor):
        self.campo, self.valor = campo, valor
        self.dica = "Tente “2026-11-03 14:00”, “03/11/2026 14h” ou “amanhã 9h”."
        super().__init__(f"Não entendi a data “{str(valor)[:60]}” em {NOME_CAMPO.get(campo, campo)}. {self.dica}")


def _iso(v, campo) -> Optional[str]:
    """Aceita ISO 8601 e formatos humanos ("2026-11-03 14:00", "03/11/2026 14h", "amanhã 9h", "sexta 15h") usando
    os reconhecedores; devolve a forma ISO normalizada ou None. Valor impossível -> DataInvalida (mensagem amigável)."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    try:
        if len(s) == 10 and s[4] == "-":
            return _dt.date.fromisoformat(s).isoformat()
        d = _dt.datetime.fromisoformat(s.replace(" ", "T", 1).replace("Z", "+00:00"))
        return d.replace(tzinfo=None).isoformat(timespec="minutes" if d.second == 0 else "seconds")
    except (ValueError, IndexError):
        pass
    try:
        from djorge.runtime.recognizers import data_humana
        r = data_humana(s)
    except Exception:
        r = None
    if not r:
        raise DataInvalida(campo, v)
    return r


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().casefold() in ("1", "true", "sim", "s", "yes", "on")
    return bool(v)


def normalize(data: Dict, base: Optional[Dict] = None) -> Dict:
    """Valida e completa um card (parcial sobre `base`). Levanta ValueError com mensagem em português."""
    c = dict(base or {})
    if not isinstance(data, dict):
        raise ValueError("card: objeto esperado")
    for k, v in data.items():
        if k in ("id", "created", "updated"):
            continue
        if k not in FIELDS:
            continue                                      # campos desconhecidos são ignorados
        if k == "kind":
            if v not in KINDS:
                raise ValueError(f"kind: use {', '.join(KINDS)}")
        elif k == "column":
            if v not in COLUMNS:
                raise ValueError(f"column: use {', '.join(COLUMNS)}")
        elif k in ("title", "body", "recurrence", "color"):
            v = "" if v is None else str(v)
            v = v[:200] if k == "title" else v[:20000] if k == "body" else v[:120]
        elif k in ("start", "end", "due"):
            v = _iso(v, k)
        elif k in ("all_day", "pinned", "done"):
            v = _bool(v)
        elif k == "priority":
            try:
                v = max(0, min(3, int(v or 0)))
            except (TypeError, ValueError):
                raise ValueError("priority: número de 0 a 3")
        elif k == "order":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError("order: número esperado")
        elif k == "tags":
            if isinstance(v, str):
                v = [x.strip() for x in v.split(",")]
            if not isinstance(v, list):
                raise ValueError("tags: lista esperada")
            v = list(dict.fromkeys(str(x).strip().lstrip("#")[:40] for x in v if str(x).strip()))[:30]
        elif k == "people":
            if not isinstance(v, list):
                raise ValueError("people: lista esperada")
            out = []
            for p in v[:30]:
                if isinstance(p, str):
                    p = {"nome": p}
                if not isinstance(p, dict):
                    raise ValueError("people: itens {nome, tipo, email}")
                out.append({"nome": str(p.get("nome") or "")[:120] or None, "tipo": p.get("tipo") or None,
                            "email": (str(p["email"]).lower()[:200] if p.get("email") else None)})
            v = out
        elif k == "checklist":
            if not isinstance(v, list):
                raise ValueError("checklist: lista esperada")
            v = [{"text": str(x.get("text") if isinstance(x, dict) else x)[:300],
                  "done": _bool(x.get("done")) if isinstance(x, dict) else False} for x in v[:200]]
        elif k == "source":
            if v is None:
                v = {}
            if not isinstance(v, dict):
                raise ValueError("source: objeto {integration, external_id, url}")
            v = {kk: (str(v[kk])[:500] if v.get(kk) is not None else None)
                 for kk in ("integration", "external_id", "url")}
        c[k] = v
    c.setdefault("kind", "tarefa"); c.setdefault("title", ""); c.setdefault("body", "")
    c.setdefault("column", "feito" if c.get("done") else "entrada")
    for k in ("start", "end", "due"):
        c.setdefault(k, None)
    c.setdefault("all_day", False); c.setdefault("priority", 0); c.setdefault("tags", [])
    c.setdefault("people", []); c.setdefault("checklist", []); c.setdefault("recurrence", "")
    c.setdefault("color", ""); c.setdefault("pinned", False); c.setdefault("done", False)
    c.setdefault("source", {"integration": None, "external_id": None, "url": None})
    if "done" in data and "column" not in data:          # marcar feito leva para a coluna "feito"
        if c["done"]:
            c["column"] = "feito"
        elif c["column"] == "feito":
            c["column"] = "entrada"
    elif "column" in data and "done" not in data:
        c["done"] = c["column"] == "feito"
    if not c["title"].strip() and not c["body"].strip() and not c["checklist"]:
        raise ValueError("o card precisa de um título")
    return c


class Cards:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cards (
              id TEXT PRIMARY KEY, kind TEXT, title TEXT, body TEXT, col TEXT, start_ TEXT, end_ TEXT, due TEXT,
              all_day INTEGER, priority INTEGER, tags TEXT, people TEXT, checklist TEXT, recurrence TEXT, color TEXT,
              pinned INTEGER, done INTEGER, source TEXT, ord REAL, created TEXT, updated TEXT,
              src_int TEXT, src_ext TEXT);
            CREATE INDEX IF NOT EXISTS cards_col ON cards(col, ord);
            CREATE INDEX IF NOT EXISTS cards_when ON cards(start_, due);
            CREATE INDEX IF NOT EXISTS cards_src ON cards(src_int, src_ext);
            """)
            self.db.commit()

    # ------------------------------------------------------------------ básico --
    def _row(self, r) -> Dict:
        c = {}
        for k in FIELDS:
            v = r[_SQLCOL.get(k, k)]
            if k in _JSON:
                v = json.loads(v or ("{}" if k == "source" else "[]"))
            elif k in ("all_day", "pinned", "done"):
                v = bool(v)
            c[k] = v
        return c

    def _write(self, c: Dict):
        vals = []
        for k in FIELDS:
            v = c[k]
            vals.append(json.dumps(v, ensure_ascii=False) if k in _JSON else int(v) if isinstance(v, bool) else v)
        src = c.get("source") or {}
        cols = [_SQLCOL.get(k, k) for k in FIELDS] + ["src_int", "src_ext"]
        self.db.execute(f"INSERT OR REPLACE INTO cards ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        vals + [src.get("integration"), src.get("external_id")])
        self.db.commit()

    def get(self, cid: str) -> Optional[Dict]:
        with self.lock:
            r = self.db.execute("SELECT * FROM cards WHERE id=?", (str(cid),)).fetchone()
        return self._row(r) if r else None

    def _next_order(self, column: str) -> float:
        r = self.db.execute("SELECT MAX(ord) FROM cards WHERE col=?", (column,)).fetchone()
        return float((r[0] or 0) + 1)

    def create(self, data: Dict) -> Dict:
        with self.lock:
            c = normalize(data)
            c["id"] = uuid.uuid4().hex[:12]
            c["created"] = c["updated"] = _now()
            if "order" not in data:
                c["order"] = self._next_order(c["column"])
            self._write(c)
            return c

    def update(self, cid: str, data: Dict) -> Dict:
        with self.lock:
            cur = self.get(cid)
            if not cur:
                raise KeyError(f"card não encontrado: {cid}")
            c = normalize(data, cur)
            c["id"], c["created"], c["updated"] = cur["id"], cur["created"], _now()
            if "column" in data and data["column"] != cur["column"] and "order" not in data:
                c["order"] = self._next_order(c["column"])
            self._write(c)
            return c

    def delete(self, cid: str) -> bool:
        with self.lock:
            n = self.db.execute("DELETE FROM cards WHERE id=?", (str(cid),)).rowcount
            self.db.commit()
        if not n:
            raise KeyError(f"card não encontrado: {cid}")
        return True

    def by_source(self, integration: str, external_id: str) -> Optional[Dict]:
        with self.lock:
            r = self.db.execute("SELECT * FROM cards WHERE src_int=? AND src_ext=?",
                                (integration, str(external_id))).fetchone()
        return self._row(r) if r else None

    def upsert_source(self, data: Dict) -> (Dict, bool):
        """Para as integrações: cria ou atualiza pelo par (source.integration, source.external_id)."""
        s = data.get("source") or {}
        cur = self.by_source(s.get("integration"), s.get("external_id")) if s.get("external_id") else None
        if cur:
            return self.update(cur["id"], data), False
        return self.create(data), True

    def get_external(self, integration: str, external_id: str) -> Optional[Dict]:
        return self.by_source(integration, external_id)

    def upsert_external(self, integration: str, external_id: str, fields: Dict) -> Dict:
        """Cria ou atualiza o card vindo de uma integração. Na atualização, a organização feita pela pessoa
        (column, order, pinned, color) é preservada."""
        data = dict(fields or {})
        src = dict(data.get("source") or {})
        src.update(integration=integration, external_id=str(external_id))
        data["source"] = src
        cur = self.by_source(integration, str(external_id))
        if cur:
            for k in ("column", "order", "pinned", "color"):
                data.pop(k, None)
            if "done" in data:                               # sem mexer na coluna escolhida
                c = normalize({k: v for k, v in data.items() if k != "done"}, cur)
                c["done"] = bool(data["done"])
                c["id"], c["created"], c["updated"] = cur["id"], cur["created"], _now()
                with self.lock:
                    self._write(c)
                return c
            return self.update(cur["id"], data)
        return self.create(data)

    # ------------------------------------------------------------------ mover / reordenar --
    def move(self, cid: str, column: str, order=None, start=None) -> Dict:
        """Arrastar no kanban (column + order) ou na agenda (start). `order` pode ser fracionário (entre dois)."""
        with self.lock:
            cur = self.get(cid)
            if not cur:
                raise KeyError(f"card não encontrado: {cid}")
            patch = {"column": column or cur["column"]}
            patch["order"] = float(order) if order is not None else self._next_order(patch["column"])
            if start is not None:
                new = _iso(start, "start")
                patch["start"] = new
                if cur.get("start") and cur.get("end") and new:     # mantém a duração do evento
                    try:
                        a = _dt.datetime.fromisoformat(cur["start"] if "T" in cur["start"] else cur["start"] + "T00:00")
                        b = _dt.datetime.fromisoformat(cur["end"] if "T" in cur["end"] else cur["end"] + "T00:00")
                        n = _dt.datetime.fromisoformat(new if "T" in new else new + "T00:00")
                        e = n + (b - a)
                        patch["end"] = e.date().isoformat() if len(new) == 10 else e.isoformat(timespec="minutes")
                    except ValueError:
                        pass
                elif cur.get("due") and not cur.get("start"):
                    patch["due"], patch["start"] = new, None
            if "done" not in patch:
                patch["done"] = patch["column"] == "feito"
            return self.update(cid, patch)

    def reorder(self, column: str, ids: List[str]) -> List[Dict]:
        """Grava a ordem completa de uma coluna (1, 2, 3...)."""
        return [self.move(i, column, order=n + 1) for n, i in enumerate(ids)]

    # ------------------------------------------------------------------ consultas --
    def list(self, q=None, kind=None, column=None, tag=None, frm=None, to=None, source=None, priority=None,
             done=None, person=None, limit: int = 2000) -> List[Dict]:
        sql, args = ["SELECT * FROM cards WHERE 1=1"], []
        if kind:
            ks = [k for k in str(kind).split(",") if k]
            sql.append(f"AND kind IN ({','.join('?' * len(ks))})"); args += ks
        if column:
            cs = [k for k in str(column).split(",") if k]
            sql.append(f"AND col IN ({','.join('?' * len(cs))})"); args += cs
        if priority not in (None, ""):
            p = str(priority)
            if p.endswith("+"):
                sql.append("AND priority >= ?"); args.append(int(p[:-1]))
            else:
                sql.append("AND priority = ?"); args.append(int(p))
        if done not in (None, ""):
            sql.append("AND done = ?"); args.append(int(_bool(done)))
        if source:
            if source in ("local", "app"):
                sql.append("AND (src_int IS NULL OR src_int = '')")
            else:
                sql.append("AND src_int = ?"); args.append(source)
        f, t = _iso(frm, "from"), _iso(to, "to")
        if f or t:
            sql.append("AND COALESCE(start_, due) IS NOT NULL")
            if t:
                sql.append("AND substr(COALESCE(start_, due),1,?) <= ?"); args += [len(t), t]
            if f:
                sql.append("AND substr(COALESCE(end_, start_, due),1,?) >= ?"); args += [len(f), f]
        sql.append("ORDER BY pinned DESC, col, ord, created")
        with self.lock:
            rows = [self._row(r) for r in self.db.execute(" ".join(sql), args).fetchall()]
        if tag:
            tg = _fold(str(tag).lstrip("#"))
            rows = [c for c in rows if any(_fold(x) == tg for x in c["tags"])]
        if person:
            pf = _fold(person)
            rows = [c for c in rows if any(pf in _fold(p.get("nome")) or pf in _fold(p.get("email"))
                                           for p in c["people"])]
        if q:
            terms = _fold(q).split()
            def hay(c):
                return _fold(" ".join([c["title"], c["body"], " ".join(c["tags"]),
                                       " ".join(str(p.get("nome") or "") for p in c["people"]),
                                       " ".join(x["text"] for x in c["checklist"])]))
            rows = [c for c in rows if all(w in hay(c) for w in terms)]
        return rows[:limit]

    def agenda(self, frm=None, to=None) -> List[Dict]:
        """Cards com data no intervalo (eventos, lembretes, tarefas com prazo), em ordem de data."""
        if not frm and not to:
            today = _dt.date.today()
            frm, to = today.isoformat(), (today + _dt.timedelta(7)).isoformat()
        rows = self.list(frm=frm, to=to)
        return sorted(rows, key=lambda c: (c["start"] or c["due"] or "", c["order"]))

    def all(self) -> List[Dict]:
        return self.list()

    def close(self):
        with self.lock:
            self.db.close()
