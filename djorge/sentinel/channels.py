"""Dedicated per-channel training records for Djorge Sentinel: domains, e-mail addresses, phone numbers and short
messages. DEFENSIVE: every record teaches the model to JUDGE a piece of content (is this domain/address/number/
message risky, and how?). Nothing here generates an attack - only the structural shapes a defender must recognise,
built from public, non-operational patterns (brand look-alikes, risky TLDs, display-name mismatches, BR number
formats). The heavy scam-message text comes from the public datasets and the existing scams_br generator.

Each builder yields DJORGE records (CHOICE / SET / SCORE / NOUL) already wrapped in the universal envelope, so the
serving path and the training path read the identical format.
"""
from __future__ import annotations

import random
from typing import Dict, Iterator, List

from . import taxonomy as T
from .envelope import render_piece, content_offset

# ---- building blocks (public, non-operational) --------------------------------------------------------------
BRANDS = ["nubank", "itau", "bradesco", "caixa", "bancodobrasil", "santander", "inter", "c6bank", "picpay",
          "mercadolivre", "mercadopago", "shopee", "magazineluiza", "amazon", "netflix", "paypal", "correios",
          "gov", "receita", "serasa", "detran", "inss", "whatsapp", "instagram", "google", "microsoft", "apple"]
GOOD_DOMAIN = {"nubank": "nubank.com.br", "itau": "itau.com.br", "correios": "correios.com.br", "gov": "gov.br",
               "paypal": "paypal.com", "netflix": "netflix.com", "amazon": "amazon.com.br", "google": "google.com",
               "microsoft": "microsoft.com", "mercadolivre": "mercadolivre.com.br"}
RISKY_TLD = ["xyz", "top", "online", "site", "click", "shop", "live", "icu", "cfd", "sbs", "rest", "fit"]
GOOD_TLD = ["com", "com.br", "org", "net", "gov.br", "edu.br"]
SHORTENERS = ["bit.ly", "tinyurl.com", "t.co", "encurtador.com.br", "cutt.ly", "rebrand.ly"]
HOMO = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "b": "6"}
WORDS = ["seguranca", "atualizacao", "verificar", "acesso", "cliente", "suporte", "central", "portal", "app",
         "oficial", "promo", "sorteio", "beneficio", "regularize", "desbloqueio", "pagamento"]
GENERIC = ["blog", "receita-bolo", "noticias", "universidade", "loja-movel", "clinica", "escritorio", "padaria",
           "turismo", "esportes", "musica", "jardim", "livraria", "fotografia"]


def _homoglyph(s: str, rng: random.Random) -> str:
    out = list(s)
    for i, c in enumerate(out):
        if c in HOMO and rng.random() < 0.4:
            out[i] = HOMO[c]
    return "".join(out)


def _typo(s: str, rng: random.Random) -> str:
    if len(s) < 4:
        return s + rng.choice("aeiou")
    i = rng.randrange(len(s) - 1)
    op = rng.random()
    if op < 0.4:                       # swap
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if op < 0.7:                       # drop
        return s[:i] + s[i + 1:]
    return s[:i] + s[i] + s[i:]        # duplicate


# ---- 1) domains -------------------------------------------------------------------------------------------
def gen_domain(rng: random.Random):
    """-> (domain, is_risky, type_key_or_None)."""
    r = rng.random()
    if r < 0.30:                                       # legit brand
        b = rng.choice(BRANDS)
        return GOOD_DOMAIN.get(b, f"{b}.{rng.choice(['com', 'com.br'])}"), False, None
    if r < 0.50:                                       # legit generic
        return f"{rng.choice(GENERIC)}{rng.randint(0, 99) if rng.random() < .3 else ''}.{rng.choice(GOOD_TLD)}", False, None
    if r < 0.68:                                       # brand in subdomain of a strange domain
        b = rng.choice(BRANDS)
        return f"{b}.{rng.choice(WORDS)}-{rng.randint(1, 99)}.{rng.choice(RISKY_TLD)}", True, "personificacao"
    if r < 0.82:                                       # look-alike (typo / homoglyph) of a brand
        b = rng.choice(BRANDS)
        fake = _homoglyph(b, rng) if rng.random() < 0.5 else _typo(b, rng)
        return f"{fake}.{rng.choice(RISKY_TLD + ['com'])}", (fake != b), "personificacao"
    if r < 0.92:                                       # brand + action word
        b = rng.choice(BRANDS)
        return f"{b}-{rng.choice(WORDS)}.{rng.choice(RISKY_TLD + GOOD_TLD)}", True, "link_malicioso"
    return rng.choice(SHORTENERS), None, None          # shortener: context-dependent -> abstain target


