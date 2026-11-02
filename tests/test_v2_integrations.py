"""Testes das integrações (parte B) com transporte HTTP falso e keyring em memória."""
import base64
import hashlib
import json
import logging
import urllib.parse

import pytest

from djorge_local.integrations import Integrations, MemoryKeyring
from djorge_local.integrations.base import StateStore, pkce_pair


class FakeHTTP:
    """Responde por (método, prefixo da URL); grava as chamadas."""

    def __init__(self):
        self.routes, self.calls, self.fail = [], [], False

    def on(self, method, prefix, status=200, body=None):
        self.routes.insert(0, (method, prefix, status, body))

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if self.fail:
            raise OSError("rede caiu")
        for m, pre, st, b in self.routes:
            if m == method and url.startswith(pre):
                b = b(url, headers, body) if callable(b) else b
                return st, {}, json.dumps(b if b is not None else {}).encode()
        return 404, {}, b'{"error":{"message":"nao encontrado"}}'


class FakeCards:
    def __init__(self):
        self.cards, self.n = {}, 0

    def get_external(self, integ, ext):
        return next((c for c in self.cards.values() if c["source"] == {**c["source"], "integration": integ, "external_id": ext}), None)

    def upsert_external(self, integ, ext, fields):
        c = self.get_external(integ, ext)
        if c:
            c.update(fields); return c
        self.n += 1
        c = {"id": str(self.n), "column": "entrada", **fields}
        self.cards[c["id"]] = c
        return c


class ListCards:
    """Store sem upsert_external: só list/create/update."""

    def __init__(self):
        self.items = []

    def list(self, **kw):
        return {"cards": self.items}

    def create(self, f):
        self.items.append({"id": str(len(self.items) + 1), **f}); return self.items[-1]

    def update(self, cid, f):
        c = next(c for c in self.items if c["id"] == cid); c.update(f); return c


@pytest.fixture
def env(tmp_path):
    http, kr, cards, clock = FakeHTTP(), MemoryKeyring(), FakeCards(), [1_000_000.0]
    notes = []
    integ = Integrations({"_dir": str(tmp_path)}, cards, lambda t, m: notes.append((t, m)),
                         transport=http, keyring_backend=kr, clock=lambda: clock[0])
    return integ, http, kr, cards, clock, tmp_path


def _connect_google(integ, http):
    integ.configure("google", {"client_id": "cid.apps.googleusercontent.com", "client_secret": "SEGREDO-GOOGLE"})
    url = integ.connect("google")["auth_url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    http.on("POST", "https://oauth2.googleapis.com/token", 200,
            {"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600})
    http.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile", 200, {"emailAddress": "eu@gmail.com"})
    page = integ.oauth_callback({"state": [q["state"]], "code": ["CODE-123"]})
    return q, page


# ----------------------------------------------------------------------------------------------- PKCE / state
def test_pkce_s256():
    v, c = pkce_pair()
    assert 43 <= len(v) <= 128
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def test_state_unico_e_expira():
    t = [0.0]
    s = StateStore(clock=lambda: t[0])
    a = s.new("google", "v")
    assert s.take(a) == ("google", "v")
    assert s.take(a) is None                     # uso único
    b = s.new("google", "v")
    t[0] = 601
    assert s.take(b) is None                     # 10 min


def test_connect_google_url_e_troca_de_codigo(env):
    integ, http, kr, cards, clock, _ = env
    assert "error" in integ.connect("google")    # sem client id
    q, page = _connect_google(integ, http)
    assert q["redirect_uri"] == "http://127.0.0.1:8765/oauth/callback"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"]
    assert "gmail.send" in q["scope"] and "calendar.events" in q["scope"] and "auth/tasks" in q["scope"]
    assert "Pode fechar esta janela" in page and "Conectado" in page
    tok = [c for c in http.calls if c["url"].startswith("https://oauth2")][0]
    form = dict(urllib.parse.parse_qsl(tok["body"].decode()))
    assert form["code"] == "CODE-123" and form["grant_type"] == "authorization_code"
    assert hashlib.sha256(form["code_verifier"].encode()).digest() == \
        base64.urlsafe_b64decode(q["code_challenge"] + "=")
    g = next(i for i in integ.list() if i["id"] == "google")
    assert g["connected"] and g["account"] == "eu@gmail.com"
    # reusar o mesmo state falha com segurança
    again = integ.oauth_callback({"state": q["state"], "code": "X"})
    assert "Não foi possível" in again


def test_callback_state_invalido_e_erro(env):
    integ, http, *_ = env
    assert "Não foi possível" in integ.oauth_callback("state=xyz&code=1")
    assert "Não foi possível" in integ.oauth_callback("")
    assert "<script" not in integ.oauth_callback("state=<script>alert(1)</script>")
    integ.configure("google", {"client_id": "a", "client_secret": "b"})
    st = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(integ.connect("google")["auth_url"]).query))["state"]
    assert "cancelada" in integ.oauth_callback(f"state={st}&error=access_denied")


