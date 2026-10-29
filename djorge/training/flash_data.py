"""Data for the Djorge Origo Flash Local (DOFL): every public source in one place, prepared the same way.

    python -m djorge.training.flash_data --out DIR --catalog origo_v2_train.jsonl --spans spans_pt.jsonl \
        --security-train sentinel_train.jsonl --security-test sentinel_test.jsonl \
        --commands-train commands_train.jsonl --commands-test commands_test.jsonl [--clinc] [--massive-ptbr]

Steps (all deterministic):
  1. text cleaning (djorge.runtime.clean) on security, commands and tool requests - the same function the app uses.
     Records with character offsets (spans) are only cleaned when cleaning changes nothing, so offsets stay valid;
  2. corpus markers masked in the security data (mail headers, dates, Enron-style "/ect" tokens), so the model learns
     the scam and not which dataset a text came from;
  3. tool selection + "can the app do this?" (out of scope) from real requests: MASSIVE (pt/es/en, CC BY 4.0) and
     CLINC150 (en, CC BY 3.0, with 1,200 out-of-scope requests). Nothing is generated;
  4. de-duplication everywhere, and every training text that also appears in a test is removed (no leakage);
  5. fixed parts by text fingerprint: train / escolha (10%) / validacao (5%); the tests are the "checagem" and are
     never used before the final gate. One whole security corpus is held out of training (cross-corpus check).
Writes: train.jsonl escolha.jsonl validacao.jsonl checagem_ferramentas.jsonl checagem_seguranca_fora.jsonl
        security_test.jsonl commands_test.jsonl manifest.json
Every record carries meta.group (the unit the self-regulated trainer balances and the selection compares).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
from collections import Counter, defaultdict

from ..runtime.clean import clean, is_identity

TOOLS = {"calcular": "fazer uma conta ou converter valores",
         "datas_horas": "consultar ou comparar data, hora, prazo ou fuso",
         "proximas_tarefas": "consultar a agenda, alarmes ou o que vem a seguir",
         "cards": "criar, mudar ou apagar compromisso, lembrete, alarme ou tarefa",
         "listas": "criar, mudar, ordenar ou consultar uma lista",
         "bilhete": "enviar mensagem, e-mail ou post para alguém",
         "buscar": "procurar e-mails, contatos ou dados guardados",
         "regra": "criar uma regra automática para o que chegar",
         "nenhuma": "nada disso: o app não faz este pedido"}
TOOL_PT = {"calcular": "calcular", "datas_horas": "datas e horas", "proximas_tarefas": "próximas tarefas",
           "cards": "cards e tarefas", "listas": "listas", "bilhete": "mensagem ou e-mail", "buscar": "buscar nos meus dados",
           "regra": "regra automática", "nenhuma": "nenhuma (fora do escopo)"}
MASSIVE_TOOL = {"qa_maths": "calcular", "qa_currency": "calcular", "datetime_query": "datas_horas",
                "datetime_convert": "datas_horas", "calendar_query": "proximas_tarefas", "alarm_query": "proximas_tarefas",
                "calendar_set": "cards", "calendar_remove": "cards", "alarm_set": "cards", "alarm_remove": "cards",
                "lists_createoradd": "listas", "lists_remove": "listas", "lists_query": "listas",
                "email_sendemail": "bilhete", "social_post": "bilhete", "email_query": "buscar",
                "email_querycontact": "buscar"}
CLINC_TOOL = {"calculator": "calcular", "measurement_conversion": "calcular", "exchange_rate": "calcular",
              "time": "datas_horas", "date": "datas_horas", "timezone": "datas_horas", "next_holiday": "datas_horas",
              "calendar": "proximas_tarefas", "meeting_schedule": "proximas_tarefas",
              "schedule_meeting": "cards", "reminder": "cards", "reminder_update": "cards", "alarm": "cards",
              "timer": "cards", "calendar_update": "cards", "todo_list": "listas", "todo_list_update": "listas",
              "shopping_list": "listas", "shopping_list_update": "listas", "text": "bilhete"}
ASK_TOOL = {"pt": "Qual ferramenta do app resolve este pedido?", "es": "¿Qué herramienta de la app resuelve este pedido?",
            "en": "Which app tool handles this request?"}
CAN = {"pt": "O app consegue fazer este pedido com as suas ferramentas.", "es": "La app puede hacer este pedido con sus herramientas.",
       "en": "The app can do this request with its tools."}
HDR = re.compile(r"(?i)\b(?:from|to|cc|bcc|subject|date|sent|message-id|x-[\w-]+|received|return-path|content-[\w-]+|"
                 r"mime-version|reply-to|assunto|enviado)\s*:\s*")      # header LABELS only (texts arrive on one line)
MSGID = re.compile(r"<[^<>\s]{3,200}@[^<>\s]{3,200}>")
DATE = re.compile(r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,? \d{1,2} "
                  r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{4})\b", re.I)
ENRON = re.compile(r"(?i)\b(?:hou|corp|enron)\s*/\s*ect\b|@\s*ect\b|/\s*ect\b")


def key_of(text: str) -> str:
    s = re.sub(r"\W+", " ", str(text).casefold()).strip()[:500]
    return hashlib.sha1(s.encode()).hexdigest()


def part_of(text: str, escolha=0.10, validacao=0.05) -> str:
    h = int(key_of(text)[:8], 16) / 0xFFFFFFFF
    return "escolha" if h < escolha else ("validacao" if h < escolha + validacao else "train")


def group_of(r: dict) -> str:
    m = r.get("meta") or {}
    if m.get("group"):
        return m["group"]
    area = m.get("area") or ("trechos_pt" if r.get("task") == "resolve" and "gliner" in str(m.get("provenance", "")) else "catalogo")
    kind = "trechos" if r.get("task") in ("resolve", "answer") else "decisao"
    return f"{area}:{kind}"


def mask_security(text: str) -> str:
    t = HDR.sub(" ", text)
    t = MSGID.sub(" ", t)
    t = DATE.sub("[data]", t)
    t = ENRON.sub(" ", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def _jl(path):
    if not path or not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _clean_rec(r: dict, mask=False) -> dict | None:
    src = r.get("source", "")
    if r.get("task") in ("resolve", "answer"):              # offsets must stay valid
        return r if is_identity(src) else None
    if mask:
        src = mask_security(src)
    r = dict(r)
    r["source"] = clean(src).text
    return r if len(r["source"]) >= 3 else None


def tool_records(texts, rng):
    """[(text, tool, lang, provenance)] -> CHOICE (which tool) + NOUL (can the app do it?) records."""
    out = []
    names = list(TOOLS)
    for text, tool, lang, prov in texts:
        lang = lang if lang in ASK_TOOL else "en"
        t = clean(text).text
        meta = {"area": "tools", "lang": lang, "provenance": prov, "tool": tool}
        opts = names[:]
        rng.shuffle(opts)
        out.append({"task": "CHOICE", "source": t, "options": [TOOL_PT[o] for o in opts], "label": TOOL_PT[tool],
                    "descriptions": [TOOLS[o] for o in opts], "ask": ASK_TOOL[lang],
                    "meta": dict(meta, group="tools:ferramenta")})
        out.append({"task": "NOUL", "source": t, "options": [CAN[lang]], "label": tool != "nenhuma",
                    "meta": dict(meta, group="tools:fora_do_escopo")})
    return out


def massive_tool_texts(cmd_recs):
    out = []
    for r in cmd_recs:
        m = r.get("meta") or {}
        if r.get("task") == "CHOICE" and m.get("intent"):
            out.append((r["source"], MASSIVE_TOOL.get(m["intent"], "nenhuma"), m.get("lang", "en"),
                        f"massive:{m['intent']}"))
    return out


def clinc_texts(split):
    from datasets import load_dataset
    ds = load_dataset("clinc/clinc_oos", "plus", split=split)
    names = ds.features["intent"].names
    return [(row["text"], "nenhuma" if names[row["intent"]] == "oos" else CLINC_TOOL.get(names[row["intent"]], "nenhuma"),
             "en", f"clinc:{names[row['intent']]}") for row in ds]


def build(a):
    rng = random.Random(7)
    stats = Counter()
    tests = {"security_test": [], "commands_test": [], "checagem_ferramentas": [], "checagem_seguranca_fora": []}
    pool = []
    # ---- catalog + Portuguese spans (already clean public data; only offsets-safe cleaning)
    for path, tag in ((a.catalog, "catalogo"), (a.spans, "trechos_pt")):
        for r in _jl(path):
            if r.get("split", "train") not in ("train", None) and tag == "trechos_pt":
                continue
            c = _clean_rec(r)
            if c:
                c.setdefault("meta", {})
                if tag == "trechos_pt":
                    c["meta"].setdefault("group", "trechos_pt:trechos")
                pool.append(c); stats[f"in:{tag}"] += 1
    # ---- security: cleaned + masked; one whole corpus held out
    sec_tr = [r for r in (_clean_rec(r, mask=True) for r in _jl(a.security_train)) if r]
    sec_te = [r for r in (_clean_rec(r, mask=True) for r in _jl(a.security_test)) if r]
    provs = Counter(r["meta"].get("provenance") for r in sec_tr if r["meta"].get("lang") != "url")
    hold = a.holdout_source or (sorted(provs, key=lambda p: provs[p])[len(provs) // 2] if provs else None)
    for r in sec_tr:
        r["meta"]["group"] = "security:" + ("link" if r["meta"].get("lang") == "url" else "mensagem")
        (tests["checagem_seguranca_fora"] if r["meta"].get("provenance") == hold else pool).append(r)
    for r in sec_te:
        r["meta"]["group"] = "security:" + ("link" if r["meta"].get("lang") == "url" else "mensagem")
        (tests["checagem_seguranca_fora"] if r["meta"].get("provenance") == hold else tests["security_test"]).append(r)
    stats["security_holdout_source"] = hold
    # ---- commands (MASSIVE): only the fields the app uses are trained as spans
    keep_slots = set(a.app_slots.split(","))
    cmd_tr, cmd_te = _jl(a.commands_train), _jl(a.commands_test)
    if a.massive_ptbr:
        cmd_tr += _jl(a.massive_ptbr)
    for r in cmd_tr:
        if r["task"] == "resolve" and r["meta"].get("slot") not in keep_slots:
            continue
        c = _clean_rec(r)
        if c:
            c["meta"]["group"] = "commands:" + ("campos" if r["task"] == "resolve" else "pedido")
            pool.append(c)
    for r in cmd_te:
        c = _clean_rec(r)
        if c:
            c["meta"]["group"] = "commands:" + ("campos" if r["task"] == "resolve" else "pedido")
            tests["commands_test"].append(c)
    # ---- tools + out of scope (MASSIVE + CLINC150)
    pool += tool_records(massive_tool_texts(cmd_tr), rng)
    tests["checagem_ferramentas"] += tool_records(massive_tool_texts(cmd_te), rng)
    if a.clinc:
        try:
            pool += tool_records(clinc_texts("train"), rng)
            tests["checagem_ferramentas"] += tool_records(clinc_texts("test"), rng)
        except Exception as e:                          # no internet / no datasets: continue without CLINC
            stats["clinc_error"] = f"{type(e).__name__}: {e}"[:200]
    # ---- leakage + duplicates
    test_keys = {key_of(r["source"]) for v in tests.values() for r in v}
    for name in ("broad_test", "spans_test"):
        for r in _jl(getattr(a, name, None)):
            test_keys.add(key_of(r["source"]))
    seen, parts = set(), {"train": [], "escolha": [], "validacao": []}
    for r in pool:
        k = key_of(r["source"]) + "|" + str(r.get("target") or r.get("ask") or "") + "|" + r["task"]
        if key_of(r["source"]) in test_keys:
            stats["removed_leak"] += 1; continue
        if k in seen:
            stats["removed_dup"] += 1; continue
        seen.add(k)
        r.setdefault("meta", {})["group"] = group_of(r)
        r["split"] = "train"
        parts[part_of(r["source"])].append(r)
    os.makedirs(a.out, exist_ok=True)
    for name, recs in list(parts.items()) + list(tests.items()):
        if name in tests:
            for r in recs:
                r["split"] = "test"
                r.setdefault("meta", {})["group"] = group_of(r)
        rng.shuffle(recs)
        with open(os.path.join(a.out, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    man = {"parts": {k: len(v) for k, v in parts.items()}, "tests": {k: len(v) for k, v in tests.items()},
           "groups_train": dict(Counter(r["meta"]["group"] for r in parts["train"])), "stats": dict(stats),
           "licenses": {"MASSIVE": "CC BY 4.0", "CLINC150": "CC BY 3.0", "security": "see sentinel.manifest.json"}}
    json.dump(man, open(os.path.join(a.out, "manifest.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(man, ensure_ascii=False))
    return man


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--catalog"); ap.add_argument("--spans")
    ap.add_argument("--security-train"); ap.add_argument("--security-test")
    ap.add_argument("--commands-train"); ap.add_argument("--commands-test")
    ap.add_argument("--broad-test"); ap.add_argument("--spans-test")
    ap.add_argument("--clinc", action="store_true", help="add CLINC150 (needs internet + datasets)")
    ap.add_argument("--massive-ptbr", default=None, help="jsonl of MASSIVE pt-BR commands (train only)")
    ap.add_argument("--holdout-source", default=None, help="security corpus kept out of training (default: median size)")
    ap.add_argument("--app-slots", default="person,email_address,email_folder,app_name,list_name,business_name",
                    help="MASSIVE slots trained as spans; dates/times come from the runtime clock rules, not the span head")
    build(ap.parse_args(argv))


if __name__ == "__main__":
    main()