def domain_records(rng: random.Random, n: int, held=False) -> Iterator[dict]:
    opts, descs, _ = None, None, None
    labels = ["legítimo", "suspeito", "malicioso"]
    ldesc = ["domínio comum, sem sinais de golpe", "parece imitar uma marca ou usar truques", "claramente feito para enganar"]
    for i in range(n):
        dom, risky, typ = gen_domain(rng)
        url = ("https://" if rng.random() < 0.6 else "http://") + dom + rng.choice(["", "/login", "/acesso", "/app", "/verificar"])
        piece = {"channel": "url", "url": url, "content": url}
        text, _ = render_piece(piece, with_url_view=True)
        if risky is None:                               # shortener -> teach abstention (no confident label)
            yield _rec("CHOICE", text, labels, None, ldesc, T.ASK_FAMILY, "url", "gen_domain", band=None, weight=0.5)
            continue
        lab = "malicioso" if (risky and typ in ("personificacao",)) else ("suspeito" if risky else "legítimo")
        band = 3 if lab == "malicioso" else (2 if lab == "suspeito" else 0)
        yield _rec("CHOICE", text, labels, lab, ldesc, "Este domínio é seguro para abrir?", "url", "gen_domain", band=band)


# ---- 2) e-mail addresses ----------------------------------------------------------------------------------
def gen_email(rng: random.Random):
    b = rng.choice(BRANDS)
    r = rng.random()
    if r < 0.4:                                        # legit: display matches domain
        dom = GOOD_DOMAIN.get(b, f"{b}.com.br")
        return f"{b.capitalize()} <{rng.choice(['atendimento', 'noreply', 'contato'])}@{dom}>", False
    if r < 0.7:                                        # display brand, domain unrelated / free mail
        dom = rng.choice([f"{b}-seguranca.{rng.choice(RISKY_TLD)}", "gmail.com", f"{_typo(b, rng)}.com",
                          f"{rng.choice(WORDS)}.{rng.choice(RISKY_TLD)}"])
        return f"{b.capitalize()} <{rng.choice(['alerta', 'suporte', 'seguranca'])}@{dom}>", True
    if r < 0.85:                                       # legit generic person
        return f"{rng.choice(['Ana', 'Joao', 'Equipe', 'Maria'])} <{rng.choice(GENERIC)}@{rng.choice(['gmail.com', 'outlook.com', 'uol.com.br'])}>", False
    return f"<{rng.choice(WORDS)}@{_typo(b, rng)}.{rng.choice(RISKY_TLD)}>", True


def email_records(rng: random.Random, n: int, held=False) -> Iterator[dict]:
    labels = ["legítimo", "suspeito"]
    ld = ["remetente coerente, sem sinais de fraude", "nome e domínio não batem ou imitam uma marca"]
    for i in range(n):
        frm, risky = gen_email(rng)
        dn = frm.split("<")[0].strip()
        addr = frm.split("<")[1].rstrip(">") if "<" in frm else frm
        piece = {"channel": "email", "display_name": dn, "origin": addr, "content": "(verificação do remetente)"}
        text, _ = render_piece(piece, with_url_view=False)
        yield _rec("CHOICE", text, labels, "suspeito" if risky else "legítimo", ld,
                   "O remetente deste e-mail é confiável?", "email", "gen_email", band=2 if risky else 0)


# ---- 3) phone numbers / channel-switch --------------------------------------------------------------------
def gen_number_message(rng: random.Random):
    ddd = rng.choice([11, 21, 31, 41, 51, 61, 71, 81, 85])
    num = f"({ddd}) 9{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
    intl = f"+{rng.choice([1, 44, 234, 351, 55])} {rng.randint(100, 999)} {rng.randint(1000, 9999)}"
    r = rng.random()
    if r < 0.4:
        return num, "Oi, tudo bem? Confirmando nosso horário de amanhã às 10h.", False, True, None
    if r < 0.7:
        return intl, "Olá! Vi seu currículo e temos uma vaga. Me chama no WhatsApp neste número.", True, False, "troca_canal"
    if r < 0.85:
        return num, "Seu código de verificação chegou. Não compartilhe com ninguém.", False, True, None
    return intl, "Aqui é do suporte. Me chama neste outro número para resolver sua conta.", True, False, "falsa_central"


def number_records(rng: random.Random, n: int, held=False) -> Iterator[dict]:
    labels = ["normal", "suspeito"]
    ld = ["contato comum, sem pedido arriscado", "número estranho ou pedido para mudar de canal"]
    for i in range(n):
        number, body, intl, known, tech = gen_number_message(rng)
        piece = {"channel": "sms", "number": number, "sender_known": known, "content": body}
        text, _ = render_piece(piece, with_url_view=False)
        risky = tech is not None
        yield _rec("CHOICE", text, labels, "suspeito" if risky else "normal", ld,
                   "Este número e pedido são confiáveis?", "sms", "gen_number", band=2 if risky else 0)


# ---- record helper ---------------------------------------------------------------------------------------
def _rec(task, text, options, label, descriptions, ask, channel, prov, band=None, weight=1.0, known=None,
         techs=None, soft=None) -> dict:
    r = {"task": task, "source": text, "options": list(options), "label": label, "descriptions": list(descriptions),
         "ask": ask, "weight": weight, "split": "train",
         "meta": {"area": "security", "channel": channel, "provenance": prov, "lang": "pt"}}
    if known is not None:
        r["known"] = known
    if soft is not None:
        r["soft"] = soft
    if band is not None:
        r["meta"]["band"] = band
    return r


SOURCES = {"gen_domain": domain_records, "gen_email": email_records, "gen_number": number_records}


def build(rng: random.Random, per_source: int, held=False) -> List[dict]:
    out = []
    for name, fn in SOURCES.items():
        out.extend(list(fn(rng, per_source, held)))
    return out
