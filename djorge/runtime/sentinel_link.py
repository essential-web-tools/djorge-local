"""Origo -> Sentinel hand-off (Origo is a cataloguing/judgement/decision model; Sentinel is the security specialist).

Configuration comes ONLY from the environment; nothing is invented:
  SENTINEL_URL       base URL of a deployed Sentinel (e.g. https://sentinel.example.com) - optional
  SENTINEL_API_KEY   key for that Sentinel - optional (needed only for active checks)
  SENTINEL_TIMEOUT_S request timeout (default 3)

Flow
  1. Origo's safety layer (guard.py) flags risk categories in the analysed text or the question.
  2. recommend() turns that into a structured recommendation: which categories, why, and the configured URL.
  3. check() (only when the caller asks and both variables are set) calls POST {SENTINEL_URL}/v1/sentinel.
     If Sentinel cannot be reached the result is marked fail_closed=True -> the caller must review, never proceed
     silently with a risky operation.
The text is sent to Sentinel only on an explicit active check; logs keep categories, never the text.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Dict, Optional

# guard categories that Sentinel specialises in -> Sentinel kind to ask
ROUTE = {"manipulation": "prompt", "cyber_abuse": "texto"}
REASON = {"manipulation": "possível manipulação ou injeção de instruções",
          "cyber_abuse": "possível golpe, fraude ou abuso digital",
          "hate_harassment": "possível ofensa ou assédio", "violence_weapons": "menção a violência",
          "self_harm": "possível risco à pessoa", "illegal_drugs": "menção a drogas ilícitas"}


def configured_url() -> Optional[str]:
    u = (os.environ.get("SENTINEL_URL") or "").strip().rstrip("/")
    return u if u.startswith("https://") or u.startswith("http://127.0.0.1") or u.startswith("http://localhost") else None


def recommend(safety: Optional[dict], second_opinion: Optional[dict] = None) -> Optional[dict]:
    cats = list((safety or {}).get("categories", {}) or {})
    if second_opinion and second_opinion.get("category") and second_opinion["category"] not in cats:
        cats.append(second_opinion["category"])
    if not cats:
        return None
    kinds = sorted({ROUTE[c] for c in cats if c in ROUTE})
    return {"recommended": True, "categories": cats, "reasons": [REASON.get(c, c) for c in cats],
            "sentinel_kinds": kinds or ["texto"], "url": configured_url(),
            "message": "Este conteúdo tem indícios de risco. Recomenda-se uma análise especializada no Sentinel."
                       + ("" if configured_url() else " (URL do Sentinel não configurada: defina SENTINEL_URL)")}


def check(text: str, kind: str = "texto", profile: str = "equilibrado") -> Dict:
    url, key = configured_url(), os.environ.get("SENTINEL_API_KEY") or ""
    if not url or not key:
        return {"checked": False, "reason": "SENTINEL_URL/SENTINEL_API_KEY não configurados"}
    try:
        timeout = float(os.environ.get("SENTINEL_TIMEOUT_S", "3"))
    except ValueError:
        timeout = 3.0
    req = urllib.request.Request(f"{url}/v1/sentinel", json.dumps({"text": text[:20000], "kind": kind,
                                                                     "profile": profile}).encode(),
                                 {"content-type": "application/json", "authorization": f"Bearer {key}"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        keep = ("action", "answer", "probability", "danger", "abstain", "kind")
        return {"checked": True, **{k: r.get(k) for k in keep}}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return {"checked": False, "fail_closed": True, "error": type(e).__name__,
                "message": "Sentinel indisponível: revisar antes de prosseguir com operações de risco"}
