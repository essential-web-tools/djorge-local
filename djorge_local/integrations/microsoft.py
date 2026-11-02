"""Microsoft (Graph): Outlook (não lidos, envio, busca), Calendário (próximos 30 dias) e To Do.
Microsoft identity platform, cliente público com PKCE, tenant "common" (contas pessoais e de trabalho)."""
from __future__ import annotations

import datetime as dt
import urllib.parse

from ._mapping import evento_fields, mensagem_fields, tarefa_fields
from .base import OAuthProvider

GRAPH = "https://graph.microsoft.com/v1.0"


class Microsoft(OAuthProvider):
    id, name, group, icon = "microsoft", "Microsoft (Outlook)", "E-mail e agenda", "microsoft"
    features = ["Outlook: ler não lidos", "Outlook: enviar", "Outlook: buscar", "Calendário (próximos 30 dias)",
                "To Do"]
    needs = [{"key": "client_id", "label": "ID do aplicativo (cliente) registrado no Microsoft Entra / Azure",
              "secret": False}]
    auth_endpoint = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    token_endpoint = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    scopes = ["offline_access", "User.Read", "Mail.Read", "Mail.Send", "Calendars.Read", "Tasks.Read"]
    scopes_on_refresh = True
    extra_auth = {"response_mode": "query"}
    max_mails = 20

    def client_secret(self):
        return None                                     # cliente público: nunca envia segredo

    def fetch_account(self):
        me = self.api("GET", f"{GRAPH}/me?$select=mail,userPrincipalName")
        return me.get("mail") or me.get("userPrincipalName")

    @staticmethod
    def _dt(x):
        if not x or not x.get("dateTime"):
            return None
        s = x["dateTime"].split(".")[0]
        return s + "Z" if (x.get("timeZone") or "UTC").upper() == "UTC" else s

    def items(self):
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        q = urllib.parse.urlencode({"startDateTime": now.isoformat(), "endDateTime": (now + dt.timedelta(days=30)).isoformat(),
                                    "$top": 250, "$orderby": "start/dateTime",
                                    "$select": "id,subject,start,end,isAllDay,location,bodyPreview,webLink,attendees,isCancelled"})
        for ev in self.api("GET", f"{GRAPH}/me/calendarView?{q}", headers={"Prefer": 'outlook.timezone="UTC"'}).get("value", []):
            if ev.get("isCancelled"):
                continue
            people = [{"nome": a.get("emailAddress", {}).get("name"), "tipo": "participante",
                       "email": a.get("emailAddress", {}).get("address")} for a in ev.get("attendees", [])]
            loc = (ev.get("location") or {}).get("displayName")
            yield evento_fields("microsoft", "cal:" + ev["id"], ev.get("subject"), self._dt(ev.get("start")),
                                self._dt(ev.get("end")), ev.get("isAllDay"),
                                "\n".join(x for x in [loc, ev.get("bodyPreview")] if x), ev.get("webLink"), people)
        for lst in self.api("GET", f"{GRAPH}/me/todo/lists").get("value", []):
            q = urllib.parse.urlencode({"$filter": "status ne 'completed'", "$top": 100})
            lid = urllib.parse.quote(lst["id"], safe="")
            for t in self.api("GET", f"{GRAPH}/me/todo/lists/{lid}/tasks?{q}").get("value", []):
                yield tarefa_fields("microsoft", "task:" + t["id"], t.get("title"), self._dt(t.get("dueDateTime")),
                                    t.get("status") == "completed", (t.get("body") or {}).get("content") or "")
        q = urllib.parse.urlencode({"$filter": "isRead eq false", "$top": self.max_mails,
                                    "$orderby": "receivedDateTime desc",
                                    "$select": "id,subject,from,bodyPreview,receivedDateTime,webLink"})
        for m in self.api("GET", f"{GRAPH}/me/mailFolders/inbox/messages?{q}").get("value", []):
            yield self._mail_fields(m)

    @staticmethod
    def _from(m):
        a = (m.get("from") or {}).get("emailAddress") or {}
        return f"{a.get('name')} <{a.get('address')}>" if a.get("name") else a.get("address")

    def _mail_fields(self, m):
        return mensagem_fields("microsoft", "mail:" + m["id"], self._from(m), m.get("subject"),
                               m.get("bodyPreview", ""), m.get("webLink"), m.get("receivedDateTime"))

    def send(self, to, body, subject=None, **kw):
        msg = {"message": {"subject": subject or "", "body": {"contentType": "Text", "content": body or ""},
                           "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in str(to).split(",") if a.strip()]},
               "saveToSentItems": True}
        self.api("POST", f"{GRAPH}/me/sendMail", json_body=msg)     # 202 sem corpo
        return {"ok": True, "id": None}

    def search(self, q, limit=10):
        qs = urllib.parse.urlencode({"$search": '"' + q.replace('"', " ") + '"', "$top": limit,
                                     "$select": "id,subject,from,bodyPreview,receivedDateTime,webLink"})
        return [{"source": "microsoft", "kind": "e-mail", "id": m["id"], "title": m.get("subject") or "(sem assunto)",
                 "snippet": m.get("bodyPreview", ""), "from": self._from(m), "date": m.get("receivedDateTime"),
                 "url": m.get("webLink")}
                for m in self.api("GET", f"{GRAPH}/me/messages?{qs}", headers={"ConsistencyLevel": "eventual"}).get("value", [])]
