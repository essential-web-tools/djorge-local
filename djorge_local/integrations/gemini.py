"""Gemini (Google AI) — OPCIONAL, só para rascunhar o texto de uma mensagem quando a pessoa pede.
ATENÇÃO: a instrução e o contexto SAEM DO COMPUTADOR e vão para os servidores do Google."""
from __future__ import annotations

import urllib.parse

from .base import NetError, Provider, api_error

API = "https://generativelanguage.googleapis.com/v1beta/models"
AVISO = "Sai do computador: o texto enviado para rascunho vai para os servidores do Google (Gemini)."


class Gemini(Provider):
    id, name, group, icon = "gemini", "Gemini (rascunhos)", "Assistente externo", "gemini"
    features = ["Rascunho de mensagem quando você pede", AVISO]
    needs = [{"key": "api_key", "label": "Chave de API do Google AI Studio", "secret": True},
             {"key": "model", "label": "Modelo (opcional, padrão gemini-2.5-flash)", "secret": False, "optional": True}]
    leaves_computer = True

    def verify(self):
        self.store.set(self.k("account"), "chave configurada")
        return "chave configurada"

    def draft(self, instrucao: str, contexto: str = "") -> str:
        modelo = urllib.parse.quote((self.value("model") or "gemini-2.5-flash").strip(), safe="-.")
        prompt = ("Escreva em português do Brasil apenas o texto da mensagem pedida, sem comentários.\n\n"
                  f"Pedido: {instrucao}\n\nContexto:\n{contexto or '(nenhum)'}")
        # a chave vai no cabeçalho, nunca na URL
        st, obj = self.http.request("POST", f"{API}/{modelo}:generateContent",
                                    headers={"x-goog-api-key": self.value("api_key") or ""},
                                    json_body={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                               "generationConfig": {"temperature": 0.6, "maxOutputTokens": 1024}})
        if st != 200:
            raise NetError(api_error(st, obj))
        try:
            parts = obj["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            raise NetError("o Gemini não devolveu texto") from None
        return "".join(p.get("text", "") for p in parts).strip()
