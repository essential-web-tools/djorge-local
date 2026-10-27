"""Universal envelope: one standard input for every channel (SMS, e-mail, voice, site, chat, document, code, post,
image text, URL) -> validated pieces -> the text the model reads.

Only `content` is required. Everything else is optional context the model learned to use when present and to do
without when missing (training renders the same envelopes with random field dropout). The SAME render() is used in
training and serving, so the model always reads one format.

Untrusted content is "spotlighted": it sits between fixed delimiters that are stripped from the content itself, so
text inside a message cannot fake the envelope structure (e.g. a fake "[auth] spf=pass" line).
"""
from __future__ import annotations

import base64
import binascii
import html
import random
import re
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from .taxonomy import CHANNELS

LIMITS = {"pieces": 32, "content_chars": 200_000, "field_chars": 2_000, "links": 64, "attachments": 16, "turns": 400}
OPEN, CLOSE = "⟦", "⟧"
ZERO_WIDTH = re.compile("[​‌‍‎‏⁠⁡⁢⁣⁤﻿­]")
BIDI = re.compile("[‪-‮⁦-⁩]")
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Cyrillic / Greek letters that look Latin (detection + folding for the model's "clean" view)
CONFUSABLE = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
                            "ԁ": "d", "ѕ": "s", "ɡ": "g", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
                            "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "ο": "o", "α": "a", "ε": "e", "ι": "i",
                            "κ": "k", "ν": "v", "ρ": "p", "τ": "t", "υ": "u"})
URL_RE = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>\"'⟦⟧]{3,2000}|[a-z0-9][a-z0-9\-]{0,62}(?:\.[a-z0-9\-]{1,63})*\."
                    r"(?:com|net|org|br|xyz|top|online|site|click|info|io|app|live|shop|ru|cn|me|ly|gl|co|us|biz)"
                    r"(?:\.[a-z]{2})?(?:/[^\s<>\"'⟦⟧]*)?)")


def normalize(text: str) -> Tuple[str, dict]:
    """-> (clean text, flags). Removes invisible/bidi/control characters and folds look-alike letters; the COUNTS
    become signals of their own (hidden characters are a classic filter-evasion trick)."""
    s = str(text or "")
    flags = {"ocultos": len(ZERO_WIDTH.findall(s)) + len(BIDI.findall(s)), "controle": len(CTRL.findall(s))}
    s = ZERO_WIDTH.sub("", s)
    s = BIDI.sub("", s)
    s = CTRL.sub(" ", s)
    s = unicodedata.normalize("NFKC", s)
    folded = s.translate(CONFUSABLE)
    flags["confundiveis"] = sum(1 for a, b in zip(s, folded) if a != b)
    s = folded.replace(OPEN, "[").replace(CLOSE, "]")
    return s, {k: v for k, v in flags.items() if v}


def url_view(url: str) -> str:
    """Segmented view of a URL (presentation only - the model judges): esquema | sub | domínio | sufixo | caminho."""
    from ..runtime.sentinel import _registrable, MULTI_SUFFIX
    raw = (url or "").strip()
    u = raw if re.match(r"^[a-z]+://", raw, re.I) else "http://" + raw
    try:
        p = urlsplit(u)
    except ValueError:
        return raw[:300]
    host = (p.hostname or "").lower()
    labels = host.split(".") if host else []
    reg, sub = _registrable(labels)
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_SUFFIX:
        suffix = ".".join(labels[-2:])
    else:
        suffix = labels[-1] if len(labels) >= 2 else ""
    path = (p.path or "")[:120] + (("?" + p.query[:80]) if p.query else "")
    port = f" | porta {p.port}" if p.port else ""
    at = " | tem @ antes do domínio" if "@" in p.netloc else ""
    return f"{p.scheme or '?'} | sub: {sub or '-'} | domínio: {reg or host} | sufixo: {suffix or '-'}{port}{at} | caminho: {path or '/'}"


