"""Actions at the end of a decision path. Side effects are opt-in and, by default, wait for confirmation
in the dashboard (actions.require_confirmation). Nothing here sends anything anywhere unless configured.

  notify      desktop notification (title, message)
  log         line in the history (message)
  write_file  append text to a file inside actions.write_dir (file, content)
  webhook     POST JSON to a URL whose prefix is in actions.webhook_allow (url, body)
  command     run an executable listed in actions.command_allow, argv as a list (no shell)
  email_move  move the e-mail of this event to another folder (IMAP; needs connectors.imap.allow_move)
  stop        end this path

v2.1 (secretário; só preparam coisas DENTRO do app, nada sai do computador):
  criar_card / tarefa / alerta   card com título e os campos extraídos (valor, prazo, remetente, ...)
  notificar                      toast nativo do Windows (toasts.py)
  etiquetar / arquivar           muda a notificação/mensagem no store de notificações
  abrir_app                      prepara e abre WhatsApp (whatsapp:// ou wa.me), navegador ou e-mail (mailto:);
                                 quem envia é a pessoa
  responder_sugestao             monta um rascunho de resposta + link para abrir no app (não abre nem envia)
Com ctx["mode"] == "testar" nada é executado; com "sugerir" a ação volta como sugestão para a pessoa aprovar.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from urllib.parse import quote

from .tree import NOVAS_ACOES, render

CAMPO_ROTULO = {"valor": "Valor", "prazo": "Prazo", "remetente": "Quem mandou", "empresa": "Empresa",
                "data": "Data", "hora": "Hora", "link": "Link", "codigo": "Código", "categoria": "Categoria",
                "telefone": "Telefone", "email": "E-mail"}


def _limpa_titulo(t: str) -> str:
    t = " ".join(str(t or "").split())
    t = re.sub(r"(\s*[—–\-·:|,]\s*)+$", "", t)
    t = re.sub(r"^(\s*[—–\-·:|,]\s*)+", "", t)
    return t[:200]


def montar_card(spec: dict, ev: dict) -> dict:
    """Card (formato de cards.py) a partir dos parâmetros da ação e dos campos do evento."""
    kind = spec["action"]
    campos = [str(c) for c in (spec.get("campos") or [])]
    linhas = [f"{CAMPO_ROTULO.get(c, c.capitalize())}: {ev.get(c)}" for c in campos if ev.get(c)]
    resumo = str(ev.get("text") or "").strip()[:600]
    body = "\n".join(linhas + ([""] if linhas and resumo else []) + ([resumo] if resumo else []))
    padrao = {"alerta": "Alerta — {remetente}", "tarefa": "{title}", "criar_card": "{title}"}[kind]
    titulo = _limpa_titulo(spec.get("titulo") or render(padrao, {"event": ev, "results": {}})) or \
        _limpa_titulo(ev.get("title")) or "Mensagem recebida"
    tags = [t for t in [spec.get("etiqueta"), "alerta" if kind == "alerta" else None] if t]
    prio = spec.get("prioridade")
    prio = int(prio) if str(prio or "").isdigit() else (2 if kind == "alerta" else 1)
    card = {"title": titulo, "body": body, "kind": spec.get("tipo") or ("tarefa" if kind == "tarefa" else
                                                                         "lembrete" if kind == "alerta" else "nota"),
            "column": spec.get("coluna") or ("hoje" if kind == "alerta" else "entrada"), "priority": min(3, prio),
            "tags": tags}
    if ev.get("prazo_iso") and kind in ("tarefa", "alerta", "criar_card"):
        card["due"] = ev["prazo_iso"]
    if card["kind"] == "evento" and ev.get("inicio_iso"):
        card["start"] = ev["inicio_iso"]
    pessoa = ev.get("remetente")
    if pessoa:
        card["people"] = [{"nome": pessoa, "tipo": "pessoa", "email": ev.get("remetente_email") or None}]
    if ev.get("link") and "link" not in campos:
        card["body"] = (card["body"] + "\n" if card["body"] else "") + f"Link: {ev['link']}"
    return card


def _open_url(url: str) -> bool:
    """Abre um endereço no app padrão (Windows: os.startfile; outros: webbrowser). Os testes trocam esta função."""
    if platform.system() == "Windows":
        os.startfile(url)                              # type: ignore[attr-defined]
        return True
    import webbrowser
    return bool(webbrowser.open(url))


def link_app(spec: dict) -> dict:
    """Monta o endereço para abrir: whatsapp | wa.me | navegador | email. Nunca envia nada."""
    app = str(spec.get("app") or "whatsapp").casefold()
    texto = str(spec.get("texto") or "")
    if app in ("whatsapp", "wa", "wa.me", "whatsapp_web"):
        fone = re.sub(r"\D", "", str(spec.get("para") or spec.get("telefone") or ""))
        if fone and len(fone) <= 11:
            fone = "55" + fone                       # número brasileiro sem DDI
        if app == "whatsapp" and not spec.get("web"):
            url = f"whatsapp://send?phone={fone}&text={quote(texto)}"
        else:
            url = f"https://wa.me/{fone}?text={quote(texto)}"
        return {"app": "whatsapp", "url": url, "web": f"https://wa.me/{fone}?text={quote(texto)}"}
    if app in ("email", "e-mail", "mailto"):
        para = str(spec.get("para") or "").strip()
        q = "&".join(f"{k}={quote(str(v))}" for k, v in (("subject", spec.get("assunto")), ("body", texto)) if v)
        return {"app": "email", "url": f"mailto:{quote(para, safe='@.+')}" + (f"?{q}" if q else "")}
    url = str(spec.get("url") or "")
    if not url.startswith(("https://", "http://")):
        raise ValueError("endereço inválido: use https://")
    return {"app": "navegador", "url": url}


def abrir_app(spec: dict) -> dict:
    l = link_app(spec)
    try:
        ok = _open_url(l["url"])
    except Exception:
        ok = False
    if not ok and l.get("web"):                      # sem o WhatsApp instalado -> WhatsApp Web
        try:
            ok = _open_url(l["web"]); l["url"] = l["web"]
        except Exception:
            ok = False
    return {"action": "abrir_app", "status": "aberto" if ok else "não abriu", **l}


def desktop_notify(title: str, message: str):
    title, message = title[:120], message[:400]
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name="Djorge Local", timeout=8)
        return True
    except Exception:
        pass
    sysname = platform.system()
    try:
        if sysname == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=5)
            return True
        if sysname == "Darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                           timeout=5)
            return True
    except Exception:
        pass
    print(f"[aviso] {title}: {message}")
    return False


class Actions:
    def __init__(self, cfg, history):
        self.cfg, self.hist = cfg, history
        self.pending = {}
        self.lock = threading.Lock()
        self.hooks = {}          # ação nova -> fn(spec, ctx) (o App liga cards, notificações e toasts aqui)

    def run(self, n: dict, ctx: dict, tree: str, nid: str) -> dict:
        kind = n["action"]
        spec = {k: render(v, ctx) if isinstance(v, str) else
                ([render(x, ctx) for x in v] if isinstance(v, list) else v) for k, v in n.items()}
        if kind == "stop":
            return {"action": "stop"}
        if kind in NOVAS_ACOES:
            return self.nova(spec, ctx)
        if kind in self.cfg["actions"]["require_confirmation"] and not n.get("auto", False):
            pid = uuid.uuid4().hex[:10]
            with self.lock:
                self.pending[pid] = {"id": pid, "tree": tree, "node": nid, "spec": spec, "created": time.time()}
            return {"action": kind, "status": "aguardando confirmação", "pending_id": pid}
        return self.execute(spec)

    def preparar(self, spec: dict, ctx: dict) -> dict:
        """O que a ação faria (para testar e para sugestões), sem efeito nenhum."""
        kind, ev = spec["action"], ctx.get("event") or {}
        out = {"action": kind}
        if kind in ("criar_card", "tarefa", "alerta"):
            out["card"] = montar_card(spec, ev)
        elif kind == "notificar":
            out["titulo"] = _limpa_titulo(spec.get("titulo") or spec.get("title") or ev.get("title") or "Djorge")
            out["mensagem"] = str(spec.get("mensagem") or spec.get("message") or ev.get("text") or "")[:300]
        elif kind == "etiquetar":
            out["etiqueta"] = str(spec.get("etiqueta") or ev.get("categoria") or "").strip()[:40]
        elif kind in ("abrir_app", "responder_sugestao"):
            sp = dict(spec)
            sp.setdefault("para", ev.get("telefone") or ev.get("remetente_email") or "")
            if kind == "responder_sugestao":
                sp.setdefault("app", "email" if ev.get("remetente_email") and not ev.get("telefone") else "whatsapp")
                sp["texto"] = spec.get("texto") or f"Olá{', ' + ev['remetente'].split()[0] if ev.get('remetente') else ''}! Recebi sua mensagem e já respondo."
            out["texto"] = sp.get("texto", "")
            try:
                out.update(link_app(sp))
            except ValueError as e:
                out["erro"] = str(e)
        return out

    def nova(self, spec: dict, ctx: dict) -> dict:
        mode = ctx.get("mode")
        prep = self.preparar(spec, ctx)
        if mode == "testar":
            return dict(prep, status="simulado")
        if mode == "sugerir" and spec["action"] != "etiquetar":   # etiquetar é só organização: sempre vale
            return dict(prep, status="sugerido", spec=spec)
        return self.executar_nova(spec, ctx, prep)

    def executar_nova(self, spec: dict, ctx: dict, prep: dict = None) -> dict:
        kind = spec["action"]
        prep = prep or self.preparar(spec, ctx)
        try:
            if kind == "abrir_app":
                sp = dict(spec)
                if not sp.get("para"):
                    ev = ctx.get("event") or {}
                    sp["para"] = ev.get("telefone") or ev.get("remetente_email") or ""
                return abrir_app(sp)
            if kind == "responder_sugestao":
                return dict(prep, status="pronto")             # só o rascunho; a pessoa abre e envia
            fn = self.hooks.get(kind)
            if fn is None:
                if kind == "notificar":
                    ok = desktop_notify(prep["titulo"], prep["mensagem"])
                    return dict(prep, status="ok" if ok else "mostrado no console")
                return dict(prep, status="indisponível")
            r = fn(spec, ctx, prep) or {}
            return dict(prep, **r)
        except Exception as e:
            return dict(prep, status="erro", error=f"{type(e).__name__}: {e}")

    def confirm(self, pid: str, approve: bool) -> dict:
        with self.lock:
            p = self.pending.pop(pid, None)
        if not p:
            return {"error": "pendência não encontrada"}
        if not approve:
            self.hist.add({"type": "action", "status": "recusada", **p})
            return {"status": "recusada"}
        r = self.execute(p["spec"])
        self.hist.add({"type": "action", "status": "executada", **p, "result": r})
        return r

    def execute(self, s: dict) -> dict:
        kind, A = s["action"], self.cfg["actions"]
        try:
            if kind == "notify":
                ok = A.get("notify", True) and desktop_notify(s.get("title", "Djorge"), s.get("message", ""))
                return {"action": kind, "status": "ok" if ok else "mostrado no console"}
            if kind == "log":
                self.hist.add({"type": "log", "message": s.get("message", "")})
                return {"action": kind, "status": "ok"}
            if kind == "write_file":
                base = Path(A["write_dir"]).expanduser().resolve()
                base.mkdir(parents=True, exist_ok=True)
                p = (base / s.get("file", "saida.txt")).resolve()
                if not str(p).startswith(str(base) + os.sep):
                    return {"action": kind, "status": "negado", "reason": "fora de actions.write_dir"}
                with open(p, "a", encoding="utf-8") as f:
                    f.write(s.get("content", "") + "\n")
                return {"action": kind, "status": "ok", "file": str(p)}
            if kind == "webhook":
                url = s.get("url", "")
                if not any(url.startswith(pre) for pre in A["webhook_allow"]) or not url.startswith(("https://",
                                                                                                    "http://127.0.0.1",
                                                                                                    "http://localhost")):
                    return {"action": kind, "status": "negado", "reason": "URL fora de actions.webhook_allow"}
                import urllib.request
                body = s.get("body") or {}
                req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    return {"action": kind, "status": r.status}
            if kind == "command":
                argv = s.get("argv") or []
                if not argv or Path(argv[0]).name not in A["command_allow"]:
                    return {"action": kind, "status": "negado", "reason": "programa fora de actions.command_allow"}
                p = subprocess.run(argv, capture_output=True, text=True, timeout=int(s.get("timeout", 30)))
                return {"action": kind, "status": p.returncode, "stdout": p.stdout[-2000:], "stderr": p.stderr[-1000:]}
            if kind == "email_move":
                return self._email_move(s)
        except Exception as e:
            return {"action": kind, "status": "erro", "error": f"{type(e).__name__}: {e}"}
        return {"action": kind, "status": "desconhecida"}

    def _email_move(self, s: dict) -> dict:
        import imaplib
        c = (self.cfg.get("connectors") or {}).get("imap") or {}
        uid, folder = str(s.get("uid") or "").strip(), str(s.get("folder") or "").strip()
        if not c.get("allow_move"):
            return {"action": "email_move", "status": "negado", "reason": "ligue connectors.imap.allow_move"}
        if not uid.isdigit() or not folder or len(folder) > 100 or any(ch in folder for ch in '\r\n"'):
            return {"action": "email_move", "status": "negado", "reason": "e-mail ou pasta inválidos"}
        pw = os.environ.get(c.get("password_env", "DJORGE_IMAP_PASSWORD"))
        with imaplib.IMAP4_SSL(c["host"], int(c.get("port", 993))) as M:
            M.login(c["user"], pw)
            M.select(s.get("mailbox") or c.get("folder", "INBOX"))
            q = f'"{folder}"'
            if M.list('""', q)[1] in ([None], []):
                M.create(q)
            caps = {x.decode().upper() if isinstance(x, bytes) else str(x).upper() for x in (M.capabilities or ())}
            if "MOVE" in caps:
                typ, _ = M.uid("MOVE", uid, q)
            else:                                       # COPY + mark; expunge only this message when possible
                typ, _ = M.uid("COPY", uid, q)
                if typ == "OK":
                    M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                    if "UIDPLUS" in caps:
                        M.uid("EXPUNGE", uid)
        return {"action": "email_move", "status": "ok" if typ == "OK" else "erro", "folder": folder}
