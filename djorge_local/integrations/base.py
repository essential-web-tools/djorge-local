"""Peças comuns: transporte HTTP (urllib, injetável), cofre de segredos (keyring), PKCE/state do OAuth e a base
dos provedores. Nada aqui grava segredo em disco fora do keyring nem escreve token/URL com query em log."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional, Tuple

log = logging.getLogger("djorge.integracoes")

REDIRECT_URI = "http://127.0.0.1:8765/oauth/callback"
STATE_TTL_S = 600
TIMEOUT_S = 20
KEYRING_SERVICE = "Djorge Local"

# transporte(method, url, headers, body) -> (status, headers, bytes); levanta OSError em falha de rede
Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Dict[str, str], bytes]]


def urllib_transport(method, url, headers, body, timeout=TIMEOUT_S):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


def safe_url(url: str) -> str:
    """URL sem query nem fragmento (para log)."""
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


class NetError(Exception):
    pass


class HttpClient:
    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or urllib_transport

    def request(self, method, url, *, headers=None, json_body=None, form=None, raw=None):
        h = dict(headers or {})
        body = raw
        if json_body is not None:
            body = json.dumps(json_body).encode(); h.setdefault("Content-Type", "application/json")
        elif form is not None:
            body = urllib.parse.urlencode(form).encode(); h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        h.setdefault("Accept", "application/json")
        try:
            status, rh, data = self.transport(method, url, h, body)
        except (OSError, socket.timeout, urllib.error.URLError, ValueError) as e:
            log.warning("falha de rede em %s %s: %s", method, safe_url(url), type(e).__name__)
            raise NetError(f"sem conexão com {urllib.parse.urlsplit(url).netloc} ({type(e).__name__})") from None
        try:
            obj = json.loads(data.decode("utf-8")) if data else {}
        except (ValueError, UnicodeDecodeError):
            obj = {"_texto": data[:500].decode("utf-8", "replace")}
        log.debug("%s %s -> %s", method, safe_url(url), status)
        return status, obj


def api_error(status, obj) -> str:
    e = obj.get("error") if isinstance(obj, dict) else None
    if isinstance(e, dict):
        msg = e.get("message") or e.get("code") or ""
    else:
        msg = (obj.get("error_description") or e or "") if isinstance(obj, dict) else ""
    return f"HTTP {status}: {str(msg)[:300]}".strip()


# ------------------------------------------------------------------------------------------------ segredos --
class SecretStore:
    """Segredos no keyring do sistema (Gerenciador de Credenciais no Windows). Em teste, passe `backend` com
    get_password/set_password/delete_password (ex.: dicionário em memória)."""

    def __init__(self, backend=None, service=KEYRING_SERVICE):
        if backend is None:
            import keyring as backend          # noqa: N813
        self.kr, self.service = backend, service

    def get(self, key):
        try:
            return self.kr.get_password(self.service, key)
        except Exception as e:                 # keyring indisponível não derruba o app
            log.warning("keyring indisponível (%s)", type(e).__name__); return None

    def set(self, key, value):
        self.kr.set_password(self.service, key, value)

    def delete(self, key):
        try:
            self.kr.delete_password(self.service, key)
        except Exception:
            pass

    def get_json(self, key):
        v = self.get(key)
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def set_json(self, key, obj):
        self.set(key, json.dumps(obj))


class MemoryKeyring:
    """Backend em memória (testes)."""

    def __init__(self):
        self.data = {}

    def get_password(self, s, k):
        return self.data.get((s, k))

    def set_password(self, s, k, v):
        self.data[(s, k)] = v

    def delete_password(self, s, k):
        self.data.pop((s, k), None)


# ------------------------------------------------------------------------------------------------- OAuth ----
def pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class StateStore:
    """`state` aleatório, de uso único, válido por 10 min."""

    def __init__(self, ttl=STATE_TTL_S, clock=time.time):
        self.ttl, self.clock, self._d, self._lock = ttl, clock, {}, threading.Lock()

    def new(self, provider: str, verifier: str) -> str:
        st = secrets.token_urlsafe(32)
        with self._lock:
            now = self.clock()
            self._d = {k: v for k, v in self._d.items() if v[2] > now}
            self._d[st] = (provider, verifier, now + self.ttl)
        return st

    def take(self, state: str):
        with self._lock:
            v = self._d.pop(state or "", None)
        if not v or v[2] < self.clock():
            return None
        return v[0], v[1]


# --------------------------------------------------------------------------------------------- provedores --
class Provider:
    id = ""
    name = ""
    group = ""
    icon = ""
    features: list = []
    needs: list = []          # [{key, label, secret}]
    oauth = False

    def __init__(self, store: SecretStore, http: HttpClient, clock=time.time):
        self.store, self.http, self.clock = store, http, clock

    def k(self, key):
        return f"{self.id}:{key}"

    def value(self, key):
        return self.store.get(self.k(key))

    def configured(self):
        return all(self.value(n["key"]) for n in self.needs if not n.get("optional"))

    def connected(self):
        return self.configured()

    def account(self):
        return self.store.get(self.k("account"))

    def clear(self):
        for key in ("token", "account"):
            self.store.delete(self.k(key))

    def missing(self):
        return {"error": "Preencha antes: " + ", ".join(n["label"] for n in self.needs
                                                         if not n.get("optional") and not self.value(n["key"]))}

    def sync(self, cards):
        return {"imported": 0, "updated": 0}

    def send(self, to, body, subject=None, **kw):
        return {"error": f"{self.name} não envia mensagens"}

    def search(self, q):
        return []


class OAuthProvider(Provider):
    oauth = True
    auth_endpoint = token_endpoint = ""
    scopes: list = []
    extra_auth: dict = {}

    def connected(self):
        return bool(self.store.get_json(self.k("token")))

    def client_secret(self):
        return self.value("client_secret")

    def auth_url(self, state, challenge):
        q = {"client_id": self.value("client_id"), "redirect_uri": REDIRECT_URI, "response_type": "code",
             "scope": " ".join(self.scopes), "state": state, "code_challenge": challenge,
             "code_challenge_method": "S256", **self.extra_auth}
        return self.auth_endpoint + "?" + urllib.parse.urlencode(q)

    def _token_req(self, form):
        form = {"client_id": self.value("client_id"), **form}
        if self.client_secret():
            form["client_secret"] = self.client_secret()
        st, obj = self.http.request("POST", self.token_endpoint, form=form)
        if st != 200 or "access_token" not in obj:
            raise NetError(api_error(st, obj))
        return obj

    def _save_token(self, obj, old=None):
        tok = {"access_token": obj["access_token"],
               "refresh_token": obj.get("refresh_token") or (old or {}).get("refresh_token"),
               "expires_at": self.clock() + int(obj.get("expires_in") or 3600)}
        self.store.set_json(self.k("token"), tok)
        return tok

    def exchange(self, code, verifier):
        tok = self._save_token(self._token_req({"grant_type": "authorization_code", "code": code,
                                                "redirect_uri": REDIRECT_URI, "code_verifier": verifier}))
        try:
            acc = self.fetch_account()
            if acc:
                self.store.set(self.k("account"), acc)
        except NetError:
            pass
        return tok

    def refresh(self):
        old = self.store.get_json(self.k("token")) or {}
        if not old.get("refresh_token"):
            raise NetError("sessão expirada: conecte de novo")
        form = {"grant_type": "refresh_token", "refresh_token": old["refresh_token"]}
        if getattr(self, "scopes_on_refresh", False):
            form["scope"] = " ".join(self.scopes)
        return self._save_token(self._token_req(form), old)

    def access_token(self):
        tok = self.store.get_json(self.k("token"))
        if not tok:
            raise NetError(f"{self.name} não está conectado")
        if tok.get("expires_at", 0) - 60 < self.clock():
            tok = self.refresh()
        return tok["access_token"]

    def api(self, method, url, **kw):
        """Chamada autenticada; renova o token uma vez se vier 401."""
        for tentativa in (0, 1):
            h = {"Authorization": "Bearer " + self.access_token(), **kw.pop("headers", {})} if tentativa == 0 else \
                {**h, "Authorization": "Bearer " + self.refresh()["access_token"]}
            st, obj = self.http.request(method, url, headers=h, **kw)
            if st != 401:
                break
        if st >= 400:
            raise NetError(api_error(st, obj))
        return obj

    def fetch_account(self):
        return None