def find_urls(text: str, limit: int = 64) -> List[str]:
    out = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(1).rstrip(".,;:!?)]}'\"")
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def decode_layers(text: str, max_depth: int = 3, max_items: int = 8) -> List[Tuple[str, str]]:
    """Reveal content hidden by common encodings (Base64, %-encoding, HTML entities, \\x escapes) - bounded depth and
    size. Returned as extra pieces for the severe mode; the presence of encoded blocks is itself a signal."""
    found: List[Tuple[str, str]] = []
    frontier = [text or ""]
    for _ in range(max_depth):
        nxt = []
        for t in frontier:
            for m in re.finditer(r"[A-Za-z0-9+/]{24,}={0,2}", t):
                blob = m.group(0)
                try:
                    dec = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False).decode("utf-8", "strict")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    try:
                        dec = base64.b64decode(blob + "=" * (-len(blob) % 4)).decode("utf-16-le", "strict")
                    except Exception:
                        continue
                if dec and sum(c.isprintable() or c in "\n\t" for c in dec) / len(dec) > 0.9 and len(dec) >= 8:
                    found.append(("base64", dec[:20000])); nxt.append(dec)
            if re.search(r"%[0-9A-Fa-f]{2}.*%[0-9A-Fa-f]{2}", t):
                dec = unquote(t)
                if dec != t:
                    found.append(("url_encoding", dec[:20000])); nxt.append(dec)
            if re.search(r"&(#\d+|#x[0-9a-fA-F]+|[a-z]+);", t):
                dec = html.unescape(t)
                if dec != t:
                    found.append(("html_entities", dec[:20000])); nxt.append(dec)
            if re.search(r"(\\x[0-9a-fA-F]{2}){4,}", t):
                try:
                    dec = re.sub(r"(?:\\x[0-9a-fA-F]{2})+", lambda m: bytes.fromhex(m.group(0).replace("\\x", "")).decode("utf-8", "replace"), t)
                    found.append(("hex_escape", dec[:20000])); nxt.append(dec)
                except ValueError:
                    pass
            if len(found) >= max_items:
                return found[:max_items]
        if not nxt:
            break
        frontier = nxt
    return found[:max_items]


# --------------------------------------------------------------------------------------------- validation --
def _s(v, n=LIMITS["field_chars"]):
    if v is None:
        return None
    if not isinstance(v, (str, int, float, bool)):
        raise ValueError("envelope fields must be strings")
    return str(v)[:n]


