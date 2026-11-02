"""Portão de ações (como o "gating" dos modelos de decisão): antes de o app AGIR, decide
    seguir      faz sozinho
    confirmar   mostra ao usuário e espera um toque
    bloquear    não faz (e explica)
Regras leves cobrem o que tem que ser exato (dinheiro, apagar, contato novo, horário de silêncio, link suspeito); o
DOFL, se houver, vota com uma pergunta SIM/NÃO calibrada. Na dúvida, confirma - nunca segue sozinho.

    from djorge.runtime.autorizacao import autorizar
    autorizar("enviar", {"destino": "edson@x.com", "texto": "...", "contato_conhecido": False}, engine=eng)
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from .recognizers import reconhecer

IRREVERSIVEL = {"apagar", "excluir", "cancelar assinatura", "enviar", "pagar", "transferir", "encaminhar", "postar"}
SEMPRE_CONFIRMA = {"pagar", "transferir", "apagar", "excluir", "postar"}


def autorizar(acao: str, ctx: Dict, engine=None, limiar_seguir: float = 0.85) -> Dict:
    acao = acao.casefold().strip()
    texto = str(ctx.get("texto") or "")
    motivos = []
    dados = reconhecer(texto) if texto else []
    if ctx.get("seguranca") and ctx["seguranca"].get("rotulo") not in (None, "seguro") and ctx["seguranca"].get("p", 0) >= 0.7:
        return {"decisao": "bloquear", "motivos": ["conteúdo marcado como ameaça"], "p": None}
    if acao in SEMPRE_CONFIRMA:
        motivos.append("ação com dinheiro, exclusão ou publicação")
    if any(d.campo in ("valor", "chave pix", "boleto") for d in dados) and acao in ("enviar", "encaminhar"):
        motivos.append("mensagem com valor ou dados de pagamento")
    if any(d.campo in ("cpf", "cnpj", "código") for d in dados):
        motivos.append("contém dado sensível ou código")
    if acao in ("enviar", "encaminhar") and not ctx.get("contato_conhecido", False):
        motivos.append("destinatário novo")
    if ctx.get("silencio"):
        motivos.append("horário de silêncio")
    if acao in ("enviar", "encaminhar") and len(texto) < 2:
        return {"decisao": "bloquear", "motivos": ["mensagem vazia"], "p": None}
    p = None
    if engine is not None:
        from .semantic import SemReq
        st = f"Ação: {acao}. Destino: {ctx.get('destino') or '-'}. Conteúdo: {texto[:800]}"
        r = engine.run([SemReq("NOUL", st, options=["É seguro o assistente fazer isto sem perguntar ao usuário."])])[0]
        p = float(r.get("p_true", r.get("probability", 0)))
        if p < 0.5:
            motivos.append(f"modelo pede confirmação ({p:.0%})")
    seguir = not motivos and (p is None or p >= limiar_seguir) and acao not in IRREVERSIVEL - {"enviar"}
    if acao == "enviar" and p is None and not motivos:
        seguir = bool(ctx.get("contato_conhecido"))
    return {"decisao": "seguir" if seguir else "confirmar", "motivos": motivos or ([] if seguir else ["sem certeza suficiente"]),
            "p": p}
