"""Integrações do Djorge Local v2: Google, Microsoft, WhatsApp Business (Cloud API) e Gemini.

    from djorge_local.integrations import Integrations
    integ = Integrations(cfg, cards, notify)

Interface (contrato docs/V2_SPEC.md, parte B): list, configure, connect, oauth_callback, disconnect, sync, send,
search, start_background (+ draft para o Gemini). Erros nunca derrubam o app: voltam como {"error": ...}.

Segredos (client secret, tokens OAuth, token do WhatsApp, chave do Gemini, e também client_id/phone id) ficam SÓ no
keyring do sistema (Gerenciador de Credenciais do Windows), serviço "Djorge Local". Nada vai para config.yaml.
Em disco fica apenas HOME/integracoes.json com dados não secretos (última sincronização, último erro).

O que esperamos do objeto `cards` (store do agente A, djorge_local/cards.py) — em ordem de preferência:
  1. cards.upsert_external(integration, external_id, fields) -> card   (cria ou atualiza pelo source)
     se existir também cards.get_external(integration, external_id) -> card|None, usamos para contar
     importados x atualizados; senão contamos "importado" quando card["created"] == card["updated"].
  2. senão: cards.list(source=integration) -> [card] | {"cards": [...]}, cards.create(fields) -> card e
     cards.update(id, fields) -> card; procuramos o card por source.external_id.
Na atualização de um card já existente não mexemos em column/order/pinned (a pessoa pode ter arrastado).
`notify(titulo, mensagem)` é chamado (se não for None) quando a sincronização em segundo plano traz novidades.
"""
from __future__ import annotations

import html
import json
import logging
import threading
import time
import urllib.parse
from pathlib import Path

from .base import Provider, HttpClient, MemoryKeyring, NetError, SecretStore, StateStore, pkce_pair, safe_url  # noqa: F401
from .gemini import Gemini
from .google import Google
from .microsoft import Microsoft
from .whatsapp import WhatsApp

log = logging.getLogger("djorge.integracoes")
PROVIDERS = (Google, Microsoft, WhatsApp, Gemini)
KEEP_ON_UPDATE = ("column", "order", "pinned", "color")

