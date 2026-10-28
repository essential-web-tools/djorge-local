"""Automatic security check: every event that arrives (notification, file, clipboard, e-mail, POST /api/event)
gets a verdict from the Flash before the trees run: seguro | spam | golpe | phishing | malware.

The verdict goes into the event as  seguranca  and  seguranca_p  (probability), so trees can branch on it
(when: {seguranca_in: [golpe, phishing]}) and texts can use {seguranca}. A desktop alert is shown for the
threats listed in  seguranca.alert_on  above  seguranca.min_probability. Nothing leaves the computer.
"""
from __future__ import annotations

import time

LABELS = ["seguro", "spam", "golpe", "phishing", "malware"]
DESC = ["legítima, sem ameaça", "propaganda em massa não solicitada",
        "engana a pessoa para pagar ou transferir dinheiro",
        "rouba senhas, códigos ou dados de cartão se passando por alguém",
        "entrega programa ou arquivo malicioso"]
ASK = "Esta mensagem é segura ou uma ameaça?"
TITLE = {"golpe": "Possível golpe", "phishing": "Possível phishing", "malware": "Possível arquivo malicioso",
         "spam": "Provável spam"}


class SecurityCheck:
    def __init__(self, cfg, models, notify):
        self.cfg, self.models, self.notify = cfg, models, notify
        self.stats = {"checked": 0, "threats": 0, "ms": 0.0}

    @property
    def opts(self):
        return self.cfg.get("seguranca") or {}

    def model_name(self):
        m = self.opts.get("model") or "flash"
        dm = self.models.decision_models()
        return m if m in dm else (dm[0] if dm else None)

    def check(self, ev: dict) -> dict:
        o = self.opts
        if not o.get("enabled", True):
            return ev
        text = f"{ev.get('title') or ''}\n{ev.get('text') or ''}".strip()
        if len(text) < int(o.get("min_chars", 12)) or "djorge" in (ev.get("app") or "").casefold():
            return ev                                # too short, or our own alert coming back
        name = self.model_name()
        if not name:
            return ev
        try:
            z = self.models.get(name)
            from djorge.runtime.semantic import SemReq
            eng = getattr(z.semantic, "engine", z.semantic)
            t0 = time.perf_counter()
            r = eng.run([SemReq("CHOICE", text[:2000], options=LABELS, descriptions=DESC, ask=ASK)])[0]
            ms = (time.perf_counter() - t0) * 1000
        except Exception as e:                       # the check never blocks the event
            ev["seguranca_erro"] = f"{type(e).__name__}: {e}"[:200]
            return ev
        lab, p = r.get("answer"), float(r.get("probability") or 0)
        ev["seguranca"], ev["seguranca_p"] = lab, round(p, 3)
        self.stats["checked"] += 1
        self.stats["ms"] = round(0.9 * self.stats["ms"] + 0.1 * ms, 1) if self.stats["checked"] > 1 else round(ms, 1)
        if lab != "seguro":
            self.stats["threats"] += 1
        if lab in (o.get("alert_on") or []) and p >= float(o.get("min_probability", 0.7)) and o.get("alert", True):
            who = ev.get("app") or ev.get("sender") or ev.get("source") or ""
            self.notify(f"⚠ {TITLE.get(lab, lab)} ({p:.0%})", (f"{who}: " if who else "") + text[:140])
        return ev
