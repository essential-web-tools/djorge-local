"""Origo safety layer: light, multilingual (pt/en/es), two stages, never slows clean requests.

Stage 1 - lexical gate (always on, ~0.1 ms per KB): normalised text (case, accents, leetspeak, spaced-out
          letters, zero-width characters) is matched against compiled cue patterns per category.
Stage 2 - learned check (optional): when the `safety` specialist LoRA is installed and stage 1 is unsure
          (weak cue) - or always, with mode="strict" - ONE extra forward classifies the text into the same
          categories. Clean text with no cue costs nothing in mode="auto".

Why mostly FLAG and rarely BLOCK: Origo does not write text; it analyses text the caller already has. Analysing a
news story about weapons or a support ticket that mentions suicide is legitimate and often protective. So:
  * block  - building discriminatory profiling queries (classifying people as criminal/dangerous/inferior by a
             protected attribute) and any sexual content involving minors;
  * flag   - self-harm (with support resources), violence/weapons, illegal drugs, cyber abuse, manipulation /
             jailbreak attempts, hate/harassment text. The caller decides what to do with the flag.
Policy is configurable per category ("allow" | "flag" | "block").
Nothing is stored: only the category names travel in the response; the text is never logged.
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Dict, Iterable, List, Optional

CATEGORIES = ["self_harm", "violence_weapons", "hate_harassment", "sexual_minors", "illegal_drugs",
              "cyber_abuse", "manipulation", "discriminatory_profiling"]

DEFAULT_POLICY = {"self_harm": "flag", "violence_weapons": "flag", "hate_harassment": "flag",
                  "sexual_minors": "block", "illegal_drugs": "flag", "cyber_abuse": "flag",
                  "manipulation": "flag", "discriminatory_profiling": "block"}

SUPPORT = {
    "self_harm": {"pt": "Se você ou alguém estiver em risco, ligue 188 (CVV, gratuito, 24h) ou procure o SAMU 192.",
                  "en": "If you or someone else is at risk, contact local emergency services or find a helpline at findahelpline.com.",
                  "es": "Si tú o alguien está en riesgo, contacta a los servicios de emergencia o busca ayuda en findahelpline.com."}}

# Cue patterns operate on NORMALISED text (lowercase, no accents, leetspeak undone). Strong cues -> score 1.0,
# weak cues -> 0.5 (weak alone = "unsure": stage 2 decides when the safety specialist is installed).
_STRONG = {
    "self_harm": [r"\b(me matar|me suicidar|suicid\w*|tirar (a )?minha (propria )?vida|acabar com (a )?minha vida|"
                  r"kill myself|end my life|take my (own )?life|want to die|quero morrer|nao quero mais viver|"
                  r"self[- ]?harm|automutila\w*|me cortar|cutting myself|hurt myself|me machucar|autolesion\w*|"
                  r"quitarme la vida|matarme|no quiero vivir)\b"],
    "violence_weapons": [r"\b(make|build|fabricar|fazer|construir|hacer)\w* (a |an |uma? |una? )?(bomb|bomba|explosiv\w*|"
                         r"pipe bomb|molotov|arma caseira|homemade gun|ghost gun)\b",
                         r"\b(vou|i will|i'm going to|voy a) (te |you )?(matar|kill|shoot|atirar|esfaquear|stab|matarte)\b",
                         r"\b(mass shooting|massacre|ataque terrorista|terrorist attack|atentado)\b"],
    "illegal_drugs": [r"\b(synthesi[sz]e|sintetizar|cook|cozinhar|fabricar|produzir|make)\w* (meth|metanfetamina|crack|"
                      r"cocain\w*|heroin\w*|fentanyl|fentanil|lsd|mdma|ecstasy)\b"],
    "cyber_abuse": [r"\b(create|write|criar|escrever|crear|build|make|fazer)\w* (a |an |um |uma |un |una )?(ransomware|keylogger|botnet)\b",
                    r"\b(credential stuffing|phishing kit|kit de phishing|"
                    r"roubar senhas?|steal passwords?|invadir (a )?conta|hack (into )?(an? |the )?account|"
                    r"sql injection payload|bypass (2fa|mfa|authentication))\b"],
    "manipulation": [r"\b(ignore|ignora|ignorar|disregard|esqueca|olvida)\w* (all |todas |as |las |the |your )*"
                     r"(previous |anteriores |prior )?(instructions|instrucoes|instrucciones|rules|regras|reglas)\b",
                     r"\b(jailbreak|dan mode|modo dan|developer mode|modo desenvolvedor|modo desarrollador|"
                     r"system prompt|prompt do sistema|sem restricoes|without (any )?restrictions|no rules|sin reglas)\b",
                     r"\b(finja|pretend|finge|act as if) (que )?(you |voce |tu )?(have no|nao tem|no tienes) "
                     r"(rules|limits|regras|limites|reglas)\b"],
    "hate_harassment": [r"\b(raca inferior|inferior race|raza inferior|deveriam morrer|should (all )?die|deberian morir|"
                        r"exterminar|exterminate|limpeza etnica|ethnic cleansing|limpieza etnica)\b"],
}
_WEAK = {
    "self_harm": [r"\b(sem esperanca|hopeless|sin esperanza|nao aguento mais|can't go on|cant go on|no puedo mas|"
                  r"overdose|sobredosis|remedios? para (dormir|morrer)|pills? to die)\b"],
    "violence_weapons": [r"\b(arma|armas|weapon\w*|gun|guns|pistola|rifle|fuzil|faca|knife|explosiv\w*|municao|ammo|"
                         r"ammunition|matar|kill|atirar|shoot)\b"],
    "illegal_drugs": [r"\b(cocaina|cocaine|crack|heroina|heroin|meth|metanfetamina|fentanil|fentanyl|lsd|mdma|ecstasy|"
                      r"maconha|marijuana|traficar|trafficking|traficante)\b"],
    "cyber_abuse": [r"\b(ransomware|keylogger|botnet|ddos|hack\w*|invadir|exploit\w*|malware|virus|phishing|senha|password|bypass)\b"],
    "hate_harassment": [r"\b(odeio|i hate|odio a|lixo humano|subhuman|sub-humano|vermes?|vermin|macaco|monkey)\b"],
}
# Profiling: a protected attribute AND a judgement word inside the TARGET / OPTIONS of a semantic query.
_PROTECTED = (r"\b(raca|race|raza|etnia|ethnicity|etnico|negr[oa]s?|black people|pret[oa]s?|branc[oa]s?|white people|"
              r"judeus?|jews?|judios?|muculman\w*|muslims?|musulman\w*|cristaos?|christians?|gays?|lesbic\w*|lgbt\w*|"
              r"trans\b|transgender|indigena\w*|imigrantes?|immigrants?|inmigrantes?|religiao|religion|religio\w*|"
              r"nordestin\w*|deficientes?|disabled)")
_JUDGEMENT = (r"\b(criminos\w*|criminal\w*|perigos\w*|dangerous|peligros\w*|suspeit\w*|suspicious|sospech\w*|"
              r"inferior\w*|terrorist\w*|terrorista\w*|ladr\w*|thie(f|ves)|bandid\w*|inteligente|intelligent|burr[oa]s?|"
              r"stupid|estupid\w*|preguicos\w*|lazy|perezos\w*|confiavel|trustworthy|confiable)")
_MINORS = r"\b(crianca\w*|child\w*|kids?|menor(es)? de idade|minors?|\d{1,2} ?anos|\d{1,2} ?years? old|nin[oa]s?|adolescente?s?|teen\w*|pre-?teen)"
_SEXUAL = r"\b(sex\w*|nud\w*|naked|pelad[oa]s?|desnud\w*|porn\w*|erotic\w*|erotic|nsfw|explicit\w*)"

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.translate(_ZW))
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = re.sub(r"(?<=\w)[\.\-\*_](?=\w\b|\w[\.\-\*_])", "", t)        # s.u.i.c.i.d.e / k-i-l-l
    t = re.sub(r"\b(?:\w ){3,}\w\b", lambda m: m.group().replace(" ", ""), t)   # s u i c i d e
    t = re.sub(r"(?<=[a-z])[013457@$!](?=[a-z])|(?<=\b)[013457@$](?=[a-z]{2})", lambda m: m.group().translate(_LEET), t)
    return re.sub(r"(.)\1{2,}", r"\1\1", t)                           # kiiiill -> kiill


def _compile(d):
    return {k: re.compile("|".join(v)) for k, v in d.items()}


_S, _W = _compile(_STRONG), _compile(_WEAK)
_PROT, _JUDG = re.compile(_PROTECTED), re.compile(_JUDGEMENT)
_MIN, _SEX = re.compile(_MINORS), re.compile(_SEXUAL)


class SafetyGuard:
    def __init__(self, policy: Optional[Dict[str, str]] = None, mode: str = "auto", engine=None,
                 specialist: str = "specialist:safety", learned_threshold: float = 0.6):
        self.policy = {**DEFAULT_POLICY, **(policy or {})}
        self.mode = mode                 # "off" | "lexical" | "auto" | "strict"
        self.engine = engine             # SemanticEngine (or MicroBatcher) with the safety specialist loaded
        self.specialist = specialist
        self.learned_threshold = learned_threshold

    def lexical(self, text: str, targets: Iterable[str] = ()) -> Dict[str, float]:
        t = normalise(text)
        out: Dict[str, float] = {}
        for k, rx in _S.items():
            if rx.search(t):
                out[k] = 1.0
        for k, rx in _W.items():
            if k not in out and rx.search(t):
                out[k] = 0.5
        if _MIN.search(t) and _SEX.search(t):
            out["sexual_minors"] = 1.0
        tg = normalise(" ".join(targets))
        if tg and _PROT.search(tg) and _JUDG.search(tg):
            out["discriminatory_profiling"] = 1.0
        return out

    def learned(self, text: str) -> Dict[str, float]:
        from .semantic import SemReq
        from ..training.domains import SAFETY_LABELS, SAFETY_DESC
        req = SemReq("CHOICE", text[:4000], options=SAFETY_LABELS, descriptions=[SAFETY_DESC[x] for x in SAFETY_LABELS],
                     ask="Which safety category fits this text?")
        d = self.engine.run([req], tenant_adapter=self.specialist)[0]["distribution"]
        return {k: round(float(p), 4) for k, p in d.items() if k != "safe" and p >= self.learned_threshold}

    def check(self, texts: List[str], targets: Iterable[str] = (), lang: str = "pt") -> dict:
        if self.mode == "off":
            return {"level": "ok", "categories": {}, "stage": "off"}
        t0 = time.perf_counter()
        joined = "\n".join(x for x in texts if x)
        cats = self.lexical(joined, targets)
        stage = "lexical"
        has_specialist = self.engine is not None and self.specialist in getattr(self.engine, "_loaded_adapters", set())
        unsure = any(v < 1.0 for v in cats.values())
        if has_specialist and (self.mode == "strict" or (self.mode == "auto" and unsure)):
            learned = self.learned(joined)
            stage = "lexical+learned"
            for k in list(cats):                     # a weak cue the model does not confirm is dropped
                if cats[k] < 1.0 and k not in learned:
                    cats.pop(k)
            for k, p in learned.items():
                cats[k] = 1.0                        # confirmed by the safety specialist
        # strong cue or learned confirmation -> act; a weak cue alone is only reported (no false alarms on
        # "kill the process" / "a faca de cozinha")
        acts = {k: self.policy.get(k, "flag") for k, v in cats.items() if v >= 1.0}
        acts = {k: a for k, a in acts.items() if a != "allow"}
        level = "block" if "block" in acts.values() else ("flag" if acts else "ok")
        out = {"level": level, "categories": {k: cats[k] for k in acts}, "actions": acts, "stage": stage,
               "weak_cues": sorted(k for k, v in cats.items() if v < 1.0), "ms": round((time.perf_counter() - t0) * 1e3, 3)}
        if "self_harm" in acts:
            out["support"] = SUPPORT["self_harm"].get(lang, SUPPORT["self_harm"]["en"])
        return out
