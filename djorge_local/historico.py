"""Histórico = ações, não conversa (rodada 4).

Cada registro é uma solicitação e o que foi feito:
  {id, t, pedido, ferramenta, acao, alvo, resultado, alteracoes:[{t, campo, de, para}], desfeito}
A conversa (respostas do assistente, mensagens trocadas) não vai para o disco. As alterações de cards são
guardadas também por card (HOME/alteracoes.jsonl) para `GET /api/cards/<id>/alteracoes`.
O histórico antigo (historico.jsonl, com conversas) é migrado só no que era ação e depois apagado.
"""
from __future__ import annotations

import collections
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

CAMPOS = ("id", "t", "pedido", "ferramenta", "acao", "alvo", "resultado", "alteracoes", "desfeito")
_IGNORA_DIFF = {"updated", "created", "order"}
_local = threading.local()


def _agora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _curto(s, n=200) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def diff(antes: Optional[Dict], depois: Optional[Dict]) -> List[Dict]:
    """[{t, campo, de, para}] entre duas versões de um item (campos técnicos ignorados)."""
    a, b, t = antes or {}, depois or {}, _agora()
    out = []
    for k in sorted(set(a) | set(b)):
        if k in _IGNORA_DIFF or k == "id":
            continue
        if a.get(k) != b.get(k):
            out.append({"t": t, "campo": k, "de": a.get(k), "para": b.get(k)})
    return out


def _mascara(texto: str, cfg) -> str:
    h = cfg.get("history") or {}
    if not h.get("store_text", True):
        return f"[{len(texto)} caracteres]"
    if h.get("mask_pii", True):
        try:
            from djorge.runtime.tools import mask_pii
            return mask_pii(texto)["masked"]
        except Exception:
            pass
    return texto


def normalizar(rec: Dict, cfg=None) -> Dict:
    """Converte qualquer registro (comando, evento, ação de árvore, secretário, log) no formato de ação."""
    cfg = cfg or {}
    r = dict(rec)
    tipo = r.pop("type", None)
    out = {"id": r.pop("id", None) or uuid.uuid4().hex[:10], "t": r.pop("t", None) or _agora(),
           "pedido": r.pop("pedido", None), "ferramenta": r.pop("ferramenta", None), "acao": r.pop("acao", None),
           "alvo": r.pop("alvo", None), "resultado": r.pop("resultado", None),
           "alteracoes": list(r.pop("alteracoes", None) or []), "desfeito": bool(r.pop("desfeito", False))}
    if tipo == "command":
        out["pedido"] = out["pedido"] or _curto(_mascara(str(r.get("text") or ""), cfg))
        out["ferramenta"] = out["ferramenta"] or r.get("tool")
        out["acao"] = out["acao"] or r.get("action")
    elif tipo == "event":
        ev = r.get("event") or {}
        out["pedido"] = out["pedido"] or _curto(_mascara(" — ".join(x for x in (ev.get("title"), ev.get("text")) if x),
                                                        cfg))
        out["ferramenta"] = out["ferramenta"] or f"evento:{ev.get('source') or ''}"
        runs = r.get("runs") or []
        out["acao"] = out["acao"] or ", ".join(str(x.get("tree")) for x in runs if x.get("tree")) or "evento"
        out["resultado"] = out["resultado"] or ("erro" if any(x.get("error") for x in runs) else "processado")
        if r.get("secretario") is not None:
            out["secretario"] = r["secretario"]
        out["fonte"] = ev.get("source")
    elif tipo == "action":
        out["ferramenta"] = out["ferramenta"] or "árvore"
        out["acao"] = out["acao"] or r.get("action") or r.get("kind")
        out["resultado"] = out["resultado"] or r.get("status")
        out["pedido"] = out["pedido"] or _curto(r.get("summary") or r.get("tree") or "")
    elif tipo == "log":
        out["ferramenta"] = out["ferramenta"] or "árvore"
        out["acao"] = out["acao"] or "registrar"
        out["pedido"] = out["pedido"] or _curto(r.get("message") or "")
        out["resultado"] = out["resultado"] or "registrado"
    elif tipo == "secretario":
        out["ferramenta"] = out["ferramenta"] or "secretário"
        out["acao"] = out["acao"] or "regras"
        out["alvo"] = out["alvo"] or ({"notificacao": r.get("notificacao")} if r.get("notificacao") else None)
        out["resultado"] = out["resultado"] or r.get("regras")
    elif tipo:
        out["ferramenta"] = out["ferramenta"] or tipo
    return out


