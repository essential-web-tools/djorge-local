"""Djorge Sentinel - security data catalog (45 open sources) -> DJORGE training records + local reputation lists.

DEFENSIVE use only: every source is used to TEACH DETECTION (is this message/URL/e-mail a threat?) and to build local
blocklists for exact reputation lookups. Nothing here generates attacks.

Kinds
  hf    Hugging Face datasets (parquet)            -> CHOICE / NOUL training records
  feed  open threat-intelligence feeds (HTTP)      -> URL training records and/or reputation lists (IPs, domains, URLs)
  gen   built-in generators (Brazil scams, hard negatives, spoofed senders)  -> training records

Feeds that need a FREE key read it from the environment: ABUSECH_AUTH_KEY (URLhaus / ThreatFox / Feodo / SSLBL via the
abuse.ch Authentication Portal). Missing keys or unreachable sources are skipped with a warning - never fatal.
Respect each provider's terms: commercial=False sources are excluded unless --allow-nc; Spamhaus asks for attribution
and at most one download per hour; abuse.ch fair-use; OpenPhish community feed is free for non-commercial use.

    python -m djorge.training.sentinel_data --out-dir DATA [--per-source 3000] [--test 150] [--allow-nc]
writes  sentinel_train.jsonl  sentinel_test.jsonl  sentinel_reputation.json  sentinel.manifest.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import ipaddress
import json
import os
import random
import re
import time
import zipfile
from collections import Counter, defaultdict
from typing import Callable, Dict, Iterable, List, Optional

# ----------------------------------------------------------------------------------------------- label space --
LABELS = {
    "safe": {"en": "safe", "pt": "seguro", "es": "seguro"},
    "spam": {"en": "spam", "pt": "spam", "es": "spam"},
    "phishing": {"en": "phishing", "pt": "phishing", "es": "phishing"},
    "scam": {"en": "scam", "pt": "golpe", "es": "estafa"},
    "malware": {"en": "malware", "pt": "malware", "es": "malware"},
    "prompt_injection": {"en": "prompt injection", "pt": "injeção de prompt", "es": "inyección de prompt"},
}
DESC = {
    "safe": {"en": "legitimate, no threat", "pt": "legítima, sem ameaça", "es": "legítimo, sin amenaza"},
    "spam": {"en": "unsolicited bulk advertising", "pt": "propaganda em massa não solicitada", "es": "publicidad masiva no solicitada"},
    "phishing": {"en": "steals passwords, codes or card data by impersonation", "pt": "rouba senhas, códigos ou dados de cartão se passando por alguém",
                 "es": "roba contraseñas, códigos o tarjetas suplantando a alguien"},
    "scam": {"en": "tricks the person into paying or transferring money", "pt": "engana a pessoa para pagar ou transferir dinheiro",
             "es": "engaña a la persona para pagar o transferir dinero"},
    "malware": {"en": "delivers malicious software or files", "pt": "entrega programa ou arquivo malicioso", "es": "entrega software o archivos maliciosos"},
    "prompt_injection": {"en": "tries to override an AI's instructions", "pt": "tenta mudar as instruções de uma IA", "es": "intenta cambiar las instrucciones de una IA"},
}
ASK = {"en": ["Is this message safe or a threat?", "What kind of threat is this, if any?", "Classify the security risk of this content."],
       "pt": ["Esta mensagem é segura ou uma ameaça?", "Que tipo de ameaça é esta, se houver?", "Classifique o risco de segurança deste conteúdo."],
       "es": ["¿Este mensaje es seguro o una amenaza?", "¿Qué tipo de amenaza es esta, si la hay?", "Clasifica el riesgo de seguridad de este contenido."]}
URL_LABELS = {"legit": {"en": "legit", "pt": "legítimo", "es": "legítimo"}, "phishing": LABELS["phishing"],
              "malware": LABELS["malware"], "defacement": {"en": "defacement", "pt": "site invadido", "es": "sitio vandalizado"}}
URL_ASK = {"en": "Is this link safe to open?", "pt": "Este link é seguro para abrir?", "es": "¿Es seguro abrir este enlace?"}
NOUL_PROP = {
    "phishing": {"en": "This content is a phishing attempt.", "pt": "Este conteúdo é uma tentativa de phishing.", "es": "Este contenido es un intento de phishing."},
    "spam": {"en": "This message is spam.", "pt": "Esta mensagem é spam.", "es": "Este mensaje es spam."},
    "scam": {"en": "This message is a scam.", "pt": "Esta mensagem é um golpe.", "es": "Este mensaje es una estafa."},
    "prompt_injection": {"en": "This text tries to manipulate an AI system.", "pt": "Este texto tenta manipular um sistema de IA.",
                         "es": "Este texto intenta manipular un sistema de IA."},
}
CROSS = {"message": ["prompt_injection"], "injection": ["spam", "phishing"]}     # disjoint distractors per domain


def _clip(s: str, n: int = 1200) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n]


def message_record(text, threat, lang, prov, lic, domain="message", rng=None, noul_share=0.3):
    """threat in LABELS keys. Binary sources give options [safe, threat] + disjoint distractors only."""
    rng = rng or random
    text = _clip(text)
    if len(text) < 8:
        return None
    lang = lang if lang in ("en", "pt", "es") else "en"
    meta = {"area": "security", "lang": lang, "provenance": prov, "license": lic, "threat": threat}
    pos = threat if threat != "safe" else None
    if pos in NOUL_PROP and rng.random() < noul_share:
        return {"task": "NOUL", "source": text, "options": [NOUL_PROP[pos][lang]], "label": True, "meta": meta}
    keys = ["safe"] + ([threat] if threat != "safe" else [rng.choice(["spam", "phishing", "scam"]) if domain == "message"
                                                          else "prompt_injection"])
    keys += [k for k in CROSS.get(domain, []) if k not in keys and rng.random() < 0.5]
    rng.shuffle(keys)
    ll = rng.choice([lang, lang, "en"])                     # labels mostly in the text's language, sometimes English
    opts = [LABELS[k][ll] for k in keys]
    return {"task": "CHOICE", "source": text, "options": opts, "label": LABELS[threat][ll],
            "descriptions": [DESC[k][ll] for k in keys], "ask": rng.choice(ASK[ll]), "meta": meta}


def url_record(url, kind, prov, lic, rng=None, options=("legit", "phishing")):
    rng = rng or random
    url = str(url or "").strip()
    if not url or len(url) > 400:
        return None
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "http://" + url if kind != "legit" and rng.random() < 0.5 else "https://" + url
    ll = rng.choice(["en", "pt", "es"])
    keys = list(dict.fromkeys(list(options) + [kind]))
    rng.shuffle(keys)
    return {"task": "CHOICE", "source": url, "options": [URL_LABELS[k][ll] for k in keys], "label": URL_LABELS[kind][ll],
            "ask": URL_ASK[ll], "descriptions": [""] * len(keys),
            "meta": {"area": "security", "lang": "url", "provenance": prov, "license": lic, "threat": kind}}

# ------------------------------------------------------------------------------------------------- HF sources --


def _lbl(map_):
    return lambda v: map_.get(str(v).strip().lower())


HF = [  # id, hf, config, splits, text fn, threat fn, lang, license, commercial, domain
    ("hf_phishing_email_kaggle", "zefang-liu/phishing-email-dataset", None, ["train"], lambda r: r["Email Text"],
     lambda r: {"phishing email": "phishing", "safe email": "safe"}.get(str(r["Email Type"]).lower()), "en", "LGPL-3.0", True, "message"),
    ("hf_phishing_emailv2_email", "cybersectony/PhishingEmailDetectionv2.0", None, ["train", "test"], lambda r: r["content"],
     lambda r: {0: "safe", 1: "phishing"}.get(r["label"]), "en", "unstated", False, "message"),
    ("hf_seven_phishing_spam", "puyang2025/seven-phishing-email-datasets", None, ["train", "test"],
     lambda r: f"{r.get('subject') or ''}\n{r['text']}", lambda r: {0: "safe", 1: "phishing"}.get(r["label"]), "en", "other", True, "message"),
    ("hf_phishing_benign_curated", "darkknight25/phishing_benign_email_dataset", None, ["train"],
     lambda r: f"{r['subject']}\n{r['body']}", lambda r: "phishing" if "phish" in str(r["label"]).lower() else "safe", "en", "MIT", True, "message"),
    ("hf_spear_phishing_es", "Darito/spanish_spear_phishing", None, ["train"], lambda r: r["translated_email_body"],
     lambda r: "phishing", "es", "unknown", False, "message"),
    ("hf_sms_spam_uci", "ucirvine/sms_spam", "plain_text", ["train"], lambda r: r["sms"],
     lambda r: {0: "safe", 1: "spam"}.get(r["label"]), "en", "unknown(research)", False, "message"),
    ("hf_enron_spam", "SetFit/enron_spam", None, ["train", "test"], lambda r: r["text"],
     lambda r: {0: "safe", 1: "spam"}.get(r["label"]), "en", "public research corpus", True, "message"),
    ("hf_spam_detection", "Deysi/spam-detection-dataset", None, ["train", "test"], lambda r: r["text"],
     lambda r: _lbl({"spam": "spam", "not_spam": "safe"})(r["label"]), "en", "Apache-2.0", True, "message"),
    ("hf_sms_spam_pt", "dbarbedillo/SMS_Spam_Multilingual_Collection_Dataset", None, ["train"], lambda r: r["text_pt"],
     lambda r: _lbl({"spam": "spam", "ham": "safe"})(r["labels"]), "pt", "GPL", True, "message"),
    ("hf_sms_spam_es", "dbarbedillo/SMS_Spam_Multilingual_Collection_Dataset", None, ["train"], lambda r: r["text_es"],
     lambda r: _lbl({"spam": "spam", "ham": "safe"})(r["labels"]), "es", "GPL", True, "message"),
    ("hf_all_scam_spam_multi", "FredZhang7/all-scam-spam", None, ["train"], lambda r: r["text"],
     lambda r: {0: "safe", 1: "scam"}.get(r["is_spam"]), "multi", "Apache-2.0", True, "message"),
    ("hf_spamassassin", "talby/spamassassin", "text", ["train"], lambda r: r["text"],
     lambda r: {0: "spam", 1: "safe"}.get(r["label"]), "en", "public corpus", True, "message"),
    ("hf_spam_messages_telegram", "mshenoda/spam-messages", None, ["train", "test"], lambda r: r["text"],
     lambda r: _lbl({"spam": "spam", "ham": "safe"})(r["label"]), "en", "MIT", True, "message"),
    ("hf_sms_otp_spam", "alusci/sms-otp-spam-dataset", None, ["train"], lambda r: r["sms_text"],
     lambda r: "safe" if str(r["label"]).strip().lower() == "valid" else "phishing", "en", "MIT", True, "message"),
    ("hf_scam_dialogue", "BothBosu/scam-dialogue", None, ["train", "test"], lambda r: r["dialogue"],
     lambda r: {0: "safe", 1: "scam"}.get(r["label"]), "en", "Apache-2.0", True, "message"),
    ("hf_scam_conversation_agents", "BothBosu/multi-agent-scam-conversation", None, ["train", "test"], lambda r: r["dialogue"],
     lambda r: {0: "safe", 1: "scam"}.get(r["labels"]), "en", "Apache-2.0", True, "message"),
    ("hf_prompt_injections_deepset", "deepset/prompt-injections", None, ["train", "test"], lambda r: r["text"],
     lambda r: {0: "safe", 1: "prompt_injection"}.get(r["label"]), "multi", "Apache-2.0", True, "injection"),
    ("hf_safeguard_injection", "xTRam1/safe-guard-prompt-injection", None, ["train", "test"], lambda r: r["text"],
     lambda r: {0: "safe", 1: "prompt_injection"}.get(r["label"]), "en", "unstated", True, "injection"),
    ("hf_neuralchemy_injection", "neuralchemy/Prompt-injection-dataset", "full", ["train", "test"], lambda r: r["text"],
     lambda r: {0: "safe", 1: "prompt_injection"}.get(r["label"]), "en", "Apache-2.0", True, "injection"),
    ("hf_spml_injection", "reshabhs/SPML_Chatbot_Prompt_Injection", None, ["train"], lambda r: r["User Prompt"],
     lambda r: {0: "safe", 1: "prompt_injection"}.get(r["Prompt injection"]), "en", "MIT", True, "injection"),
    ("hf_jailbreak_classification", "jackhhao/jailbreak-classification", None, ["train", "test"], lambda r: r["prompt"],
     lambda r: _lbl({"jailbreak": "prompt_injection", "benign": "safe"})(r["type"]), "en", "Apache-2.0", True, "injection"),
    ("hf_injection_safety_60k", "jayavibhav/prompt-injection-safety", None, ["train", "test"], lambda r: r["text"],
     lambda r: {0: "safe", 1: "prompt_injection"}.get(r["label"]), "en", "unstated", True, "injection"),
    ("hf_injections_multilingual", "yanismiraoui/prompt_injections", None, ["train"], lambda r: r["prompt_injections"],
     lambda r: "prompt_injection", "multi", "Apache-2.0", True, "injection"),
]
HF_URL = [  # id, hf, config, splits, url fn, kind fn, options, license, commercial
    ("hf_phishing_urls_886k", "Mitake/PhishingURLsANDBenignURLs", None, ["train"], lambda r: r["url"],
     lambda r: {0: "legit", 1: "phishing"}.get(r["label"]), ("legit", "phishing"), "CC-BY-SA-4.0", True),
    ("hf_malicious_url_651k", "surajshelke/malicious_url", None, ["train", "test"], lambda r: r["url"],
     lambda r: {"benign": "legit", "phishing": "phishing", "malware": "malware", "defacement": "defacement"}.get(str(r["type"]).lower()),
     ("legit", "phishing", "malware", "defacement"), "unstated", True),
    ("hf_phishing_site_urls", "shawhin/phishing-site-classification", None, ["train", "test"], lambda r: r["text"],
     lambda r: {0: "legit", 1: "phishing"}.get(r["labels"]), ("legit", "phishing"), "ODbL", True),
    ("hf_phishing_emailv2_urls", "cybersectony/PhishingEmailDetectionv2.0", None, ["train"], lambda r: r["content"],
     lambda r: {2: "legit", 3: "phishing"}.get(r["label"]), ("legit", "phishing"), "unstated", False),
    ("hf_phishing_url_bench", "pirocheto/phishing-url", None, ["train", "test"], lambda r: r["url"],
     lambda r: {"legitimate": "legit", "phishing": "phishing"}.get(str(r.get("status")).lower()), ("legit", "phishing"), "CC-BY-4.0", True),
]

# ---------------------------------------------------------------------------------------------------- feeds --
ABUSECH = os.environ.get("ABUSECH_AUTH_KEY", "")
FEEDS = [  # id, url, parser, role(s), license, commercial, needs
    ("feed_urlhaus_recent", "https://urlhaus-api.abuse.ch/v2/files/exports/{ABUSECH}/recent.csv", "urlhaus_csv",
     ["train_url:malware", "rep_url"], "abuse.ch fair use", True, "ABUSECH_AUTH_KEY"),
    ("feed_threatfox_recent", "https://threatfox-api.abuse.ch/v2/files/exports/{ABUSECH}/recent.json", "threatfox_json",
     ["rep_ioc"], "abuse.ch fair use", True, "ABUSECH_AUTH_KEY"),
    ("feed_feodo_ipblocklist", "https://feodotracker.abuse.ch/downloads/ipblocklist.txt", "ip_lines", ["rep_ip"], "abuse.ch CC0", True, None),
    ("feed_sslbl_ips", "https://sslbl.abuse.ch/blacklist/sslipblacklist.txt", "ip_lines", ["rep_ip"], "abuse.ch CC0", True, None),
    ("feed_openphish_community", "https://openphish.com/feed.txt", "url_lines", ["train_url:phishing", "rep_url"],
     "OpenPhish community (non-commercial)", False, None),
    ("feed_phishing_database_links", "https://raw.githubusercontent.com/mitchellkrogza/Phishing.Database/master/phishing-links-ACTIVE.txt",
     "url_lines", ["train_url:phishing", "rep_url"], "MIT", True, None),
    ("feed_phishing_database_domains", "https://raw.githubusercontent.com/mitchellkrogza/Phishing.Database/master/phishing-domains-ACTIVE.txt",
     "domain_lines", ["rep_domain"], "MIT", True, None),
    ("feed_phishing_army", "https://phishing.army/download/phishing_army_blocklist.txt", "domain_lines", ["rep_domain"],
     "CC BY-NC 4.0", False, None),
    ("feed_spamhaus_drop", "https://www.spamhaus.org/drop/drop_v4.json", "spamhaus_json", ["rep_net"], "Spamhaus (attribution)", True, None),
    ("feed_firehol_level1", "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset", "ip_lines",
     ["rep_net"], "FireHOL (per-list terms)", True, None),
    ("feed_blocklist_de", "https://lists.blocklist.de/lists/all.txt", "ip_lines", ["rep_ip"], "blocklist.de (free)", True, None),
    ("feed_cins_army", "https://cinsscore.com/list/ci-badguys.txt", "ip_lines", ["rep_ip"], "CINS Army (free)", True, None),
    ("feed_et_compromised", "https://rules.emergingthreats.net/blockrules/compromised-ips.txt", "ip_lines", ["rep_ip"],
     "Proofpoint ET Open", True, None),
    ("feed_disposable_email", "https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/master/disposable_email_blocklist.conf",
     "domain_lines", ["rep_disposable"], "CC0", True, None),
    ("feed_tor_exits", "https://check.torproject.org/torbulkexitlist", "ip_lines", ["rep_tor"], "Tor Project (public)", True, None),
    ("feed_tranco_top", "https://tranco-list.eu/top-1m.csv.zip", "tranco_zip", ["train_url:legit", "rep_allow"], "Tranco (CC BY 4.0)", True, None),
]


def _http(url: str, timeout=60) -> Optional[bytes]:
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Djorge-Sentinel/1.0 (+defensive research)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        print(f"WARNING feed unreachable {url.split('?')[0][:80]}: {type(e).__name__}", flush=True)
        return None


def _lines(b: bytes) -> List[str]:
    return [l.strip() for l in b.decode("utf-8", "replace").splitlines() if l.strip() and not l.lstrip().startswith(("#", ";", "//"))]


def parse_feed(kind: str, b: bytes) -> Dict[str, list]:
    out = defaultdict(list)
    if kind == "url_lines":
        out["url"] = [l for l in _lines(b) if l.startswith("http")]
    elif kind == "domain_lines":
        out["domain"] = [l.split()[-1].lower() for l in _lines(b) if "." in l]
    elif kind == "ip_lines":
        for l in _lines(b):
            tok = l.split()[0].split(",")[0]
            try:
                out["net"].append(str(ipaddress.ip_network(tok, strict=False)))
            except ValueError:
                pass
    elif kind == "spamhaus_json":
        for l in b.decode("utf-8", "replace").splitlines():
            try:
                j = json.loads(l)
                if "cidr" in j:
                    out["net"].append(j["cidr"])
            except ValueError:
                pass
    elif kind == "urlhaus_csv":
        rows = [l for l in b.decode("utf-8", "replace").splitlines() if l and not l.startswith("#")]
        for r in csv.reader(rows):
            if len(r) > 2 and r[2].startswith("http"):
                out["url"].append(r[2])
    elif kind == "threatfox_json":
        try:
            j = json.loads(b)
            for v in (j.values() if isinstance(j, dict) else []):
                for ioc in v if isinstance(v, list) else []:
                    val, t = str(ioc.get("ioc_value", "")), str(ioc.get("ioc_type", ""))
                    if t == "url":
                        out["url"].append(val)
                    elif t == "domain":
                        out["domain"].append(val.lower())
                    elif t.startswith("ip"):
                        out["net"].append(val.split(":")[0] + "/32")
        except ValueError:
            pass
    elif kind == "tranco_zip":
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            name = z.namelist()[0]
            for i, l in enumerate(z.read(name).decode().splitlines()):
                if i >= 20000:
                    break
                out["domain"].append(l.split(",")[-1].strip().lower())
    return out

# ----------------------------------------------------------------------------------------------- generators --


def _gen_sources(rng, n):
    from . import scams_br
    return {"gen_scams_br": (scams_br.generate(rng, n, held=False), "MIT (generated by DJORGE)"),
            "gen_hard_negatives": (scams_br.hard_negatives(rng, n // 2, held=False), "MIT (generated by DJORGE)"),
            "gen_spoofed_senders": (scams_br.spoofed_senders(rng, n // 2, held=False), "MIT (generated by DJORGE)")}

# --------------------------------------------------------------------------------------------------- build --


def _is_test(text: str, pct: float = 0.04) -> bool:
    return int(hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest(), 16) % 10000 < pct * 10000


def _hf_rows(hf, config, splits):
    from datasets import load_dataset
    for sp in splits:
        try:
            ds = load_dataset(hf, config, split=sp) if config else load_dataset(hf, split=sp)
        except Exception as e:
            print(f"WARNING skip {hf}:{sp} ({type(e).__name__}: {str(e)[:120]})", flush=True)
            continue
        for r in ds.shuffle(seed=0):
            yield r


def build(out_dir: str, per_source: int = 3000, test_per_source: int = 150, allow_nc: bool = False, seed: int = 0):
    rng = random.Random(seed)
    train, test, stats, rep = [], [], {}, defaultdict(set)
    seen = set()

    def take(sid, recs: Iterable[dict], lic, commercial):
        if not commercial and not allow_nc:
            stats[sid] = {"skipped": "non-commercial licence (use --allow-nc for research)"}
            return
        ntr = nte = 0
        for r in recs:
            if r is None:
                continue
            key = r["source"][:300]
            if key in seen:
                continue
            seen.add(key)
            r["meta"]["provenance"], r["meta"]["license"] = sid, lic
            if _is_test(r["source"]) and nte < test_per_source:
                r["split"] = "test"; test.append(r); nte += 1
            elif ntr < per_source:
                r["split"] = "train"; train.append(r); ntr += 1
            if ntr >= per_source and nte >= test_per_source:
                break
        stats[sid] = {"train": ntr, "test": nte, "license": lic, "commercial": commercial}
        print(f"{sid:<34} train {ntr:>6}  test {nte:>4}", flush=True)

    for sid, hf, cfg, splits, tf, lf, lang, lic, com, dom in HF:
        def gen():
            for row in _hf_rows(hf, cfg, splits):
                try:
                    t = lf(row)
                    if t is None:
                        continue
                    yield message_record(tf(row), t, lang if lang != "multi" else "en", sid, lic, dom, rng)
                except Exception:
                    continue
        take(sid, gen(), lic, com)
    for sid, hf, cfg, splits, uf, kf, opts, lic, com in HF_URL:
        def gen():
            for row in _hf_rows(hf, cfg, splits):
                try:
                    k = kf(row)
                    if k:
                        yield url_record(uf(row), k, sid, lic, rng, opts)
                except Exception:
                    continue
        take(sid, gen(), lic, com)
    for sid, url, parser, roles, lic, com, need in FEEDS:
        if need and not os.environ.get(need):
            stats[sid] = {"skipped": f"set {need} (free key) to use this feed"}
            print(f"{sid:<34} skipped (needs {need})", flush=True)
            continue
        if not com and not allow_nc:
            stats[sid] = {"skipped": "non-commercial licence (use --allow-nc for research)"}
            continue
        b = _http(url.replace("{ABUSECH}", ABUSECH))
        if not b:
            stats[sid] = {"skipped": "unreachable"}
            continue
        p = parse_feed(parser, b)
        for role in roles:
            if role.startswith("train_url:"):
                kind = role.split(":")[1]
                items = p.get("url") or [f"{d}/" for d in p.get("domain", [])]
                rng.shuffle(items)
                take(sid, (url_record(u, kind, sid, lic, rng, ("legit", "phishing", "malware")) for u in items), lic, com)
            elif role == "rep_url":
                rep["url"].update(p.get("url", []))
            elif role in ("rep_domain", "rep_disposable", "rep_allow"):
                rep[role.replace("rep_", "")].update(p.get("domain", []))
            elif role in ("rep_ip", "rep_net", "rep_ioc", "rep_tor"):
                key = {"rep_tor": "tor"}.get(role, "net")
                rep[key].update(p.get("net", []))
                rep["url"].update(p.get("url", [])); rep["domain"].update(p.get("domain", []))
        stats.setdefault(sid, {})["reputation_items"] = sum(len(v) for v in p.values())
        time.sleep(1)
    for sid, (recs, lic) in _gen_sources(rng, per_source).items():
        take(sid, recs, lic, True)
        held = {"gen_scams_br": "generate", "gen_hard_negatives": "hard_negatives", "gen_spoofed_senders": "spoofed_senders"}[sid]
        from . import scams_br
        for r in getattr(scams_br, held)(random.Random(seed + 7), test_per_source, held=True):
            r["split"], r["meta"]["provenance"] = "test", sid
            test.append(r)
    rng.shuffle(train)
    os.makedirs(out_dir, exist_ok=True)
    for fn, recs in (("sentinel_train.jsonl", train), ("sentinel_test.jsonl", test)):
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    repj = {k: sorted(v) for k, v in rep.items()}
    repj["_meta"] = {"built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "attribution": "Includes data from abuse.ch, The Spamhaus Project, FireHOL, blocklist.de, CINS Army, "
                                    "Proofpoint ET Open, Phishing.Database, Tor Project, Tranco."}
    json.dump(repj, open(os.path.join(out_dir, "sentinel_reputation.json"), "w"))
    manifest = {"sources": stats, "n_sources": len(HF) + len(HF_URL) + len(FEEDS) + 3, "train": len(train), "test": len(test),
                "by_threat": dict(Counter(r["meta"]["threat"] for r in train)),
                "reputation": {k: len(v) for k, v in rep.items()}}
    json.dump(manifest, open(os.path.join(out_dir, "sentinel.manifest.json"), "w"), indent=1)
    print(json.dumps({k: manifest[k] for k in ("n_sources", "train", "test", "by_threat", "reputation")}, ensure_ascii=False))
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-source", type=int, default=3000)
    ap.add_argument("--test", type=int, default=150)
    ap.add_argument("--allow-nc", action="store_true", help="include non-commercial sources (research only)")
    a = ap.parse_args(argv)
    build(a.out_dir, a.per_source, a.test, a.allow_nc)


if __name__ == "__main__":
    main()