PAGINA = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Djorge Local</title>
<body style="font-family:system-ui,sans-serif;background:#f6f3ee;color:#2b2b2b;display:grid;place-items:center;
height:100vh;margin:0"><div style="text-align:center;max-width:28rem"><h1 style="color:#b56576;font-size:1.4rem">
{titulo}</h1><p>{msg}</p><p>Pode fechar esta janela.</p></div></body></html>"""


def _pagina(titulo, msg):
    return PAGINA.format(titulo=html.escape(titulo), msg=html.escape(msg))


class Integrations:
    def __init__(self, cfg, cards, notify=None, *, transport=None, keyring_backend=None, clock=time.time):
        self.cfg, self.cards, self.notify, self.clock = cfg or {}, cards, notify, clock
        self.store = SecretStore(keyring_backend)
        self.http = HttpClient(transport)
        self.states = StateStore(clock=clock)
        self.providers = {P.id: P(self.store, self.http, clock) for P in PROVIDERS}
        home = (self.cfg.get("_dir") if isinstance(self.cfg, dict) else None)
        if not home:
            from ..config import HOME
            home = HOME
        self.status_path = Path(home) / "integracoes.json"
        self.status = self._load_status()
        self._lock = threading.RLock()
        self._bg = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------------------------------- estado
    def _load_status(self):
        try:
            return json.loads(self.status_path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _set_status(self, pid, **kw):
        self.status.setdefault(pid, {}).update(kw)
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(json.dumps(self.status, ensure_ascii=False, indent=1), "utf-8")
        except OSError:
            pass

    def _get(self, pid):
        p = self.providers.get(pid)
        if p is None:
            raise KeyError(pid)
        return p

    def _guard(self, pid, fn, *a, **kw):
        try:
            return fn(self._get(pid), *a, **kw)
        except KeyError:
            return {"error": f"integração desconhecida: {pid}"}
        except NetError as e:
            self._set_status(pid, error=str(e))
            return {"error": str(e)}
        except Exception as e:                      # nunca derruba o app
            log.exception("erro na integração %s", pid)
            self._set_status(pid, error=f"erro interno ({type(e).__name__})")
            return {"error": f"erro interno ({type(e).__name__})"}

    # ----------------------------------------------------------------------------------------- interface
    def list(self):
        out = []
        for p in self.providers.values():
            st = self.status.get(p.id, {})
            out.append({"id": p.id, "name": p.name, "group": p.group, "icon": p.icon,
                        "connected": bool(p.connected()), "configured": bool(p.configured()),
                        "account": p.account(), "last_sync": st.get("last_sync"), "error": st.get("error"),
                        "needs": [{"key": n["key"], "label": n["label"], "secret": n["secret"],
                                   "optional": bool(n.get("optional")), "filled": bool(p.value(n["key"]))}
                                  for n in p.needs],
                        "features": list(p.features), "oauth": p.oauth,
                        "leaves_computer": bool(getattr(p, "leaves_computer", False))})
        return out

    def configure(self, pid, values):
        def _do(p):
            allowed = {n["key"] for n in p.needs}
            bad = set(values or {}) - allowed
            if bad:
                return {"error": "campos desconhecidos: " + ", ".join(sorted(bad))}
            for k, v in (values or {}).items():
                v = str(v or "").strip()
                if v:
                    p.store.set(p.k(k), v)
            self._set_status(p.id, error=None)
            return {"ok": True, "configured": p.configured()}
        return self._guard(pid, _do)

    def connect(self, pid):
        def _do(p):
            if not p.configured():
                return p.missing()
            if p.oauth:
                verifier, challenge = pkce_pair()
                return {"auth_url": p.auth_url(self.states.new(p.id, verifier), challenge)}
            acc = p.verify()
            self._set_status(p.id, error=None)
            return {"ok": True, "account": acc}
        return self._guard(pid, _do)

    def oauth_callback(self, query):
        try:
            q = urllib.parse.parse_qs(query.lstrip("?")) if isinstance(query, str) else dict(query or {})
            q = {k: (v[0] if isinstance(v, (list, tuple)) else v) for k, v in q.items()}
            got = self.states.take(str(q.get("state") or ""))
            if not got:
                return _pagina("Não foi possível conectar", "O pedido expirou ou não é válido. Tente conectar de novo pelo Djorge.")
            pid, verifier = got
            p = self._get(pid)
            if q.get("error"):
                self._set_status(pid, error="autorização recusada")
                return _pagina("Conexão cancelada", f"{p.name} não autorizou o acesso.")
            if not q.get("code"):
                return _pagina("Não foi possível conectar", "A resposta não trouxe o código de autorização.")
            p.exchange(str(q["code"]), verifier)
            self._set_status(pid, error=None)
            return _pagina("Conectado!", f"{p.name} foi conectado ao Djorge Local.")
        except NetError as e:
            return _pagina("Não foi possível conectar", str(e))
        except Exception as e:
            log.warning("callback OAuth falhou (%s)", type(e).__name__)
            return _pagina("Não foi possível conectar", "Ocorreu um erro inesperado. Tente de novo.")

    def disconnect(self, pid, forget=False):
        def _do(p):
            p.clear()
            if forget:
                for n in p.needs:
                    p.store.delete(p.k(n["key"]))
            self._set_status(p.id, error=None, last_sync=None)
            return {"ok": True}
        return self._guard(pid, _do)

    def sync(self, pid):
        def _do(p):
            if not p.connected():
                return {"error": f"{p.name} não está conectado"}
            if not hasattr(p, "items"):
                return {"imported": 0, "updated": 0, "note": f"{p.name} não importa itens"}
            imp = upd = 0
            with self._lock:
                for fields in p.items():
                    created = self._upsert(p.id, fields["source"]["external_id"], fields)
                    imp += created; upd += not created
            self._set_status(p.id, last_sync=time.strftime("%Y-%m-%dT%H:%M:%S"), error=None)
            return {"imported": imp, "updated": upd}
        return self._guard(pid, _do)

    def send(self, pid, to, body, subject=None, **kw):
        def _do(p):
            if not p.connected():
                return {"error": f"{p.name} não está conectado"}
            if not to:
                return {"error": "destinatário vazio"}
            return p.send(to, body, subject, **kw)
        return self._guard(pid, _do)

    def search(self, q, limit=10):
        out = []
        if not (q or "").strip():
            return out
        for p in self.providers.values():
            if type(p).search is not Provider.search and p.connected():
                r = self._guard(p.id, lambda pp: pp.search(q, limit))
                if isinstance(r, list):
                    out += r
        return out

    def draft(self, instrucao, contexto=""):
        """Rascunho pelo Gemini (o texto SAI do computador). -> {"text", "leaves_computer": True}|{"error"}"""
        def _do(p):
            if not p.configured():
                return p.missing()
            return {"text": p.draft(instrucao, contexto), "leaves_computer": True, "aviso": p.features[-1]}
        return self._guard("gemini", _do)

    def start_background(self, every_min=None):
        if self._bg and self._bg.is_alive():
            return
        conf = (self.cfg.get("integrations") or {}) if isinstance(self.cfg, dict) else {}
        every = max(1.0, float(every_min or conf.get("sync_min") or 15)) * 60

        def loop():
            espera = 5                                  # primeira rodada logo após abrir o app
            while not self._stop.wait(espera):
                espera = every
                self.sync_all(notify=True)
        self._bg = threading.Thread(target=loop, daemon=True, name="integracoes-sync")
        self._bg.start()

    def stop_background(self):
        self._stop.set()

    def sync_all(self, notify=False):
        res = {}
        for p in self.providers.values():
            if hasattr(p, "items") and p.connected():
                res[p.id] = r = self.sync(p.id)
                if notify and self.notify and r.get("imported"):
                    try:
                        self.notify("Djorge Local", f"{p.name}: {r['imported']} novo(s) item(ns) importado(s)")
                    except Exception:
                        pass
        return res

    # ---------------------------------------------------------------------------------------- cards
    def _upsert(self, integration, ext_id, fields) -> bool:
        c = self.cards
        if hasattr(c, "upsert_external"):
            existed = None
            if hasattr(c, "get_external"):
                existed = c.get_external(integration, ext_id)
                if existed:
                    fields = {k: v for k, v in fields.items() if k not in KEEP_ON_UPDATE}
            card = c.upsert_external(integration, ext_id, fields) or {}
            if hasattr(c, "get_external"):
                return not existed
            return card.get("created") is not None and card.get("created") == card.get("updated")
        found = None
        try:
            lst = c.list(source=integration)
        except TypeError:
            lst = c.list()
        if isinstance(lst, dict):
            lst = lst.get("cards", [])
        for card in lst or []:
            s = card.get("source") or {}
            if s.get("integration") == integration and s.get("external_id") == ext_id:
                found = card; break
        if found:
            c.update(found["id"], {k: v for k, v in fields.items() if k not in KEEP_ON_UPDATE})
            return False
        c.create(fields)
        return True