class Historico:
    """Mesma interface do History antigo (add, last, clear, items, lock, file), agora com ações."""

    def __init__(self, cfg, home: Path):
        self.cfg = cfg
        home.mkdir(parents=True, exist_ok=True)
        self.items = collections.deque(maxlen=int((cfg.get("history") or {}).get("keep", 2000)))
        self.lock = threading.RLock()
        self.file = home / "acoes.jsonl"
        self.alt_file = home / "alteracoes.jsonl"
        self._carregar()
        self._migrar(home / "historico.jsonl")

    # ---------------------------------------------------------------- disco --
    def _carregar(self):
        if not self.file.is_file():
            return
        for ln in self.file.read_text(encoding="utf-8").splitlines():
            try:
                self.items.append(json.loads(ln))
            except ValueError:
                continue

    def _migrar(self, antigo: Path):
        """O arquivo antigo guardava conversas: aproveita só as ações e apaga o arquivo."""
        if not antigo.is_file():
            return
        try:
            for ln in antigo.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                r.pop("reply", None)
                self.items.append(normalizar(r, self.cfg))
            self._rewrite()
        finally:
            try:
                antigo.unlink()
            except OSError:
                pass

    def _rewrite(self):
        with open(self.file, "w", encoding="utf-8") as f:
            for rec in self.items:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # ---------------------------------------------------------------- API --
    def add(self, rec: Dict) -> Dict:
        r = normalizar(rec, self.cfg)
        pend = consumir_alteracoes()
        if pend:
            r["alteracoes"] = r["alteracoes"] + [{k: v for k, v in a.items() if k != "card"} for a in pend]
            if not r["alvo"]:
                ids = sorted({a["card"] for a in pend if a.get("card")})
                r["alvo"] = {"tipo": "card", "id": ids[0]} if len(ids) == 1 else ({"tipo": "card", "ids": ids}
                                                                                  if ids else None)
        with self.lock:
            self.items.append(r)
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return r

    def atualizar(self, rid: str, **campos) -> Optional[Dict]:
        """Completa um registro (ex.: o pedido confirmado depois, desfeito). Alterações pendentes da thread entram."""
        pend = consumir_alteracoes()
        with self.lock:
            for r in self.items:
                if str(r.get("id")) == str(rid):
                    for k, v in campos.items():
                        if v is not None:
                            r[k] = v
                    if pend:
                        r["alteracoes"] = list(r.get("alteracoes") or []) + [
                            {k: v for k, v in a.items() if k != "card"} for a in pend]
                        if not r.get("alvo"):
                            r["alvo"] = {"tipo": "card", "id": pend[0]["card"]}
                    self._rewrite()
                    return r
        if pend:
            return self.add({"id": rid, **campos})
        return None

    def last(self, n=100) -> List[Dict]:
        with self.lock:
            return list(self.items)[-n:][::-1]

    def clear(self, tudo: bool = False, antes_de: str = None, ids=None) -> int:
        """Apaga tudo, o que for anterior a uma data (ISO, ex. 2026-11-01) ou os itens com estes ids."""
        with self.lock:
            n = len(self.items)
            if tudo:
                keep = []
            elif antes_de:
                try:
                    import datetime as _d
                    _d.datetime.fromisoformat(str(antes_de).strip())
                except ValueError:
                    raise ValueError("antes_de: use uma data como 2026-11-01")
                keep = [r for r in self.items if str(r.get("t", "")) >= str(antes_de)]
            elif ids:
                ids = {str(i) for i in ids}
                keep = [r for r in self.items if str(r.get("id")) not in ids]
            else:
                raise ValueError("diga o que apagar: tudo, antes_de ou ids")
            self.items = collections.deque(keep, maxlen=self.items.maxlen)
            self._rewrite()
            if tudo and self.alt_file.is_file():
                self.alt_file.write_text("", encoding="utf-8")
            return n - len(self.items)

    # ---------------------------------------------------------------- alterações de itens --
    def registrar_alteracoes(self, card_id: str, alts: List[Dict], origem: str = "ui"):
        if not alts:
            return
        with self.lock:
            with open(self.alt_file, "a", encoding="utf-8") as f:
                for a in alts:
                    f.write(json.dumps(dict(a, card=card_id, origem=origem), ensure_ascii=False, default=str) + "\n")
        buf = getattr(_local, "pend", None)
        if buf is None:
            buf = _local.pend = []
        buf.extend(dict(a, card=card_id) for a in alts)

    def alteracoes(self, card_id: str) -> List[Dict]:
        if not self.alt_file.is_file():
            return []
        out = []
        with self.lock:
            for ln in self.alt_file.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if str(r.get("card")) == str(card_id):
                    out.append({k: r.get(k) for k in ("t", "campo", "de", "para", "origem")})
        return out


def coletar_alteracoes():
    """Começa a juntar, nesta thread, as alterações de cards para o próximo registro de ação."""
    _local.pend = []


def consumir_alteracoes() -> List[Dict]:
    buf = getattr(_local, "pend", None) or []
    _local.pend = None
    return buf


def instrumentar_cards(cards, hist: Historico):
    """Envolve create/update/move/delete do Cards para guardar de/para (UI e comando passam por aqui)."""
    if getattr(cards, "_instrumentado", False):
        return cards
    o_create, o_update, o_move, o_delete = cards.create, cards.update, cards.move, cards.delete

    def create(data):
        c = o_create(data)
        hist.registrar_alteracoes(c["id"], [{"t": _agora(), "campo": "criado", "de": None, "para": c.get("title")}])
        return c

    def update(cid, data):
        antes = cards.get(cid)
        c = o_update(cid, data)
        hist.registrar_alteracoes(cid, diff(antes, c))
        return c

    def move(cid, column, order=None, start=None):
        antes = cards.get(cid)
        c = o_move(cid, column, order, start)
        hist.registrar_alteracoes(cid, diff(antes, c))
        return c

    def delete(cid):
        antes = cards.get(cid)
        r = o_delete(cid)
        hist.registrar_alteracoes(cid, [{"t": _agora(), "campo": "apagado", "de": (antes or {}).get("title"),
                                         "para": None}])
        return r

    cards.create, cards.update, cards.move, cards.delete = create, update, move, delete
    cards._instrumentado = True
    return cards
