"""Alertas do Djorge (V2_SPEC_4): lembretes que disparam toast do Windows e aviso no app.

Formato de um alerta (no card, campo `alertas`, e no banco HOME/alertas.db):
  quando   ISO "2026-11-06T09:00"            (ou)  antes: minutos antes do início/prazo do card
  repetir  recorrência normalizada de recognizers.recorrencia: {"freq": diaria|semanal|dias_uteis|mensal|anual|
           horaria|minutos, "intervalo": N, "dias_semana": [...], "hora": "09:00", ...}
  ate      ISO (até a data X; data sem hora = o dia inteiro)
  janela   {"inicio": "08:00", "fim": "18:00"} (só dispara dentro desse período; "de 2 em 2 horas entre 8h e 18h")
  canal    ["toast", "app"]
Estado guardado junto: id, card_id, titulo, msg, ref (início/prazo do card, para `antes`), ativo, estado
(ativo|adiado|concluido|desativado), proximo, ultimo, disparos.

O agendador é uma thread que só existe enquanto houver alerta ativo; o relógio é injetável (`agora`), e `tick()`
pode ser chamado direto (testes). Rotas: `rotas(app, metodo, caminho, corpo)`; comandos: `de_texto(texto, agora)`.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

FREQS = ("diaria", "semanal", "dias_uteis", "mensal", "anual", "horaria", "minutos")
CANAIS = ("toast", "app")
ADIAR = (5, 15, 60)
_HHMM = re.compile(r"^([01]?\d|2[0-3])(?::|h)?([0-5]\d)?$")


# ------------------------------------------------------------------------------------------ formato --
def _iso(dt: _dt.datetime) -> str:
    return dt.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def _dt_de(v, fim_do_dia=False) -> Optional[_dt.datetime]:
    if v in (None, ""):
        return None
    if isinstance(v, _dt.datetime):
        return v.replace(tzinfo=None)
    s = str(v).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            d = _dt.date.fromisoformat(s)
            return _dt.datetime(d.year, d.month, d.day, 23, 59) if fim_do_dia else _dt.datetime(d.year, d.month, d.day)
        return _dt.datetime.fromisoformat(s.replace(" ", "T", 1).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    from djorge.runtime.recognizers import data_humana
    r = data_humana(s)
    if not r:
        raise ValueError(f"não entendi a data “{s[:60]}”. Tente “2026-11-06 09:00” ou “amanhã 9h”.")
    return _dt_de(r, fim_do_dia)


def _hora(v) -> Optional[str]:
    m = _HHMM.match(str(v or "").strip().lower())
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m else None


def _minutos(v) -> int:
    if isinstance(v, (int, float)):
        return max(0, int(v))
    s = str(v).strip().lower()
    m = re.match(r"^(\d+|meia)\s*(h|hora|horas|min|minutos?|m)?", s)
    if not m:
        raise ValueError("antes: minutos (ex.: 30)")
    n = 30 if m.group(1) == "meia" else int(m.group(1))
    return n * 60 if (m.group(2) or "").startswith("h") and m.group(1) != "meia" else n


def _repetir(v) -> Optional[Dict]:
    if v in (None, "", False):
        return None
    if isinstance(v, str):
        from djorge.runtime.recognizers import recorrencia
        r = recorrencia(v)
        if not r:
            raise ValueError(f"repetir: não entendi “{v[:60]}” (ex.: todo dia, toda segunda, a cada 2 horas)")
        return r
    if not isinstance(v, dict) or v.get("freq") not in FREQS:
        raise ValueError(f"repetir.freq: use {', '.join(FREQS)}")
    r = {"freq": v["freq"]}
    if v.get("intervalo"):
        r["intervalo"] = max(1, int(v["intervalo"]))
    for k in ("dias_semana",):
        if v.get(k):
            r[k] = sorted({int(x) % 7 for x in v[k]})
    for k in ("dia", "mes", "ordinal"):
        if v.get(k) not in (None, ""):
            r[k] = int(v[k])
    if v.get("ultimo_dia"):
        r["ultimo_dia"] = True
    if v.get("hora") and _hora(v["hora"]):
        r["hora"] = _hora(v["hora"])
    return r


def _janela(v) -> Optional[Dict]:
    if v in (None, "", False):
        return None
    if isinstance(v, str):
        a, _, b = v.partition("-")
        v = {"inicio": a, "fim": b}
    ini, fim = _hora((v or {}).get("inicio")), _hora((v or {}).get("fim"))
    if not ini or not fim:
        raise ValueError("janela: {inicio: \"08:00\", fim: \"18:00\"}")
    return {"inicio": ini, "fim": fim}


def normalizar(a: Dict) -> Dict:
    """Valida um alerta (formato do contrato) e devolve a forma normalizada (só os campos do contrato + id)."""
    if not isinstance(a, dict):
        raise ValueError("alerta: objeto {quando|antes, repetir, ate, janela, canal}")
    out = {"id": str(a.get("id") or uuid.uuid4().hex[:12])[:40]}
    q = _dt_de(a.get("quando"))
    out["quando"] = _iso(q) if q else None
    out["antes"] = _minutos(a["antes"]) if a.get("antes") not in (None, "") else None
    out["repetir"] = _repetir(a.get("repetir"))
    ate = _dt_de(a.get("ate"), fim_do_dia=True)
    out["ate"] = _iso(ate) if ate else None
    out["janela"] = _janela(a.get("janela"))
    canal = a.get("canal") or list(CANAIS)
    if isinstance(canal, str):
        canal = [x.strip() for x in canal.split(",")]
    canal = [c for c in canal if c in CANAIS]
    if not canal:
        raise ValueError("canal: toast e/ou app")
    out["canal"] = canal
    if not out["quando"] and out["antes"] is None and not out["repetir"]:
        raise ValueError("alerta sem hora: diga quando (ou quantos minutos antes)")
    for k in ("titulo", "msg"):
        if a.get(k):
            out[k] = str(a[k])[:200 if k == "titulo" else 400]
    return out


def normalizar_lista(v) -> List[Dict]:
    """Campo `alertas` de um card: None -> [], um alerta -> [alerta], lista -> lista normalizada (até 20)."""
    if v in (None, "", []):
        return []
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        raise ValueError("alertas: lista de {quando|antes, repetir, ate, janela, canal}")
    return [normalizar(x) for x in v[:20]]


def descrever(a: Dict) -> str:
    """'todo dia às 09:00 até sex., 6 de nov.' / 'qui., 5 de nov. às 15:00' / '30 min antes'."""
    from djorge.runtime.recognizers import descrever_recorrencia
    from .secretario import data_fmt
    partes = []
    if a.get("repetir"):
        partes.append(descrever_recorrencia(a["repetir"]))
    elif a.get("quando"):
        partes.append(data_fmt(a["quando"][:10], a["quando"][11:16] or None))
    if a.get("antes") is not None:
        partes.append(f"{a['antes']} min antes" if a["antes"] < 60 or a["antes"] % 60 else f"{a['antes'] // 60} h antes")
    if a.get("janela"):
        partes.append(f"entre {a['janela']['inicio']} e {a['janela']['fim']}")
    if a.get("ate"):
        partes.append("até " + data_fmt(a["ate"][:10]))
    return " ".join(p for p in partes if p)


# ------------------------------------------------------------------------------------------ cálculo --
def _base(a: Dict) -> Optional[_dt.datetime]:
    if a.get("antes") is not None and a.get("ref"):
        return _dt_de(a["ref"]) - _dt.timedelta(minutes=int(a["antes"]))
    return _dt_de(a.get("quando"))


def _passo(rep: Dict) -> Optional[_dt.timedelta]:
    n = int(rep.get("intervalo") or 1)
    if rep["freq"] == "horaria":
        return _dt.timedelta(hours=n)
    if rep["freq"] == "minutos":
        return _dt.timedelta(minutes=n)
    return None


def _na_janela(t: _dt.datetime, jan: Optional[Dict]) -> bool:
    if not jan:
        return True
    h = t.strftime("%H:%M")
    ini, fim = jan["inicio"], jan["fim"]
    return (ini <= h <= fim) if ini <= fim else (h >= ini or h <= fim)


def calcular_proximo(a: Dict, depois: _dt.datetime, primeiro: bool = False) -> Optional[_dt.datetime]:
    """Próximo disparo estritamente depois de `depois` (ou, em `primeiro`, o próprio início se ainda não disparou,
    mesmo atrasado). None = acabou (passou de `ate` ou alerta único já disparado)."""
    base, rep, jan = _base(a), a.get("repetir"), a.get("janela")
    ate = _dt_de(a.get("ate"), fim_do_dia=True)
    nx = None
    if not rep:
        nx = base if (primeiro and base) else None
    else:
        passo = _passo(rep)
        if passo:
            if jan:                                            # grade do dia: inicio, inicio+passo, ... <= fim
                ini = [int(x) for x in jan["inicio"].split(":")]
                for i in range(0, 400):
                    d = depois.date() + _dt.timedelta(i)
                    t = _dt.datetime(d.year, d.month, d.day, ini[0], ini[1])
                    if base and primeiro and base > depois and i == 0:
                        t = max(t, base) if _na_janela(base, jan) else t
                    fim_d = t.replace(hour=int(jan["fim"][:2]), minute=int(jan["fim"][3:]))
                    if fim_d < t:
                        fim_d += _dt.timedelta(1)
                    while t <= fim_d:
                        if t > depois or (primeiro and t == depois):
                            nx = t
                            break
                        t += passo
                    if nx:
                        break
            else:
                anc = base or depois
                if primeiro and anc >= depois:
                    nx = anc
                else:
                    k = int((depois - anc) / passo) + 1
                    nx = anc + k * passo
        else:
            from djorge.runtime.recognizers import proxima
            r = dict(rep)
            if not r.get("hora"):
                r["hora"] = base.strftime("%H:%M") if base else "09:00"
            ref = depois
            if primeiro and base and base > depois:
                ref = base - _dt.timedelta(minutes=1)
            nx = proxima(r, ref)
            if nx and jan and not _na_janela(nx, jan):
                nx = None
    if nx and ate and nx > ate:
        return None
    return nx


# ---------------------------------------------------------------------------------------- serviço --
class Alertas:
    def __init__(self, path, agora: Callable[[], _dt.datetime] = None, mostrar: Callable = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.agora = agora or (lambda: _dt.datetime.now())
        if mostrar is None:
            from . import toasts
            mostrar = toasts.mostrar
        self.mostrar = mostrar
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS alertas (id TEXT PRIMARY KEY, card_id TEXT, ativo INTEGER, proximo TEXT,
                dados TEXT);
            CREATE INDEX IF NOT EXISTS alertas_prox ON alertas(ativo, proximo);
            CREATE TABLE IF NOT EXISTS avisos (id TEXT PRIMARY KEY, t TEXT, alerta_id TEXT, card_id TEXT,
                titulo TEXT, msg TEXT, lido INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS avisos_t ON avisos(t);
        """)
        self.db.commit()
        self._stop, self._acorda = threading.Event(), threading.Event()
        self._thread = None

    # ---- banco ----
    def _salvar(self, a: Dict) -> Dict:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO alertas (id, card_id, ativo, proximo, dados) VALUES (?,?,?,?,?)",
                            (a["id"], a.get("card_id"), int(bool(a.get("ativo"))), a.get("proximo"),
                             json.dumps(a, ensure_ascii=False)))
            self.db.commit()
        return a

    def get(self, aid: str) -> Optional[Dict]:
        with self.lock:
            r = self.db.execute("SELECT dados FROM alertas WHERE id=?", (str(aid),)).fetchone()
        return json.loads(r[0]) if r else None

    def _exige(self, aid: str) -> Dict:
        a = self.get(aid)
        if not a:
            raise KeyError("alerta não encontrado")
        return a

    def listar(self, ativos: Optional[bool] = None, card_id: str = None) -> List[Dict]:
        w, p = [], []
        if ativos is not None:
            w.append("ativo=?"); p.append(int(bool(ativos)))
        if card_id:
            w.append("card_id=?"); p.append(card_id)
        with self.lock:
            rows = self.db.execute("SELECT dados FROM alertas" + (" WHERE " + " AND ".join(w) if w else "") +
                                   " ORDER BY ativo DESC, COALESCE(proximo, '9999') ASC", p).fetchall()
        return [dict(json.loads(r[0]), descricao=descrever(json.loads(r[0]))) for r in rows]

    def avisos(self, limit: int = 50, nao_lidos: bool = False) -> List[Dict]:
        with self.lock:
            rows = self.db.execute("SELECT id, t, alerta_id, card_id, titulo, msg, lido FROM avisos" +
                                   (" WHERE lido=0" if nao_lidos else "") + " ORDER BY t DESC LIMIT ?",
                                   (int(limit),)).fetchall()
        return [{"id": i, "t": t, "alerta_id": a, "card_id": c, "titulo": ti, "msg": m, "lido": bool(l)}
                for i, t, a, c, ti, m, l in rows]

    def marcar_lidos(self, ids=None) -> int:
        with self.lock:
            if ids:
                ids = [str(i) for i in ids]
                n = self.db.execute(f"UPDATE avisos SET lido=1 WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount
            else:
                n = self.db.execute("UPDATE avisos SET lido=1 WHERE lido=0").rowcount
            self.db.commit()
        return n

    # ---- criar / mudar ----
    def criar(self, spec: Dict, card: Dict = None, titulo: str = None) -> Dict:
        a = normalizar(spec)
        a.update(card_id=(card or {}).get("id") or spec.get("card_id"),
                 titulo=(a.get("titulo") or titulo or (card or {}).get("title") or "Lembrete")[:200],
                 msg=a.get("msg") or "", ref=spec.get("ref") or (card or {}).get("start") or (card or {}).get("due"),
                 disparos=0, ultimo=None, criado=_iso(self.agora()))
        if a["antes"] is not None and not a["ref"] and not a["quando"]:
            raise ValueError("“antes” precisa de um card com data (início ou prazo)")
        nx = calcular_proximo(a, self.agora(), primeiro=True)
        a.update(proximo=_iso(nx) if nx else None, ativo=bool(nx), estado="ativo" if nx else "concluido")
        self._salvar(a)
        self.start()
        return dict(a, descricao=descrever(a))

    def sync_card(self, card: Dict) -> List[Dict]:
        """Deixa os alertas do banco iguais ao campo `alertas` do card (criar/atualizar/remover). Chame ao criar ou
        mudar um card; ao apagar, remover_card(id)."""
        quer = {x["id"]: x for x in normalizar_lista(card.get("alertas"))}
        tem = {a["id"]: a for a in self.listar(card_id=card["id"])}
        for aid in set(tem) - set(quer):
            self._apagar(aid)
        out = []
        for aid, spec in quer.items():
            velho = tem.get(aid)
            iguais = velho and all(velho.get(k) == spec.get(k) for k in ("quando", "antes", "repetir", "ate",
                                                                          "janela", "canal")) and \
                velho.get("ref") == (card.get("start") or card.get("due")) and velho.get("titulo") == card.get("title")
            out.append(velho if iguais else self.criar(spec, card=card))
        return out

    def remover_card(self, card_id: str) -> int:
        n = 0
        for a in self.listar(card_id=card_id):
            self._apagar(a["id"]); n += 1
        return n

    def _apagar(self, aid: str):
        with self.lock:
            self.db.execute("DELETE FROM alertas WHERE id=?", (aid,))
            self.db.commit()

    def apagar(self, aid: str) -> bool:
        self._exige(aid)
        self._apagar(aid)
        return True

    def adiar(self, aid: str, minutos=15) -> Dict:
        a = self._exige(aid)
        m = _minutos(minutos or 15)
        if m <= 0:
            raise ValueError("adiar: minutos > 0 (5, 15 ou 60)")
        a.update(proximo=_iso(self.agora() + _dt.timedelta(minutes=m)), ativo=True, estado="adiado")
        self._salvar(a)
        self.start()
        return dict(a, descricao=descrever(a))

    def concluir(self, aid: str) -> Dict:
        """Único: acabou. Repetido: esta vez está feita; segue para a próxima ocorrência (ou acaba em `ate`)."""
        a = self._exige(aid)
        if a.get("repetir"):
            ref = max(self.agora(), _dt_de(a.get("proximo")) or self.agora())
            nx = calcular_proximo(a, ref)
        else:
            nx = None
        a.update(proximo=_iso(nx) if nx else None, ativo=bool(nx), estado="ativo" if nx else "concluido")
        self._salvar(a)
        with self.lock:
            self.db.execute("UPDATE avisos SET lido=1 WHERE alerta_id=?", (aid,))
            self.db.commit()
        return dict(a, descricao=descrever(a))

    def desativar(self, aid: str) -> Dict:
        a = self._exige(aid)
        a.update(ativo=False, estado="desativado")
        self._salvar(a)
        return dict(a, descricao=descrever(a))

    def ativar(self, aid: str) -> Dict:
        a = self._exige(aid)
        nx = calcular_proximo(a, self.agora(), primeiro=not a.get("disparos"))
        a.update(proximo=_iso(nx) if nx else None, ativo=bool(nx), estado="ativo" if nx else "concluido")
        self._salvar(a)
        self.start()
        return dict(a, descricao=descrever(a))

    # ---- disparo ----
    prefs: Callable[[], Dict] = None                      # app.ui.get (floreios, nome) — opcional

    def _enfeitar(self, titulo: str, msg: str, seed=None) -> Dict:
        """Floreios (V2 OR): tom feliz/sério/neutro e um enfeite curto, se a pessoa deixou ligado."""
        if not callable(self.prefs):
            return {"titulo": titulo, "msg": msg, "tom": "neutro"}
        try:
            p = self.prefs() or {}
        except Exception:
            p = {}
        try:
            from . import floreios as fl
            t = fl.tom(f"{titulo} {msg}")
            ocas = {"tipo": "aniversario"} if re.search(r"(?i)anivers[aá]rio", titulo or "") else None
            e = fl.enfeitar(titulo, msg, t, ocasiao=ocas, nome_usuario=p.get("nome_usuario") or None, seed=seed,
                            ativo=bool(p.get("floreios", True)))
            return {"titulo": e["titulo"] or titulo, "msg": e["mensagem"], "tom": t}
        except Exception:
            return {"titulo": titulo, "msg": msg, "tom": "neutro"}

    def aviso_avulso(self, titulo: str, msg: str, chave: str = None, card_id: str = None, toast: bool = True) -> int:
        """Aviso sem alerta agendado (datas, aniversários). `chave` evita repetir o mesmo aviso. Devolve 1 ou 0."""
        with self.lock:
            aid = "x:" + (chave or uuid.uuid4().hex[:12])[:100]
            if chave and self.db.execute("SELECT 1 FROM avisos WHERE alerta_id=?", (aid,)).fetchone():
                return 0
            self.db.execute("INSERT INTO avisos (id, t, alerta_id, card_id, titulo, msg, lido) VALUES (?,?,?,?,?,?,0)",
                            (uuid.uuid4().hex[:12], _iso(self.agora()), aid, card_id, titulo, msg))
            self.db.commit()
        if toast:
            try:
                self.mostrar(titulo, msg)
            except Exception:
                pass
        return 1

    def _disparar(self, a: Dict, agora: _dt.datetime) -> Dict:
        msg = a.get("msg") or (descrever(a) if a.get("repetir") else "")
        e = self._enfeitar(a["titulo"], msg, seed=f"{a['id']}{int(a.get('disparos') or 0)}")
        titulo, msg = e["titulo"], e["msg"]
        ev = {"alerta_id": a["id"], "titulo": titulo, "msg": msg, "canais": [], "tom": e["tom"]}
        if "toast" in a.get("canal", CANAIS):
            try:
                self.mostrar(titulo, msg)
                ev["canais"].append("toast")
            except Exception:
                pass
        if "app" in a.get("canal", CANAIS):
            with self.lock:
                self.db.execute("INSERT INTO avisos (id, t, alerta_id, card_id, titulo, msg, lido) VALUES (?,?,?,?,?,?,0)",
                                (uuid.uuid4().hex[:12], _iso(agora), a["id"], a.get("card_id"), titulo, msg))
                self.db.commit()
            ev["canais"].append("app")
        return ev

    def tick(self, agora: _dt.datetime = None) -> List[Dict]:
        """Dispara o que venceu (um disparo por alerta, mesmo se atrasou várias vezes) e agenda o próximo."""
        agora = agora or self.agora()
        with self.lock:
            rows = self.db.execute("SELECT dados FROM alertas WHERE ativo=1 AND proximo IS NOT NULL AND proximo<=?",
                                   (_iso(agora),)).fetchall()
        feitos = []
        for (d,) in rows:
            a = json.loads(d)
            feitos.append(self._disparar(a, agora))
            nx = calcular_proximo(a, agora)
            a.update(ultimo=_iso(agora), disparos=int(a.get("disparos") or 0) + 1, proximo=_iso(nx) if nx else None,
                     ativo=bool(nx), estado="ativo" if nx else "concluido")
            self._salvar(a)
        return feitos

    def proximo_disparo(self) -> Optional[_dt.datetime]:
        with self.lock:
            r = self.db.execute("SELECT MIN(proximo) FROM alertas WHERE ativo=1 AND proximo IS NOT NULL").fetchone()
        return _dt_de(r[0]) if r and r[0] else None

    # ---- agendador (thread só enquanto houver alerta ativo) ----
    def rodando(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        self._acorda.set()
        if self.rodando() or self.proximo_disparo() is None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alertas")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._acorda.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            nx = self.proximo_disparo()
            if nx is None:
                return                                         # nada ativo: a thread acaba (start() a recria)
            espera = max(1.0, min(30.0, (nx - self.agora()).total_seconds()))
            self._acorda.clear()
            self._acorda.wait(espera)

    def close(self):
        self.stop()
        try:
            self.db.close()
        except Exception:
            pass


def servico(app) -> Alertas:
    """O serviço de alertas do app (criado na primeira vez, em HOME/alertas.db)."""
    s = getattr(app, "_alertas", None)
    if s is None:
        from . import config as _c
        s = Alertas(Path(getattr(_c, "HOME")) / "alertas.db")
        ui = getattr(app, "ui", None)
        if ui is not None:
            s.prefs = ui.get
        try:
            app._alertas = s
        except Exception:
            pass
        s.start()
    return s


# ------------------------------------------------------------------------------------------- rotas --
def rotas(app, metodo: str, caminho: str, corpo: Optional[Dict] = None):
    """GET /api/alertas; POST /api/alertas (criar); POST /api/alertas/<id>/adiar {min} | concluir | desativar |
    ativar | apagar; POST /api/alertas/avisos/lidos {ids?}. Devolve (status, dict) ou None se não for rota daqui."""
    p = (caminho or "").split("?", 1)[0].rstrip("/")
    if p != "/api/alertas" and not p.startswith("/api/alertas/"):
        return None
    metodo, corpo = (metodo or "GET").upper(), corpo or {}
    try:
        s = servico(app)
        if p == "/api/alertas":
            if metodo == "GET":
                av = s.avisos(50)
                return 200, {"alertas": s.listar(), "avisos": av, "nao_lidos": sum(1 for x in av if not x["lido"]),
                             "rodando": s.rodando(), "adiar": list(ADIAR)}
            if metodo == "POST":
                card = None
                if corpo.get("card_id") and getattr(app, "cards", None) is not None:
                    card = app.cards.get(corpo["card_id"])
                    if not card:
                        return 404, {"error": "card não encontrado"}
                if corpo.get("texto") and not (corpo.get("quando") or corpo.get("antes") or corpo.get("repetir")):
                    d = de_texto(corpo["texto"], s.agora())
                    if not d:
                        return 400, {"error": "não achei quando avisar no texto"}
                    out = [s.criar(dict(x, ref=(card or {}).get("start") or (card or {}).get("due")), card=card,
                                   titulo=d["titulo"]) for x in d["alertas"]]
                    return 200, {"ok": True, "alertas": out}
                return 200, {"ok": True, "alerta": s.criar(corpo, card=card, titulo=corpo.get("titulo"))}
            return 405, {"error": "método não permitido"}
        resto = p[len("/api/alertas/"):].split("/")
        if resto == ["avisos"] and metodo == "GET":
            return 200, {"avisos": s.avisos(100)}
        if resto == ["avisos", "lidos"] and metodo == "POST":
            return 200, {"ok": True, "n": s.marcar_lidos(corpo.get("ids"))}
        if len(resto) == 1 and metodo == "GET":
            a = s.get(resto[0])
            return (200, dict(a, descricao=descrever(a))) if a else (404, {"error": "alerta não encontrado"})
        if len(resto) == 2 and metodo == "POST":
            aid, acao = resto
            if acao == "adiar":
                return 200, {"ok": True, "alerta": s.adiar(aid, corpo.get("min") or corpo.get("minutos") or 15)}
            if acao in ("concluir", "desativar", "ativar"):
                return 200, {"ok": True, "alerta": getattr(s, acao)(aid)}
            if acao == "apagar":
                return 200, {"ok": s.apagar(aid)}
        return 404, {"error": "rota de alertas desconhecida"}
    except KeyError as e:
        return 404, {"error": str(e).strip("'\"")}
    except ValueError as e:
        return 400, {"error": str(e)}


# ---------------------------------------------------------------------------------------- comandos --
GATILHO = re.compile(r"(?i)\b(?:me\s+)?(?:lembr\w*|avis\w*|alert\w*|notifi\w*|despert\w*)\b")
ANTES = re.compile(r"(?i)\b(\d+|meia)\s*(min(?:utos?)?|h(?:oras?)?)\s+antes\b")
_TIRAR = [r"\b(?:me\s+|nos\s+)?(?:lembr|avis|alert|notifi|despert)\w*(?:\s+(?:a\s+gente|me|nos))?(?:\s+(?:de|que|para|pra))?\b",
          r"\btod[ao]s?\s+(?:[ao]s?\s+)?(?:dias?(?:\s+\d{1,2})?|semanas?|mes(?:es)?|anos?|manhas?|manhãs?|tardes?|noites?|"
          r"(?:segunda|terça|terca|quarta|quinta|sexta|sábado|sabado|domingo)s?(?:-feiras?)?)\b",
          r"\bde\s+\d+\s+em\s+\d+\s+(?:horas?|minutos?|min|dias?)\b", r"\ba\s+cada\s+\d*\s*(?:horas?|minutos?|min|dias?|semanas?)\b",
          r"\bentre\s+(?:as\s+)?\d{1,2}(?:[:h]\d{2})?h?\s+e\s+(?:as\s+)?\d{1,2}(?:[:h]\d{2})?h?\b",
          r"\b(?:dias\s+úteis|dias\s+uteis|diariamente|semanalmente|mensalmente)\b",
          r"\b(?:\d+|meia)\s*(?:min(?:utos?)?|h(?:oras?)?)\s+antes\b"]


def _titulo_de(texto: str) -> str:
    t = texto
    for rx in _TIRAR:
        t = re.sub(rx, " ", t, flags=re.I)
    try:
        from djorge.runtime.triagem import _sem_datas
        t = _sem_datas(t)
    except Exception:
        pass
    t = re.sub(r"(?i)^\s*(?:(?:e|de|que|para|pra|por favor|,)\s+)+", "", re.sub(r"\s{2,}", " ", t).strip(" ,.;:!?"))
    t = re.sub(r"(?i)\s+(?:e|de|que|para|pra|às|as|a)$", "", t).strip(" ,.;:!?")
    return (t[:1].upper() + t[1:])[:120] if t else ""


def de_texto(texto: str, agora: _dt.datetime = None) -> Optional[Dict]:
    """Comando de alerta em linguagem natural -> {alertas:[spec], titulo, descricao, card} ou None (não é alerta).
      "me lembra todo dia às 9h até sexta de tomar o remédio" -> diaria 09:00, ate sexta
      "me avisa de 2 em 2 horas entre 8h e 18h"              -> horaria intervalo 2, janela 08:00-18:00
      "avisa 30 min antes"                                     -> {antes: 30} (para o card em foco)
      "toda segunda às 8h"                                     -> semanal [0] 08:00
    `card` é a proposta de card (lembrete) quando o alerta tem hora própria; com `antes`, vale para um card existente."""
    if not texto or not texto.strip():
        return None
    agora = (agora or _dt.datetime.now()).replace(second=0, microsecond=0)
    from djorge.runtime.recognizers import resolver_quando
    q = resolver_quando(texto, agora=agora)
    m_antes = ANTES.search(texto)
    if not GATILHO.search(texto) and not (q.get("recorrencia") and re.search(r"(?i)\btod[ao]s?\b|\bcada\b|\bem\s+\d", texto)):
        return None
    spec: Dict = {"canal": list(CANAIS)}
    if m_antes:
        spec["antes"] = _minutos(f"{m_antes.group(1)} {m_antes.group(2)}")
    rec = q.get("recorrencia")
    if rec:
        rec = dict(rec)
        if rec.get("freq") in ("horaria", "minutos"):
            rec.pop("hora", None)
            if q.get("fim") and q.get("hora") and re.search(r"(?i)\bentre\b|\bdas?\s+\d", texto):
                spec["janela"] = {"inicio": q["hora"], "fim": q["fim"]}
        spec["repetir"] = rec
        if q.get("prazo") and q.get("data"):
            spec["ate"] = q["data"]
        nx = calcular_proximo(dict(spec, quando=None, ref=None), agora)
        if rec.get("freq") in ("horaria", "minutos") and not spec.get("janela"):
            nx = agora + _passo(rec)
        if nx:
            spec["quando"] = _iso(nx)
    elif q.get("data") and not (q.get("prazo") and not q.get("hora")):
        if q.get("hora"):
            spec["quando"] = f"{q['data']}T{q['hora']}"
        else:
            spec["quando"] = f"{q['data']}T09:00"
    if not spec.get("quando") and spec.get("antes") is None and not spec.get("repetir"):
        return None
    spec = normalizar(spec)
    spec.pop("id", None)
    titulo = _titulo_de(texto) or "Lembrete"
    out = {"alertas": [spec], "titulo": titulo, "descricao": descrever(spec)}
    if spec.get("quando") or spec.get("repetir"):
        out["card"] = {"kind": "lembrete", "title": titulo, "start": spec.get("quando"), "column": "entrada",
                       "alertas": [spec]}
    return out


def iniciar(app) -> Alertas:
    """Chamado ao abrir o app: retoma o agendador se houver alertas ativos."""
    return servico(app)
