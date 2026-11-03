"""Notificações e mensagens recebidas (SQLite em HOME/notificacoes.db).

Cada item: {id, t, fonte, app, titulo, texto, remetente, etiqueta (categoria da triagem ou da pessoa), prioridade,
nota, motivos[], regras[] (automações que casaram), acoes[] (o que foi feito, com como desfazer), sugestoes[]
(o que o secretário propõe no modo sugerir), card_id, lida, arquivada, dados{} (campos extraídos)}.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

JSONS = ("motivos", "regras", "acoes", "sugestoes", "dados")
COLS = ("id", "t", "fonte", "app", "titulo", "texto", "remetente", "etiqueta", "prioridade", "nota", "motivos",
        "regras", "acoes", "sugestoes", "card_id", "lida", "arquivada", "dados")


class Notificacoes:
    def __init__(self, path, keep: int = 5000):
        self.path, self.keep = Path(path), int(keep)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS notif (id TEXT PRIMARY KEY, t TEXT, fonte TEXT, app TEXT,
            titulo TEXT, texto TEXT, remetente TEXT, etiqueta TEXT, prioridade TEXT, nota INTEGER, motivos TEXT,
            regras TEXT, acoes TEXT, sugestoes TEXT, card_id TEXT, lida INTEGER DEFAULT 0,
            arquivada INTEGER DEFAULT 0, dados TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS notif_t ON notif(t)")
        self.db.commit()

    def _row(self, r) -> Dict:
        d = dict(zip(COLS, r))
        for k in JSONS:
            try:
                d[k] = json.loads(d[k] or ("{}" if k == "dados" else "[]"))
            except Exception:
                d[k] = {} if k == "dados" else []
        d["lida"], d["arquivada"] = bool(d["lida"]), bool(d["arquivada"])
        return d

    def add(self, ev: Dict, **kw) -> Dict:
        d = {"id": uuid.uuid4().hex[:12], "t": ev.get("time") or time.strftime("%Y-%m-%dT%H:%M:%S"),
             "fonte": ev.get("source") or "", "app": ev.get("app") or "", "titulo": (ev.get("title") or "")[:300],
             "texto": (ev.get("text") or "")[:4000], "remetente": ev.get("remetente") or ev.get("sender") or "",
             "etiqueta": "", "prioridade": "normal", "nota": 0, "motivos": [], "regras": [], "acoes": [],
             "sugestoes": [], "card_id": None, "lida": False, "arquivada": False, "dados": {}}
        d.update({k: v for k, v in kw.items() if k in COLS})
        with self.lock:
            self.db.execute(f"INSERT INTO notif ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                            [json.dumps(d[k], ensure_ascii=False, default=str) if k in JSONS else
                             (int(d[k]) if k in ("lida", "arquivada") else d[k]) for k in COLS])
            n = self.db.execute("SELECT COUNT(*) FROM notif").fetchone()[0]
            if n > self.keep:
                self.db.execute("DELETE FROM notif WHERE id IN (SELECT id FROM notif ORDER BY t LIMIT ?)",
                                (n - self.keep,))
            self.db.commit()
        return d

    def get(self, nid: str) -> Optional[Dict]:
        with self.lock:
            r = self.db.execute(f"SELECT {','.join(COLS)} FROM notif WHERE id=?", (nid,)).fetchone()
        return self._row(r) if r else None

    def update(self, nid: str, **kw) -> Dict:
        cur = self.get(nid)
        if not cur:
            raise KeyError("notificação não encontrada")
        sets = {k: v for k, v in kw.items() if k in COLS and k != "id"}
        if not sets:
            return cur
        vals = [json.dumps(v, ensure_ascii=False, default=str) if k in JSONS else
                (int(bool(v)) if k in ("lida", "arquivada") else v) for k, v in sets.items()]
        with self.lock:
            self.db.execute(f"UPDATE notif SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", vals + [nid])
            self.db.commit()
        return self.get(nid)

    def list(self, etiqueta=None, q=None, app=None, lida=None, arquivada=False, fonte=None, limit=200,
             desde=None) -> List[Dict]:
        w, a = [], []
        if etiqueta:
            w.append("etiqueta=?"); a.append(etiqueta)
        if app:
            w.append("LOWER(app) LIKE ?"); a.append(f"%{str(app).lower()}%")
        if fonte:
            w.append("fonte=?"); a.append(fonte)
        if lida is not None and lida != "":
            w.append("lida=?"); a.append(1 if str(lida).lower() in ("1", "true", "sim", "yes") else 0)
        if arquivada is not None and arquivada != "todas":
            w.append("arquivada=?"); a.append(1 if str(arquivada).lower() in ("1", "true", "sim") else 0)
        if desde:
            w.append("t>=?"); a.append(desde)
        if q:
            w.append("(LOWER(titulo) LIKE ? OR LOWER(texto) LIKE ? OR LOWER(remetente) LIKE ?)")
            a += [f"%{str(q).lower()}%"] * 3
        sql = f"SELECT {','.join(COLS)} FROM notif" + (" WHERE " + " AND ".join(w) if w else "") + \
              " ORDER BY t DESC LIMIT ?"
        with self.lock:
            rows = self.db.execute(sql, a + [int(limit)]).fetchall()
        return [self._row(r) for r in rows]

    def etiquetas(self) -> List[Dict]:
        with self.lock:
            rows = self.db.execute("SELECT etiqueta, COUNT(*), SUM(1 - lida) FROM notif WHERE arquivada=0 "
                                   "GROUP BY etiqueta ORDER BY COUNT(*) DESC").fetchall()
        return [{"etiqueta": e or "sem etiqueta", "n": n, "nao_lidas": int(u or 0)} for e, n, u in rows]

    def limpar(self, antes_de: str = None, ids=None) -> int:
        with self.lock:
            if ids:
                ids = [str(i) for i in ids]
                c = self.db.execute(f"DELETE FROM notif WHERE id IN ({','.join('?' * len(ids))})", ids)
            elif antes_de:
                c = self.db.execute("DELETE FROM notif WHERE t < ?", (str(antes_de),))
            else:
                c = self.db.execute("DELETE FROM notif")
            self.db.commit()
            return c.rowcount

    def contar(self, desde: str = None) -> Dict:
        with self.lock:
            w, a = ("WHERE t>=?", [desde]) if desde else ("", [])
            tot, nl, imp = self.db.execute(
                f"SELECT COUNT(*), SUM(1 - lida), SUM(CASE WHEN prioridade IN ('alta','urgente') THEN 1 ELSE 0 END) "
                f"FROM notif {w}", a).fetchone()
        return {"total": tot or 0, "nao_lidas": int(nl or 0), "importantes": int(imp or 0)}

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass
