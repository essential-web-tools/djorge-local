"""Djorge Sentinel helpers — defensive signal extraction for the security classifier.

Everything here only INSPECTS content a caller already has and returns risk signals + a reputation verdict, with no
model call (microseconds). It helps the model decide faster and more accurately; it performs no attack and contacts
no network. Reputation lists are the local snapshot built by djorge.training.sentinel_data (sentinel_reputation.json).
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
from typing import Dict, List, Optional
from urllib.parse import urlsplit

RISKY_TLDS = {"zip", "mov", "xyz", "top", "online", "site", "click", "country", "kim", "work", "gq", "cf", "ml", "ga",
              "tk", "rest", "fit", "live", "shop", "icu", "cam", "sbs", "cfd", "lol", "quest"}
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly", "rebrand.ly", "encurtador.com.br",
              "rb.gy", "shorturl.at", "tiny.cc", "lnkd.in"}
# words frequently used in credential-harvesting pages (for DETECTION only)
CRED_WORDS = re.compile(r"(login|signin|sign-in|verify|secure|account|senha|password|atualizar|update|confirm|validar|"
                        r"recadastr|desbloque|unlock|wallet|seed|2fa|otp|banking|ident)", re.I)
BRANDS = ["paypal", "nubank", "itau", "bradesco", "caixa", "bancodobrasil", "santander", "mercadolivre", "mercadopago",
          "amazon", "netflix", "google", "microsoft", "apple", "whatsapp", "correios", "gov", "receita", "serasa",
          "binance", "steam", "instagram", "facebook", "outlook", "icloud", "shopee", "magazineluiza", "picpay", "inter"]
HOMO = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a", "vv": "w", "rn": "m"}
# second-level public suffixes: the registrable domain is the label BEFORE these (nubank.com.br -> nubank)
MULTI_SUFFIX = {"com.br", "gov.br", "org.br", "net.br", "edu.br", "co.uk", "org.uk", "gov.uk", "com.ar", "com.mx",
                "co.jp", "com.au", "com.co", "gob.mx", "gov.co", "edu.br", "jus.br", "leg.br", "mp.br"}


def _registrable(labels: list) -> tuple:
    """(registrable_label, subdomain_string) honouring two-level suffixes like com.br."""
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_SUFFIX:
        return labels[-3], ".".join(labels[:-3])
    if len(labels) >= 2:
        return labels[-2], ".".join(labels[:-2])
    return (labels[-1] if labels else ""), ""


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    from collections import Counter
    n = len(s)
    return round(-sum((c / n) * math.log2(c / n) for c in Counter(s).values()), 3)


def _edit1(a: str, b: str) -> int:
    """Bounded Levenshtein (returns up to 3); enough to flag look-alikes of known brands."""
    if abs(len(a) - len(b)) > 3:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
        if min(prev) > 3:
            return 3
    return min(prev[-1], 3)


def _defold(s: str) -> str:
    s = s.lower()
    for k, v in HOMO.items():
        s = s.replace(k, v)
    return re.sub(r"[^a-z]", "", s)


def analyze_url(url: str, reputation: Optional["Reputation"] = None) -> Dict:
    """Structural risk signals for one URL + optional reputation verdict. Higher score = more suspicious (0..~1)."""
    raw = (url or "").strip()
    signals, score = [], 0.0
    u = raw if re.match(r"^[a-z]+://", raw, re.I) else "http://" + raw
    try:
        parts = urlsplit(u)
    except ValueError:
        return {"url": raw, "score": 1.0, "signals": ["unparseable"], "verdict": "suspicious"}
    host = (parts.hostname or "").lower()
    path_q = (parts.path + ("?" + parts.query if parts.query else "")).lower()

    def add(sig, w):
        nonlocal score
        signals.append(sig); score += w

    if not parts.scheme.startswith("https"):
        add("no_https", 0.1)
    if "@" in parts.netloc:
        add("at_sign_in_authority", 0.35)                     # text before '@' hides the real host
    if parts.port and parts.port not in (80, 443):
        add(f"nonstandard_port:{parts.port}", 0.15)
    is_ip = False
    try:
        ipaddress.ip_address(host)
        is_ip = True; add("ip_as_host", 0.4)
    except ValueError:
        pass
    labels = host.split(".") if host else []
    tld = labels[-1] if len(labels) >= 2 else ""
    reg_raw, sub_raw = _registrable(labels)
    reg = _defold(reg_raw)
    sub = _defold(sub_raw)
    if tld in RISKY_TLDS:
        add(f"risky_tld:{tld}", 0.2)
    if host in SHORTENERS or ".".join(labels[-2:]) in SHORTENERS:
        add("url_shortener", 0.25)
    if "xn--" in host:
        add("punycode_idn", 0.3)                              # internationalised domain, can hide homoglyphs
    n_sub = len(sub_raw.split(".")) if sub_raw else 0
    if n_sub >= 2:
        add(f"many_subdomains:{n_sub}", 0.15)
    if len(raw) > 90:
        add("very_long_url", 0.1)
    if host and _entropy(host) > 3.6:
        add("high_entropy_host", 0.15)
    if CRED_WORDS.search(path_q) or CRED_WORDS.search(host):
        add("credential_keywords", 0.15)
    for brand in BRANDS:
        in_sub = brand in sub and brand != reg
        look = reg and reg != brand and 1 <= _edit1(reg, brand) <= 2 and abs(len(reg) - len(brand)) <= 2
        if in_sub:
            add(f"brand_in_subdomain:{brand}", 0.4); break     # e.g. nubank.security-check.xyz
        if look:
            add(f"lookalike_domain:{brand}~{reg}", 0.45); break  # e.g. nubamk / 1tau
    verdict = "suspicious" if score >= 0.5 else "caution" if score >= 0.25 else "clean"
    out = {"url": raw, "host": host, "is_ip_host": is_ip, "tld": tld, "score": round(min(score, 1.0), 3),
           "signals": signals, "verdict": verdict}
    if reputation is not None:
        rv = reputation.check_url(raw)
        out["reputation"] = rv
        if rv.get("listed"):
            out["verdict"] = "malicious"; out["score"] = 1.0
    return out


def analyze_email_address(addr: str, reputation: Optional["Reputation"] = None, display: str = "") -> Dict:
    """Signals for a sender address, incl. display-name/domain mismatch (a common spoofing tell)."""
    addr = (addr or "").strip().strip("<>").lower()
    m = re.match(r"^[^@\s]+@([^@\s]+\.[a-z]{2,})$", addr)
    if not m:
        return {"address": addr, "valid": False, "score": 0.6, "signals": ["malformed_address"], "verdict": "suspicious"}
    domain = m.group(1)
    signals, score = [], 0.0

    def add(sig, w):
        nonlocal score
        signals.append(sig); score += w
    reg_raw, _ = _registrable(domain.split("."))
    reg = _defold(reg_raw)
    for brand in BRANDS:
        if reg and reg != brand and 1 <= _edit1(reg, brand) <= 2:
            add(f"lookalike_sender:{brand}~{reg}", 0.45); break
    if domain.split(".")[-1] in RISKY_TLDS:
        add("risky_tld", 0.2)
    if display:
        d = _defold(display)
        if any(b in d for b in BRANDS) and not any(b in reg for b in BRANDS if b in d):
            add("display_name_brand_mismatch", 0.4)           # "Nubank <x@gmail.com>"
    out = {"address": addr, "domain": domain, "valid": True, "score": round(min(score, 1.0), 3), "signals": signals,
           "verdict": "suspicious" if score >= 0.4 else "caution" if score >= 0.2 else "clean"}
    if reputation is not None:
        rv = reputation.check_domain(domain)
        out["reputation"] = rv
        if rv.get("listed"):
            out["verdict"] = rv.get("list", "listed")
    return out


def analyze_ip(ip: str, reputation: Optional["Reputation"] = None) -> Dict:
    try:
        obj = ipaddress.ip_address(ip.strip())
    except ValueError:
        return {"ip": ip, "valid": False}
    out = {"ip": str(obj), "version": obj.version, "private": obj.is_private, "valid": True}
    if reputation is not None:
        out["reputation"] = reputation.check_ip(str(obj))
    return out


class Reputation:
    """Exact/suffix/CIDR lookups against the local snapshot (sentinel_reputation.json). Loaded once."""

    def __init__(self, path: Optional[str] = None):
        self.url, self.domain, self.allow, self.disposable, self.tor = set(), set(), set(), set(), set()
        self.nets4: List = []
        self.nets6: List = []
        self.meta = {}
        p = path or os.environ.get("DJORGE_REPUTATION_FILE")
        if p and os.path.exists(p):
            self.load(p)

    def load(self, path: str):
        d = json.load(open(path))
        self.url = set(d.get("url", []))
        self.domain = set(d.get("domain", []))
        self.allow = set(d.get("allow", []))
        self.disposable = set(d.get("disposable", []))
        self.tor = set(d.get("tor", []))
        self.meta = d.get("_meta", {})
        for c in d.get("net", []):
            try:
                net = ipaddress.ip_network(c, strict=False)
                (self.nets6 if net.version == 6 else self.nets4).append(net)
            except ValueError:
                pass
        self.nets4.sort(key=lambda n: int(n.network_address)); self.nets6.sort(key=lambda n: int(n.network_address))

    @staticmethod
    def _host(url: str) -> str:
        try:
            return (urlsplit(url if "://" in url else "http://" + url).hostname or "").lower()
        except ValueError:
            return ""

    def _domain_listed(self, host: str) -> bool:
        parts = host.split(".")
        return any(".".join(parts[i:]) in self.domain for i in range(len(parts) - 1))

    def check_url(self, url: str) -> Dict:
        u = url.strip().rstrip("/")
        host = self._host(url)
        listed = u in self.url or (url.strip() in self.url) or self._domain_listed(host)
        return {"listed": listed, "allow": self._domain_listed(host) and host in self.allow,
                "disposable": self._domain_listed(host) and any(host.endswith(d) for d in self.disposable)}

    def check_domain(self, domain: str) -> Dict:
        domain = domain.lower().strip(".")
        return {"listed": self._domain_listed(domain), "list": "phishing_domain" if self._domain_listed(domain) else None,
                "disposable": domain in self.disposable, "allowlisted": domain in self.allow}

    def check_ip(self, ip: str) -> Dict:
        try:
            obj = ipaddress.ip_address(ip)
        except ValueError:
            return {"listed": False}
        nets = self.nets6 if obj.version == 6 else self.nets4
        hit = any(obj in n for n in nets)                     # linear; lists are small after dedup
        return {"listed": hit, "tor_exit": ip in self.tor}