def test_refresh_quando_expira_e_em_401(env):
    integ, http, kr, cards, clock, _ = env
    _connect_google(integ, http)
    http.on("POST", "https://oauth2.googleapis.com/token", 200, {"access_token": "AT-2", "expires_in": 3600})
    clock[0] += 4000                                     # expirou
    http.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages?", 200, {"messages": []})
    assert integ.search("fatura") == []
    last_tok = [c for c in http.calls if c["url"].startswith("https://oauth2")][-1]
    form = dict(urllib.parse.parse_qsl(last_tok["body"].decode()))
    assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "RT-1"
    assert http.calls[-1]["headers"]["Authorization"] == "Bearer AT-2"
    # refresh_token antigo é mantido; 401 provoca nova renovação
    seq = iter([401, 200])
    http.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages?",
            200, lambda u, h, b: {"messages": []})
    orig = http.__call__

    calls = {"n": 0}
    def flaky(method, url, headers, body):
        if "messages?" in url and calls["n"] == 0:
            calls["n"] += 1
            http.calls.append({"method": method, "url": url, "headers": headers, "body": body})
            return 401, {}, b"{}"
        return orig(method, url, headers, body)
    integ.http.transport = flaky
    http.on("POST", "https://oauth2.googleapis.com/token", 200, {"access_token": "AT-3", "expires_in": 3600})
    assert integ.search("x") == []
    assert http.calls[-1]["headers"]["Authorization"] == "Bearer AT-3"
    assert json.loads(kr.data[("Djorge Local", "google:token")])["refresh_token"] == "RT-1"


# -------------------------------------------------------------------------------------------------- sync
def _google_data(http):
    http.on("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events", 200, {"items": [
        {"id": "e1", "summary": "Dentista", "start": {"dateTime": "2026-11-05T10:00:00-03:00"},
         "end": {"dateTime": "2026-11-05T11:00:00-03:00"}, "htmlLink": "https://cal/e1"},
        {"id": "e2", "summary": "Feriado", "start": {"date": "2026-11-15"}, "end": {"date": "2026-11-16"}},
        {"id": "e3", "status": "cancelled"}]})
    http.on("GET", "https://tasks.googleapis.com/tasks/v1/users/@me/lists", 200, {"items": [{"id": "L1"}]})
    http.on("GET", "https://tasks.googleapis.com/tasks/v1/lists/L1/tasks", 200, {"items": [
        {"id": "t1", "title": "Pagar luz", "due": "2026-11-10T00:00:00.000Z", "status": "needsAction"}]})
    http.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages?", 200, {"messages": [{"id": "m1"}]})
    http.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1", 200, {
        "id": "m1", "snippet": "Sua fatura de R$ 230,00 vence amanhã. Pague o boleto.", "internalDate": "1793000000000",
        "payload": {"headers": [{"name": "From", "value": "Enel <cobranca@enel.com.br>"},
                                {"name": "Subject", "value": "Fatura de novembro"}]}})


def test_sync_google_mapeia_cards(env):
    integ, http, kr, cards, *_ = env
    _connect_google(integ, http)
    _google_data(http)
    r = integ.sync("google")
    assert r == {"imported": 4, "updated": 0}
    by = {c["source"]["external_id"]: c for c in cards.cards.values()}
    ev = by["cal:e1"]
    assert ev["kind"] == "evento" and ev["start"].startswith("2026-11-05T10") and not ev["all_day"]
    assert ev["source"]["integration"] == "google"
    assert by["cal:e2"]["all_day"] is True and "cal:e3" not in by
    assert by["task:t1"]["kind"] == "tarefa" and by["task:t1"]["due"].startswith("2026-11-10")
    m = by["mail:m1"]
    assert m["kind"] == "mensagem" and m["column"] == "entrada"
    assert "financeiro" in m["tags"] and "Triagem" in m["body"] and "pagar" in m["body"]
    assert m["people"][0]["email"] == "cobranca@enel.com.br"
    # segunda vez: atualiza; coluna movida pela pessoa é preservada
    by["cal:e1"]["column"] = "fazendo"
    assert integ.sync("google") == {"imported": 0, "updated": 4}
    assert by["cal:e1"]["column"] == "fazendo"
    assert next(i for i in integ.list() if i["id"] == "google")["last_sync"]


