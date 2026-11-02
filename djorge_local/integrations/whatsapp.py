"""WhatsApp Business Cloud API (oficial da Meta). Só envio: texto livre (dentro da janela de 24 h desde a última
mensagem da pessoa) ou modelo aprovado (fora da janela). Não recebe mensagens e não automatiza o WhatsApp Web."""
from __future__ import annotations

import re
import urllib.parse

from .base import NetError, Provider, api_error

AVISO_24H = ("A pessoa não fala com este número há mais de 24 h: o WhatsApp só aceita um modelo de mensagem "
             "aprovado. Envie de novo informando o modelo (template).")
FORA_JANELA = {131047, 131026, 470}


class WhatsApp(Provider):
    id, name, group, icon = "whatsapp", "WhatsApp Business", "Mensagens", "whatsapp"
    features = ["Enviar texto (janela de 24 h)", "Enviar modelo aprovado (fora da janela)"]
    needs = [{"key": "token", "label": "Token de acesso permanente (usuário do sistema, Meta Business)", "secret": True},
             {"key": "phone_id", "label": "Identificação do número de telefone (Phone number ID)", "secret": False},
             {"key": "version", "label": "Versão da Graph API (ex.: v21.0)", "secret": False, "optional": True}]

    def clear(self):
        self.store.delete(self.k("account"))

    def connected(self):
        return self.configured() and bool(self.account())

    def base(self):
        v = (self.value("version") or "v21.0").strip()
        if not re.fullmatch(r"v\d+\.\d+", v):
            raise NetError("versão da Graph API inválida (use algo como v21.0)")
        return f"https://graph.facebook.com/{v}/{urllib.parse.quote(self.value('phone_id') or '', safe='')}"

    def _call(self, method, url, body=None):
        st, obj = self.http.request(method, url, headers={"Authorization": "Bearer " + (self.value("token") or "")},
                                    json_body=body)
        return st, obj

    def verify(self):
        st, obj = self._call("GET", self.base() + "?fields=display_phone_number,verified_name")
        if st != 200:
            raise NetError(api_error(st, obj))
        acc = obj.get("display_phone_number") or obj.get("verified_name") or "conectado"
        self.store.set(self.k("account"), acc)
        return acc

    def send(self, to, body, subject=None, template=None, lang="pt_BR", params=None, **kw):
        num = re.sub(r"\D", "", str(to or ""))
        if len(num) < 8:
            return {"error": "número inválido: use o formato internacional, ex.: 5511999998888"}
        msg = {"messaging_product": "whatsapp", "to": num}
        if template:
            msg.update(type="template", template={"name": template, "language": {"code": lang}})
            if params:
                msg["template"]["components"] = [{"type": "body", "parameters": [{"type": "text", "text": str(p)}
                                                                                   for p in params]}]
        else:
            if not (body or "").strip():
                return {"error": "mensagem vazia"}
            msg.update(type="text", text={"body": body[:4096], "preview_url": False})
        st, obj = self._call("POST", self.base() + "/messages", msg)
        if st >= 400:
            code = (obj.get("error") or {}).get("code") if isinstance(obj, dict) else None
            if code in FORA_JANELA:
                return {"error": "fora_da_janela_24h", "aviso": AVISO_24H}
            return {"error": api_error(st, obj)}
        return {"ok": True, "id": ((obj.get("messages") or [{}])[0]).get("id")}
