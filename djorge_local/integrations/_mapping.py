"""Conversão de itens externos (eventos, tarefas, e-mails) em campos de card do contrato (A)."""
from __future__ import annotations

PRIORIDADE = {"urgente": 3, "alta": 2, "normal": 1, "baixa": 0}


def mensagem_fields(integration, ext_id, remetente, assunto, texto, url=None, data=None):
    """E-mail recebido -> card kind "mensagem" na coluna "entrada", com a triagem no body/tags."""
    tri = {}
    try:
        from djorge.runtime.triagem import triar
        tri = triar(texto or "", remetente=remetente, assunto=assunto) or {}
    except Exception as e:                            # triagem nunca impede a importação
        tri = {"erro": type(e).__name__}
    linhas = [f"De: {remetente or '?'}", "", (texto or "").strip()[:4000]]
    if tri.get("categoria"):
        linhas += ["", "— Triagem —", f"Categoria: {tri['categoria']}", f"Prioridade: {tri.get('prioridade')}"]
        if tri.get("acoes"):
            linhas.append("Ações sugeridas: " + ", ".join(tri["acoes"]))
        if tri.get("destino"):
            linhas.append(f"Destino: {tri['destino']}")
    tags = [t for t in [integration, "e-mail", tri.get("categoria"), tri.get("prioridade")] if t]
    tags += [f"ação:{a}" for a in tri.get("acoes") or []]
    pessoas = [{"nome": remetente_nome(remetente), "tipo": "remetente", "email": remetente_email(remetente)}] \
        if remetente else []
    return {"kind": "mensagem", "title": (assunto or tri.get("resumo") or "(sem assunto)")[:200],
            "body": "\n".join(linhas), "column": "entrada", "priority": PRIORIDADE.get(tri.get("prioridade"), 1),
            "tags": tags, "people": pessoas, "due": tri.get("prazo") if isinstance(tri.get("prazo"), str) else None,
            "start": data, "source": {"integration": integration, "external_id": ext_id, "url": url}}


def evento_fields(integration, ext_id, title, start, end, all_day, body="", url=None, people=()):
    return {"kind": "evento", "title": (title or "(sem título)")[:200], "body": body or "", "start": start,
            "end": end, "all_day": bool(all_day), "people": list(people),
            "tags": [integration, "agenda"], "source": {"integration": integration, "external_id": ext_id, "url": url}}


def tarefa_fields(integration, ext_id, title, due, done, body="", url=None):
    return {"kind": "tarefa", "title": (title or "(sem título)")[:200], "body": body or "", "due": due,
            "done": bool(done), "column": "feito" if done else "entrada", "tags": [integration, "tarefa"],
            "source": {"integration": integration, "external_id": ext_id, "url": url}}


def remetente_email(s):
    s = s or ""
    if "<" in s and ">" in s:
        return s[s.index("<") + 1:s.index(">")].strip()
    return s.strip() if "@" in s else None


def remetente_nome(s):
    s = s or ""
    return (s[:s.index("<")].strip().strip('"') if "<" in s else s.split("@")[0]) or s
