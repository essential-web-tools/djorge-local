"""Google: Gmail (não lidos -> cards de mensagem, envio, busca), Calendar (próximos 30 dias) e Tasks.
OAuth 2.0 para app instalado ("Desktop app" no Google Cloud) com PKCE e redirect de loopback."""
from __future__ import annotations

import base64
import datetime as dt
import urllib.parse
from email.message import EmailMessage

from ._mapping import evento_fields, mensagem_fields, tarefa_fields
from .base import NetError, OAuthProvider

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3"
TASKS = "https://tasks.googleapis.com/tasks/v1"


class Google(OAuthProvider):
    id, name, group, icon = "google", "Google", "E-mail e agenda", "google"
    features = ["Gmail: ler não lidos", "Gmail: enviar", "Gmail: buscar", "Agenda (próximos 30 dias)", "Tarefas"]
    needs = [{"key": "client_id", "label": "ID do cliente OAuth (Google Cloud, tipo \"App para computador\")",
              "secret": False},
             {"key": "client_secret", "label": "Chave secreta do cliente OAuth (Google Cloud)", "secret": True}]
    auth_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    scopes = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send",
              "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/tasks"]
    extra_auth = {"access_type": "offline", "prompt": "consent"}
    max_mails = 20

    def fetch_account(self):
        return self.api("GET", f"{GMAIL}/profile").get("emailAddress")

    # ------------------------------------------------------------------------------------------------ sync
    def items(self):
        now = dt.datetime.now(dt.timezone.utc)
        q = urllib.parse.urlencode({"timeMin": now.isoformat(), "timeMax": (now + dt.timedelta(days=30)).isoformat(),
                                    "singleEvents": "true", "orderBy": "startTime", "maxResults": 250})
        for ev in self.api("GET", f"{CAL}/calendars/primary/events?{q}").get("items", []):
            if ev.get("status") == "cancelled":
                continue
            s, e = ev.get("start", {}), ev.get("end", {})
            people = [{"nome": a.get("displayName") or a.get("email"), "tipo": "participante", "email": a.get("email")}
                      for a in ev.get("attendees", []) if not a.get("self")]
            yield evento_fields("google", "cal:" + ev["id"], ev.get("summary"), s.get("dateTime") or s.get("date"),
                                e.get("dateTime") or e.get("date"), "date" in s,
                                "\n".join(x for x in [ev.get("location"), ev.get("description")] if x),
                                ev.get("htmlLink"), people)
        for lst in self.api("GET", f"{TASKS}/users/@me/lists?maxResults=100").get("items", []):
            q = urllib.parse.urlencode({"showCompleted": "false", "maxResults": 100})
            for t in self.api("GET", f"{TASKS}/lists/{urllib.parse.quote(lst['id'])}/tasks?{q}").get("items", []):
                if not t.get("title"):
                    continue
                yield tarefa_fields("google", "task:" + t["id"], t.get("title"), t.get("due"),
                                    t.get("status") == "completed", t.get("notes") or "", t.get("webViewLink"))
        q = urllib.parse.urlencode({"q": "is:unread in:inbox newer_than:7d", "maxResults": self.max_mails})
        for m in self.api("GET", f"{GMAIL}/messages?{q}").get("messages", []):
            yield self._mail_fields(self._mail(m["id"]))

    def _mail(self, mid):
        q = "format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date"
        return self.api("GET", f"{GMAIL}/messages/{urllib.parse.quote(mid)}?{q}")

    @staticmethod
    def _hdr(m):
        return {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}

    def _mail_fields(self, m):
        h = self._hdr(m)
        data = None
        if m.get("internalDate"):
            data = dt.datetime.fromtimestamp(int(m["internalDate"]) / 1000, dt.timezone.utc).isoformat()
        return mensagem_fields("google", "mail:" + m["id"], h.get("from"), h.get("subject"), m.get("snippet", ""),
                               f"https://mail.google.com/mail/u/0/#inbox/{m['id']}", data)

    # ------------------------------------------------------------------------------------------- send/search
    def send(self, to, body, subject=None, **kw):
        msg = EmailMessage()
        msg["To"] = to
        if subject:
            msg["Subject"] = subject
        acc = self.account()
        if acc:
            msg["From"] = acc
        msg.set_content(body or "")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = self.api("POST", f"{GMAIL}/messages/send", json_body={"raw": raw})
        return {"ok": True, "id": r.get("id")}

    def search(self, q, limit=10):
        qs = urllib.parse.urlencode({"q": q, "maxResults": limit})
        out = []
        for m in self.api("GET", f"{GMAIL}/messages?{qs}").get("messages", []):
            full = self._mail(m["id"]); h = self._hdr(full)
            out.append({"source": "google", "kind": "e-mail", "id": m["id"], "title": h.get("subject") or "(sem assunto)",
                        "snippet": full.get("snippet", ""), "from": h.get("from"), "date": h.get("date"),
                        "url": f"https://mail.google.com/mail/u/0/#all/{m['id']}"})
        return out
