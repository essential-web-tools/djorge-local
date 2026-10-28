"""Rules from plain text: "Faça com que todos os e-mails da Ana sejam movidos para a pasta Trabalho" -> a decision
tree in the same YAML format as arvores.yaml, shown to the person before it is turned on.

The model never writes the tree. It only DECIDES (which events, which action) and MARKS words in the order
(person, e-mail address, folder, app, keyword); a template turns those decisions into the tree. Exact things are
taken by rules first (e-mail addresses, text in quotes); anything the model is unsure about becomes a question.
"""
from __future__ import annotations

import re
import unicodedata

SOURCES = [("e-mails", "email"), ("notificações do computador ou do celular", "notifications"),
           ("arquivos de uma pasta do computador", "folder"), ("textos copiados", "clipboard"),
           ("qualquer coisa que chegar", None)]
ACTIONS = [("mover o e-mail para uma pasta", "email_move"), ("mostrar um aviso na tela", "notify"),
           ("salvar o texto num arquivo", "write_file"), ("só registrar no histórico", "log"),
           ("ignorar, não fazer nada", "stop")]
SEC = [("nada sobre segurança", None), ("golpes, phishing ou vírus", ["golpe", "phishing", "malware"]),
       ("spam ou propaganda", ["spam"])]
FIELDS = {"pessoa": "pessoa", "email": "endereço de e-mail", "pasta": "pasta de e-mail", "app": "aplicativo",
          "palavra": "assunto ou palavra-chave", "arquivo": "nome do arquivo"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
QUOTED = re.compile(r"[\"“'‘«]([^\"”'’»]{1,80})[\"”'’»]")
FOLDER_HINT = re.compile(r"\b(?:pasta|folder|caixa|marcador|etiqueta)\s+(?:de\s+|chamada\s+)?[\"“']?([\wÀ-ÿ./ -]{1,40}?)[\"”']?(?=[,.;!?]|$|\s+(?:e|quando|se)\b)", re.I)


def _slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40] or "regra"