def test_sync_store_sem_upsert(env, tmp_path):
    _, http, kr, *_ = env
    cards = ListCards()
    integ = Integrations({"_dir": str(tmp_path)}, cards, None, transport=http, keyring_backend=kr)
    _connect_google(integ, http)
    _google_data(http)
    assert integ.sync("google")["imported"] == 4
    assert integ.sync("google") == {"imported": 0, "updated": 4}
    assert len(cards.items) == 4


def _connect_ms(integ, http):
    integ.configure("microsoft", {"client_id": "app-guid"})
    url = integ.connect("microsoft")["auth_url"]
    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert "offline_access" in q["scope"] and "Mail.Send" in q["scope"] and q["code_challenge_method"] == "S256"
    http.on("POST", "https://login.microsoftonline.com/common/oauth2/v2.0/token", 200,
            {"access_token": "MS-AT", "refresh_token": "MS-RT", "expires_in": 3600})
    http.on("GET", "https://graph.microsoft.com/v1.0/me?", 200, {"mail": "eu@outlook.com"})
    assert "Conectado" in integ.oauth_callback({"state": q["state"], "code": "C"})
    tok = [c for c in http.calls if "oauth2/v2.0/token" in c["url"]][-1]
    assert "client_secret" not in tok["body"].decode()       # cliente público


def test_sync_microsoft(env):
    integ, http, kr, cards, *_ = env
    _connect_ms(integ, http)
    G = "https://graph.microsoft.com/v1.0"
    http.on("GET", f"{G}/me/calendarView", 200, {"value": [{"id": "E", "subject": "Reunião", "isAllDay": False,
             "start": {"dateTime": "2026-11-04T13:00:00.0000000", "timeZone": "UTC"},
             "end": {"dateTime": "2026-11-04T14:00:00.0000000", "timeZone": "UTC"}}]})
    http.on("GET", f"{G}/me/todo/lists", 200, {"value": [{"id": "A=="}]})
    http.on("GET", f"{G}/me/todo/lists/A%3D%3D/tasks", 200, {"value": [{"id": "T", "title": "Relatório",
             "status": "notStarted", "dueDateTime": {"dateTime": "2026-11-06T00:00:00.0000000", "timeZone": "UTC"}}]})
    http.on("GET", f"{G}/me/mailFolders/inbox/messages", 200, {"value": [{"id": "M", "subject": "Proposta",
             "bodyPreview": "Pode revisar a proposta do cliente?", "from": {"emailAddress": {"name": "Ana", "address": "ana@x.com"}}}]})
    assert integ.sync("microsoft") == {"imported": 3, "updated": 0}
    by = {c["source"]["external_id"]: c for c in cards.cards.values()}
    assert by["cal:E"]["start"] == "2026-11-04T13:00:00Z"
    assert by["task:T"]["due"] == "2026-11-06T00:00:00Z"
    assert by["mail:M"]["kind"] == "mensagem" and by["mail:M"]["source"]["integration"] == "microsoft"


# -------------------------------------------------------------------------------------------------- send
def test_send_gmail_mime(env):
    integ, http, *_ = env
    _connect_google(integ, http)
    http.on("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", 200, {"id": "S1"})
    assert integ.send("google", "ana@x.com", "Olá, Ana!", subject="Oi") == {"ok": True, "id": "S1"}
    raw = json.loads(http.calls[-1]["body"])["raw"]
    mime = base64.urlsafe_b64decode(raw + "==").decode()
    assert "To: ana@x.com" in mime and "Subject: Oi" in mime and "From: eu@gmail.com" in mime
    assert integ.send("google", "b@x.com", "sem assunto")["ok"]
    assert "Subject" not in base64.urlsafe_b64decode(json.loads(http.calls[-1]["body"])["raw"] + "==").decode()


def test_send_graph(env):
    integ, http, *_ = env
    _connect_ms(integ, http)
    http.on("POST", "https://graph.microsoft.com/v1.0/me/sendMail", 202, None)
    assert integ.send("microsoft", "ana@x.com", "Oi", "Assunto")["ok"]
    m = json.loads(http.calls[-1]["body"])["message"]
    assert m["toRecipients"][0]["emailAddress"]["address"] == "ana@x.com" and m["subject"] == "Assunto"
    assert http.calls[-1]["headers"]["Authorization"] == "Bearer MS-AT"


