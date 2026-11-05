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

JSONS = ("motivos", "regras", "acoes", "sugestoes", "dados", "destaque")
COLS = ("id", "t", "fonte", "app", "titulo", "texto", "remetente", "etiqueta", "prioridade", "nota", "motivos",
        "regras", "acoes", "sugestoes", "card_id", "lida", "arquivada", "dados", "destaque", "contagem", "chave",
        "externo")
NOVAS_COLS = {"destaque": "TEXT", "contagem": "INTEGER DEFAULT 1", "chave": "TEXT", "externo": "TEXT"}
JANELA_DUP_H = 6                                        # mesma notificação dentro de 6 h = repetida


def _norm(s) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w@.]+", " ", s).split())


def chave_dup(fonte, app, titulo, texto) -> str:
    import hashlib
    return hashlib.sha1("\x1f".join(_norm(x) for x in (fonte, app, titulo, texto)).encode()).hexdigest()[:20]


def destaque_valido(d) -> Optional[Dict]:
    """None tira o destaque; True ou {cor, icone, fixar, ate} põe (estilo da lista fechada de uiprefs)."""
    if d in (None, False, "", {}):
        return None
    from .uiprefs import ate_valido, estilo_destaque
    d = d if isinstance(d, dict) else {}
    out = dict(estilo_destaque(d), ate=ate_valido(d.get("ate")))
    if d.get("origem"):
        out["origem"] = str(d["origem"])[:80]
    return out


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
        tem = {r[1] for r in self.db.execute("PRAGMA table_info(notif)")}
        for c, tipo in NOVAS_COLS.items():                # migração: colunas novas em bancos antigos
            if c not in tem:
                self.db.execute(f"ALTER TABLE notif ADD COLUMN {c} {tipo}")
        self.db.execute("CREATE INDEX IF NOT EXISTS notif_chave ON notif(chave)")
        self.db.commit()
        if int(self.db.execute("PRAGMA user_version").fetchone()[0] or 0) < 2:
            self.deduplicar()
            self.db.execute("PRAGMA user_version = 2")
            self.db.commit()

    def deduplicar(self) -> int:
        """Migração/limpeza: junta as repetidas já guardadas (mesma chave dentro da janela), somando a contagem."""
        import datetime as _dt
        with self.lock:
            rows = self.db.execute("SELECT id, t, fonte, app, titulo, texto, contagem, chave FROM notif ORDER BY t").fetchall()
            ultimo, apagar, soma = {}, [], {}
            for nid, t, fo, ap, ti, tx, cnt, ch in rows:
                k = ch or chave_dup(fo, ap, ti, tx)
                if not ch:
                    self.db.execute("UPDATE notif SET chave=? WHERE id=?", (k, nid))
                ant = ultimo.get(k)
                try:
                    perto = ant and (_dt.datetime.fromisoformat(str(t)[:19]) -
                                     _dt.datetime.fromisoformat(str(ant[1])[:19])) <= _dt.timedelta(hours=JANELA_DUP_H)
                except ValueError:
                    perto = False
                if perto:
                    apagar.append(nid)
                    soma[ant[0]] = soma.get(ant[0], int(ant[2] or 1)) + int(cnt or 1)
                    self.db.execute("UPDATE notif SET t=? WHERE id=?", (t, ant[0]))
                    ultimo[k] = (ant[0], t, soma[ant[0]])
                else:
                    ultimo[k] = (nid, t, cnt or 1)
            for nid, n in soma.items():
                self.db.execute("UPDATE notif SET contagem=? WHERE id=?", (n, nid))
            for nid in apagar:
                self.db.execute("DELETE FROM notif WHERE id=?", (nid,))
            self.db.commit()
        return len(apagar)

    def repetida(self, ev: Dict) -> Optional[Dict]:
        """A notificação já guardada que esta repete (mesmo id externo, ou mesma fonte/app/título/texto em 6 h)."""
        import datetime as _dt
        ext = str(ev.get("notif_win_id") or ev.get("external_id") or "") or None
        k = chave_dup(ev.get("source"), ev.get("app"), ev.get("title"), ev.get("text"))
        desde = (_dt.datetime.now() - _dt.timedelta(hours=JANELA_DUP_H)).strftime("%Y-%m-%dT%H:%M:%S")
        with self.lock:
            r = None
            if ext:
                r = self.db.execute(f"SELECT {','.join(COLS)} FROM notif WHERE externo=? LIMIT 1", (ext,)).fetchone()
            if r is None:
                r = self.db.execute(f"SELECT {','.join(COLS)} FROM notif WHERE chave=? AND t>=? ORDER BY t DESC LIMIT 1",
                                    (k, desde)).fetchone()
        return self._row(r) if r else None

    def _row(self, r) -> Dict:
        d = dict(zip(COLS, r))
        for k in JSONS:
            try:
                d[k] = json.loads(d[k] or ("{}" if k == "dados" else "[]"))
            except Exception:
                d[k] = {} if k == "dados" else []
        d["lida"], d["arquivada"] = bool(d["lida"]), bool(d["arquivada"])
        if d.get("destaque") in ([], {}):
            d["destaque"] = None
        d["contagem"] = int(d.get("contagem") or 1)
        return d

    def add(self, ev: Dict, **kw) -> Dict:
        """Guarda a notificação. Se ela repete uma já guardada, não cria outra: atualiza t e contagem e devolve a
        existente com "duplicada": True (quem chama não roda o secretário de novo)."""
        dup = self.repetida(ev)
        if dup is not None:
            with self.lock:
                self.db.execute("UPDATE notif SET t=?, contagem=COALESCE(contagem,1)+1 WHERE id=?",
                                (ev.get("time") or time.strftime("%Y-%m-%dT%H:%M:%S"), dup["id"]))
                self.db.commit()
            return dict(self.get(dup["id"]), duplicada=True)
        d = {"id": uuid.uuid4().hex[:12], "t": ev.get("time") or time.strftime("%Y-%m-%dT%H:%M:%S"),
             "fonte": ev.get("source") or "", "app": ev.get("app") or "", "titulo": (ev.get("title") or "")[:300],
             "texto": (ev.get("text") or "")[:4000], "remetente": ev.get("remetente") or ev.get("sender") or "",
             "etiqueta": "", "prioridade": "normal", "nota": 0, "motivos": [], "regras": [], "acoes": [],
             "sugestoes": [], "card_id": None, "lida": False, "arquivada": False, "dados": {}, "destaque": None,
             "contagem": 1, "chave": chave_dup(ev.get("source"), ev.get("app"), ev.get("title"), ev.get("text")),
             "externo": str(ev.get("notif_win_id") or ev.get("external_id") or "") or None}
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
        if "destaque" in sets:
            sets["destaque"] = destaque_valido(sets["destaque"])
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

    def destacadas(self, limit=50) -> List[Dict]:
        with self.lock:
            rows = self.db.execute(f"SELECT {','.join(COLS)} FROM notif WHERE destaque IS NOT NULL AND destaque != 'null' "
                                   "AND arquivada=0 ORDER BY t DESC LIMIT ?", (int(limit),)).fetchall()
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