def validate(env: dict) -> dict:
    """Accepts {content|text, channel?, origin?, subject?, links?, attachments?, auth?, turns?, page?, context?,
    items?} and returns a clean case {pieces: [...]} (piece 0 = main). Raises ValueError with a clear message."""
    if not isinstance(env, dict):
        raise ValueError("envelope must be an object")
    pieces = []

    def piece(d, default_channel="desconhecido"):
        if isinstance(d, str):
            d = {"content": d}
        if not isinstance(d, dict):
            raise ValueError("each item must be an object or a string")
        content = d.get("content", d.get("text"))
        turns = d.get("turns")
        if (content is None or (isinstance(content, str) and not content.strip())) and not turns and not d.get("url"):
            raise ValueError("'content' (or 'turns' or 'url') is required")
        if content is not None and not isinstance(content, str):
            raise ValueError("'content' must be a string")
        if content and len(content) > LIMITS["content_chars"]:
            raise ValueError(f"'content' longer than {LIMITS['content_chars']} characters")
        ch = d.get("channel") or default_channel
        if ch not in CHANNELS:
            raise ValueError(f"'channel' must be one of {CHANNELS}")
        p = {"channel": ch, "content": content or ""}
        for k in ("origin", "display_name", "subject", "reply_to", "number", "url", "title", "platform", "language",
                  "country", "language_hint", "sender_known", "forwarded", "verified_business", "code_language"):
            if d.get(k) is not None:
                p[k] = _s(d[k])
        if isinstance(d.get("auth"), dict):
            p["auth"] = {k: _s(v, 20) for k, v in d["auth"].items() if k in ("spf", "dkim", "dmarc")}
        links = d.get("links") or []
        if not isinstance(links, list) or len(links) > LIMITS["links"]:
            raise ValueError(f"'links' must be a list of at most {LIMITS['links']}")
        p["links"] = []
        for l in links:
            if isinstance(l, str):
                l = {"href": l}
            if isinstance(l, dict) and l.get("href"):
                p["links"].append({"href": _s(l["href"]), "text": _s(l.get("text"))})
        atts = d.get("attachments") or []
        if not isinstance(atts, list) or len(atts) > LIMITS["attachments"]:
            raise ValueError(f"'attachments' must be a list of at most {LIMITS['attachments']}")
        p["attachments"] = [{"name": _s(a.get("name"), 200), "type": _s(a.get("type"), 80), "size": _s(a.get("size"), 20),
                             "text": (a.get("text") or "")[:LIMITS["content_chars"]] if isinstance(a.get("text"), str) else ""}
                            for a in atts if isinstance(a, dict)]
        if turns:
            if not isinstance(turns, list) or len(turns) > LIMITS["turns"]:
                raise ValueError(f"'turns' must be a list of at most {LIMITS['turns']}")
            p["turns"] = [{"speaker": _s(t.get("speaker") or t.get("role") or "?", 40), "text": str(t.get("text", ""))[:20000]}
                          for t in turns if isinstance(t, dict)]
            if p["channel"] == "desconhecido":
                p["channel"] = "conversa"
        if isinstance(d.get("forms"), list):
            p["forms"] = [{"fields": [str(x)[:40] for x in (f.get("fields") or [])][:30], "action": _s(f.get("action"), 300)}
                          for f in d["forms"][:10] if isinstance(f, dict)]
        return p

    pieces.append(piece(env, env.get("channel") or "desconhecido"))
    items = env.get("items") or []
    if not isinstance(items, list) or len(items) + 1 > LIMITS["pieces"]:
        raise ValueError(f"'items' must be a list (at most {LIMITS['pieces'] - 1})")
    for it in items:
        pieces.append(piece(it))
    return {"pieces": pieces, "context": {k: _s(v) for k, v in (env.get("context") or {}).items()} if isinstance(env.get("context"), dict) else {}}


# ------------------------------------------------------------------------------------------------- render --
def _q(s: str) -> str:
    return (s or "").replace(OPEN, "[").replace(CLOSE, "]")


def render_piece(p: dict, rng: Optional[random.Random] = None, drop: float = 0.0, content_override: Optional[str] = None,
                 with_url_view: bool = True) -> Tuple[str, dict]:
    """-> (text for the model, flags). rng+drop = training-time field dropout."""
    def keep():
        return rng is None or rng.random() >= drop
    lines, flags = [], {}
    ch = p.get("channel", "desconhecido")
    if keep() or ch == "desconhecido":
        lines.append(f"[canal] {ch}")
    for k, tag in (("display_name", "nome exibido"), ("origin", "de"), ("number", "número"), ("reply_to", "responder-para"),
                   ("subject", "assunto"), ("platform", "plataforma"), ("title", "título")):
        if p.get(k) and keep():
            v, f = normalize(p[k]); flags.update({f"{k}_{a}": b for a, b in f.items()})
            lines.append(f"[{tag}] {_q(v)}")
    if p.get("url") and keep():
        lines.append(f"[url] {_q(p['url'])}")
        if with_url_view:
            lines.append(f"[url partes] {url_view(p['url'])}")
    for k, tag in (("sender_known", "contato salvo"), ("forwarded", "encaminhada"), ("verified_business", "conta verificada")):
        if p.get(k) is not None and keep():
            lines.append(f"[{tag}] {p[k]}")
    if p.get("auth") and keep():
        lines.append("[autenticação] " + " ".join(f"{k}={v}" for k, v in p["auth"].items()))
    for l in p.get("links", [])[:24]:
        if not keep():
            continue
        t = f' texto="{_q(normalize(l["text"])[0][:120])}"' if l.get("text") else ""
        lines.append(f"[link]{t} destino={_q(l['href'][:300])}" + (f" ({url_view(l['href'])})" if with_url_view else ""))
    for f in p.get("forms", []):
        if keep():
            lines.append(f"[formulário] campos: {', '.join(f['fields'])} → envia para: {_q(f.get('action') or '?')}")
    for a in p.get("attachments", []):
        if keep():
            lines.append(f"[anexo] {_q(a.get('name') or '?')} | {a.get('type') or '?'} | {a.get('size') or '?'}")
    body = content_override if content_override is not None else p.get("content", "")
    if p.get("turns") and content_override is None:
        body = "\n".join(f"[{_q(t['speaker'])}] {t['text']}" for t in p["turns"])
    clean, f = normalize(body)
    flags.update(f)
    lines.append(f"{OPEN}conteúdo{CLOSE}\n{clean}\n{OPEN}fim{CLOSE}")
    for a in p.get("attachments", []):
        if a.get("text") and keep():
            ta, fa = normalize(a["text"])
            flags.update({f"anexo_{k}": v for k, v in fa.items()})
            lines.append(f"{OPEN}anexo {_q(a.get('name') or '')}{CLOSE}\n{ta}\n{OPEN}fim{CLOSE}")
    return "\n".join(lines), flags


def content_offset(rendered: str) -> int:
    i = rendered.find(f"{OPEN}conteúdo{CLOSE}\n")
    return i + len(f"{OPEN}conteúdo{CLOSE}\n") if i >= 0 else 0


def render_case(case: dict, rng=None, drop: float = 0.0) -> Tuple[str, dict]:
    """All pieces together (the model sees cross-piece inconsistencies)."""
    parts, flags = [], {}
    for i, p in enumerate(case["pieces"]):
        t, f = render_piece(p, rng, drop)
        parts.append(t if i == 0 else f"[peça {i + 1}]\n{t}")
        flags.update(f)
    return "\n\n".join(parts), flags


def cross_signals(p: dict) -> Dict[str, list]:
    """Exact (non-judgemental) mismatches the response reports as reasons; the model sees the raw fields too."""
    from ..runtime.sentinel import _registrable, BRANDS, _defold
    out: Dict[str, list] = {}
    for l in p.get("links", []):
        t, h = (l.get("text") or ""), l["href"]
        tu = find_urls(t)
        if tu:
            try:
                th = (urlsplit(tu[0] if "://" in tu[0] else "http://" + tu[0]).hostname or "").lower()
                hh = (urlsplit(h if "://" in h else "http://" + h).hostname or "").lower()
                if th and hh and _registrable(th.split("."))[0] != _registrable(hh.split("."))[0]:
                    out.setdefault("link_divergente", []).append(h)
            except ValueError:
                pass
    dn, org = (p.get("display_name") or ""), (p.get("origin") or "")
    m = re.search(r"@([A-Za-z0-9.\-]+)", org)
    if dn and m:
        reg = _defold(_registrable(m.group(1).lower().split("."))[0])
        brands = [b for b in BRANDS if b in _defold(dn)]
        if brands and not any(b in reg for b in brands):
            out["remetente_divergente"] = [org]
    a = p.get("auth") or {}
    if any(str(v).lower() in ("fail", "softfail", "none", "permerror") for v in a.values()):
        out["auth_falhou"] = [f"{k}={v}" for k, v in a.items()]
    for f in p.get("forms", []):
        if any(re.search(r"senha|password|cart|card|cvv|token|c[oó]digo|pin", x, re.I) for x in f.get("fields", [])):
            out.setdefault("pagina_formulario", []).append(f.get("action") or "?")
    return out