def test_whatsapp(env):
    integ, http, kr, *_ = env
    assert "error" in integ.send("whatsapp", "5511999998888", "oi")
    integ.configure("whatsapp", {"token": "WA-TOKEN", "phone_id": "123", "version": "v21.0"})
    http.on("GET", "https://graph.facebook.com/v21.0/123", 200, {"display_phone_number": "+55 11 4000-0000"})
    assert integ.connect("whatsapp")["ok"]
    http.on("POST", "https://graph.facebook.com/v21.0/123/messages", 200, {"messages": [{"id": "wamid.1"}]})
    assert integ.send("whatsapp", "+55 (11) 99999-8888", "Oi!") == {"ok": True, "id": "wamid.1"}
    b = json.loads(http.calls[-1]["body"])
    assert b["to"] == "5511999998888" and b["type"] == "text" and b["text"]["body"] == "Oi!"
    http.on("POST", "https://graph.facebook.com/v21.0/123/messages", 400, {"error": {"code": 131047, "message": "re-engagement"}})
    r = integ.send("whatsapp", "5511999998888", "Oi!")
    assert r["error"] == "fora_da_janela_24h" and "24 h" in r["aviso"]
    http.on("POST", "https://graph.facebook.com/v21.0/123/messages", 200, {"messages": [{"id": "wamid.2"}]})
    assert integ.send("whatsapp", "5511999998888", "", template="lembrete", params=["Ana"])["id"] == "wamid.2"
    assert json.loads(http.calls[-1]["body"])["template"]["name"] == "lembrete"
    assert integ.sync("whatsapp") == {"imported": 0, "updated": 0, "note": "WhatsApp Business não importa itens"}
    integ.configure("whatsapp", {"version": "../x"})
    assert "error" in integ.send("whatsapp", "5511999998888", "oi")


def test_gemini_draft(env):
    integ, http, *_ = env
    g = next(i for i in integ.list() if i["id"] == "gemini")
    assert g["leaves_computer"] and any("Sai do computador" in f for f in g["features"])
    assert "error" in integ.draft("convite", "")
    integ.configure("gemini", {"api_key": "GEM-KEY"})
    http.on("POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent", 200,
            {"candidates": [{"content": {"parts": [{"text": "Oi Ana, tudo bem?"}]}}]})
    r = integ.draft("chamar Ana para o churrasco", "sábado 14h")
    assert r["text"] == "Oi Ana, tudo bem?" and r["leaves_computer"]
    assert "GEM-KEY" not in http.calls[-1]["url"] and http.calls[-1]["headers"]["x-goog-api-key"] == "GEM-KEY"


# ------------------------------------------------------------------------------------- erros e segredos
def test_erro_de_rede_nao_derruba(env):
    integ, http, *_ = env
    _connect_google(integ, http)
    http.fail = True
    r = integ.sync("google")
    assert "error" in r and "sem conexão" in r["error"]
    assert "error" in integ.send("google", "a@b.c", "x")
    assert integ.search("x") == []
    assert next(i for i in integ.list() if i["id"] == "google")["error"]
    assert "error" in integ.sync("inexistente")
    integ.configure("whatsapp", {"token": "t", "phone_id": "1"})
    assert "error" in integ.connect("whatsapp")


def test_segredos_fora_de_config_e_log(env, caplog):
    integ, http, kr, cards, clock, tmp = env
    caplog.set_level(logging.DEBUG, logger="djorge.integracoes")
    _connect_google(integ, http)
    _google_data(http)
    integ.configure("whatsapp", {"token": "WA-TOKEN", "phone_id": "123"})
    integ.configure("gemini", {"api_key": "GEM-KEY"})
    integ.sync("google")
    http.fail = True
    integ.sync("google")
    secrets = ["SEGREDO-GOOGLE", "AT-1", "RT-1", "CODE-123", "WA-TOKEN", "GEM-KEY"]
    disk = "".join(p.read_text("utf-8", "ignore") for p in tmp.rglob("*") if p.is_file())
    listing = json.dumps(integ.list())
    for s in secrets:
        assert s not in disk and s not in caplog.text and s not in listing
    assert "?" not in caplog.text                                # URLs sem query no log
    assert ("Djorge Local", "google:client_secret") in kr.data
    assert "error" in integ.configure("google", {"senha_qualquer": "x"})


def test_disconnect_e_background(env):
    integ, http, kr, *_ = env
    _connect_google(integ, http)
    assert integ.disconnect("google")["ok"]
    assert ("Djorge Local", "google:token") not in kr.data
    assert ("Djorge Local", "google:client_id") in kr.data
    integ.disconnect("google", forget=True)
    assert ("Djorge Local", "google:client_id") not in kr.data
    integ.start_background(every_min=1); integ.start_background()
    integ.stop_background()


def test_notifica_novidades(env):
    integ, http, *_ = env
    notes = []
    integ.notify = lambda t, m: notes.append(m)
    _connect_google(integ, http)
    _google_data(http)
    integ.sync_all(notify=True)
    assert notes and "4" in notes[0]