class RuleBuilder:
    def __init__(self, cfg, models):
        self.cfg, self.models = cfg, models

    def _engine(self):
        dm = self.models.decision_models()
        z = self.models.get(dm[0] if dm else "flash")
        return getattr(z.semantic, "engine", z.semantic)

    def from_text(self, text: str) -> dict:
        from djorge.runtime.semantic import SemReq
        text = " ".join(str(text or "").split())[:600]
        if len(text) < 6:
            raise ValueError("escreva o que a regra deve fazer")
        eng = self._engine()
        reqs = [SemReq("CHOICE", text, options=[s for s, _ in SOURCES], ask="De onde vêm as mensagens desta regra?"),
                SemReq("CHOICE", text, options=[a for a, _ in ACTIONS], ask="O que deve ser feito com elas?"),
                SemReq("CHOICE", text, options=[s for s, _ in SEC], ask="A regra fala de segurança?")]
        reqs += [SemReq("resolve", text, mode="ENTITY", target=t, threshold=0.5) for t in FIELDS.values()]
        out = eng.run(reqs)
        pick = lambda r, table: (table[[x for x, _ in table].index(r["answer"])][1], float(r.get("probability") or 0))
        src, p_src = pick(out[0], SOURCES)
        act, p_act = pick(out[1], ACTIONS)
        sec, p_sec = pick(out[2], SEC)
        found = {}
        for key, r in zip(FIELDS, out[3:]):
            vals = [text[m.start:m.end].strip(" ,.;:\"'“”") for m in r.matches]
            found[key] = [v for v in dict.fromkeys(vals) if v]
        # exact things first: e-mail addresses, text in quotes after "pasta"
        mails = EMAIL.findall(text)
        if mails:
            found["email"] = list(dict.fromkeys(mails))
            found["pessoa"] = [p for p in found["pessoa"] if not any(p in m for m in mails)]
        fh = FOLDER_HINT.search(text)
        if fh:
            found["pasta"] = [fh.group(1).strip()]
        elif not found["pasta"] and act == "email_move":
            q = QUOTED.findall(text)
            found["pasta"] = q[-1:] if q else []
        found["palavra"] = [w for w in found["palavra"] if w not in found["pasta"] + found["pessoa"]]
        if act == "email_move":                      # moving only exists for e-mails
            src = "email"

        when, questions, notes = {}, [], []
        if src:
            when["source_in"] = [src]
        senders = found["email"] + found["pessoa"]
        if senders:
            when["sender_has"] = senders
        if found["app"] and src in ("notifications", None):
            when["app_in"] = found["app"]
        if found["palavra"]:
            when["contains_any"] = found["palavra"]
        if sec and p_sec >= 0.6:                     # only when the order clearly talks about security
            when["seguranca_in"] = sec
        node = {"action": act}
        if act == "email_move":
            node.update(folder=found["pasta"][0] if found["pasta"] else "", uid="{uid}", mailbox="{mailbox}")
            if not found["pasta"]:
                questions.append({"field": "folder", "ask": "Para qual pasta os e-mails devem ir?"})
            imap = (self.cfg.get("connectors") or {}).get("imap") or {}
            if not imap.get("enabled"):
                notes.append("Ligue o conector de e-mail (IMAP) em Configurações para esta regra funcionar.")
            if not imap.get("allow_move"):
                notes.append("Em Configurações › E-mail, ligue “Permitir mover e-mails” — por padrão o Djorge só lê.")
        elif act == "notify":
            node.update(title="{title}", message="{sender}: {text:140}")
        elif act == "write_file":
            node.update(file=(found["arquivo"][0] if found["arquivo"] else _slug(text) + ".txt"),
                        content="{time} · {sender} · {title}\n{text}\n")
        elif act == "log":
            node.update(message="{source} · {sender} · {title}")
        if not (when.keys() - {"source_in"}):
            questions.append({"field": "condition",
                              "ask": "A regra vale para tudo que chegar? Se não, diga de quem, de qual app ou com qual palavra."})
        conf = round(min(p_src, p_act), 3)
        if conf < 0.6:
            notes.append("Não tenho certeza de que entendi: confira o resumo antes de ativar.")
        name = "regra-" + _slug(" ".join(senders[:1] + found["pasta"][:1] + found["palavra"][:1]) or act)
        tree = {"name": name, "enabled": True, "when": when, "start": "acao", "nodes": {"acao": node},
                "origem": text}
        return {"tree": tree, "summary": summarize(tree), "questions": questions, "notes": notes,
                "confidence": conf, "found": found,
                "decisions": {"source": [src, round(p_src, 3)], "action": [act, round(p_act, 3)], "security": [sec, round(p_sec, 3)]}}


def summarize(tree: dict) -> str:
    w, n = tree.get("when") or {}, tree["nodes"][tree["start"]]
    src = {v: k for k, v in SOURCES}.get((w.get("source_in") or [None])[0], "tudo que chegar")
    parts = [f"Quando chegar {src}" if w.get("source_in") else "Para tudo que chegar"]
    if w.get("sender_has"):
        parts.append("de " + " ou ".join(w["sender_has"]))
    if w.get("app_in"):
        parts.append("do app " + " ou ".join(w["app_in"]))
    if w.get("contains_any"):
        parts.append("com " + " ou ".join(f"“{x}”" for x in w["contains_any"]))
    if w.get("seguranca_in"):
        parts.append("marcado como " + " ou ".join(w["seguranca_in"]))
    act = {"email_move": f"mover para a pasta “{n.get('folder') or '?'}”", "notify": "mostrar um aviso",
           "write_file": f"salvar em {n.get('file')}", "log": "registrar no histórico", "stop": "ignorar"}[n["action"]]
    return " ".join(parts) + " → " + act + "."
