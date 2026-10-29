"""Origo v2 evaluation - per-item results so two versions can be compared on the SAME items (paired gate).

    python -m djorge.training.origo_eval --artifact A --out A_eval.json [--broad broad_test.jsonl]
           [--spans spans_pt.jsonl] [--real-split dev] [--agg-n 300] [--device cuda]

Suites (each item: suite, task, id, ok, p):
  real    djorge-real benchmark (dev for selection; use --real-split hidden ONLY for the final report)
  broad   held-out public test splits (public_v2 broad_test.jsonl): CHOICE / NOUL / SCORE / SET / answer
  agg     AGG suite - SYNTHETIC (generated from templates, labelled `synthetic`): simple, number words, groups,
          negation, money, duplicate reference, distractor of another class, mass vs count
  spans   entity spans on the GLiNER-teacher TEST split: exact-span F1 per record (ok if F1 >= 0.5)
Nothing here trains or tunes anything.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLASSES = {   # class -> [(singular, plural, gender)]
    "fruta": [("banana", "bananas", "f"), ("maçã", "maçãs", "f"), ("laranja", "laranjas", "f"), ("pera", "peras", "f"),
              ("manga", "mangas", "f"), ("limão", "limões", "m"), ("abacaxi", "abacaxis", "m")],
    "animal": [("gato", "gatos", "m"), ("cachorro", "cachorros", "m"), ("cavalo", "cavalos", "m"),
               ("vaca", "vacas", "f"), ("galinha", "galinhas", "f"), ("ovelha", "ovelhas", "f")],
    "documento": [("contrato", "contratos", "m"), ("recibo", "recibos", "m"), ("boleto", "boletos", "m"),
                  ("nota fiscal", "notas fiscais", "f"), ("procuração", "procurações", "f")],
    "móvel": [("cadeira", "cadeiras", "f"), ("mesa", "mesas", "f"), ("sofá", "sofás", "m"), ("armário", "armários", "m")],
}
OTHER = {"fruta": ("caneta", "canetas", "f"), "animal": ("cadeira", "cadeiras", "f"),
         "documento": ("banana", "bananas", "f"), "móvel": ("gato", "gatos", "m")}
WORDS = {2: ("dois", "duas"), 3: ("três", "três"), 4: ("quatro", "quatro"), 5: ("cinco", "cinco"),
         6: ("seis", "seis"), 7: ("sete", "sete"), 8: ("oito", "oito"), 9: ("nove", "nove")}


def build_agg(n: int = 300, seed: int = 0):
    rng = random.Random(seed)
    cases = []
    kinds = ["simples", "por_extenso", "grupo", "negacao", "dinheiro", "referencia_repetida", "distrator", "massa"]
    for i in range(n):
        kind = kinds[i % len(kinds)]
        cls = "fruta" if kind in ("grupo", "massa") else rng.choice(list(CLASSES))   # dúzia/kg only where natural
        (a1, ap, ag), (b1, bp, bg) = rng.sample(CLASSES[cls], 2)
        n1, n2 = rng.randint(2, 9), rng.randint(2, 9)
        art = lambda g: "As" if g == "f" else "Os"
        if kind == "simples":
            t, exp = f"Comprei {n1} {ap} e {n2} {bp}.", n1 + n2
        elif kind == "por_extenso":
            t, exp = f"Comprei {WORDS[n1][ag == 'f']} {ap} e {WORDS[n2][bg == 'f']} {bp}.", n1 + n2
        elif kind == "grupo":
            g = rng.choice([1, 2])
            t = f"Comprei {'uma dúzia' if g == 1 else 'duas dúzias'} de {ap} e {n2} {bp}."
            exp = 12 * g + n2
        elif kind == "negacao":
            t, exp = f"Comprei {n1} {ap}, mas não comprei {bp}.", n1
        elif kind == "dinheiro":
            t, exp = f"Gastei R$ {n1 * 3},00 em {ap} e comprei {n2} {bp}.", n2
        elif kind == "referencia_repetida":
            t = f"Comprei {n1} {ap}. {art(ag)} {ap} estavam ótim{'a' if ag == 'f' else 'o'}s. Também comprei {n2} {bp}."
            exp = n1 + n2
        elif kind == "distrator":
            o1, op, og = OTHER[cls]
            t, exp = f"Comprei {n1} {ap} e {n2} {op}.", n1
        else:
            t, exp = f"Comprei {n1} {ap} e 2 kg de {b1}.", n1
        cases.append({"id": f"agg{i:04d}", "kind": kind, "class": cls, "text": t, "expected": exp, "synthetic": True})
    return cases


def eval_agg(z, cases):
    from ..lang.quote import quote
    items = []
    for c in cases:
        try:
            r = z.execute(f"TOTAL AGG {quote(c['class'])} FROM text", sources={"text": c["text"]})["result"]
            pred = r.get("value") if r.get("value") is not None else r.get("proposed_value")
            ok = pred is not None and float(pred) == float(c["expected"]) and not r.get("abstain")
            items.append({"suite": "agg", "task": c["kind"], "id": c["id"], "ok": bool(ok), "p": r.get("probability"),
                          "pred": pred, "expected": c["expected"]})
        except Exception as e:
            items.append({"suite": "agg", "task": c["kind"], "id": c["id"], "ok": False, "p": None,
                          "error": type(e).__name__})
    return items


def eval_real(z, split):
    sys.path.insert(0, os.path.join(HERE, "benchmarks"))
    from real_eval import score_item
    data = json.load(open(os.path.join(HERE, "benchmarks", "real", "djorge_real_v1.json")))
    items = []
    for it in data:
        if split != "all" and it["split"] != split:
            continue
        try:
            ok, answered, p, pred = score_item(it, z.execute(it["query"]))
        except Exception:
            ok, p = False, None
        items.append({"suite": "real", "task": it["category"], "id": it["id"], "ok": bool(ok), "p": p})
    return items


def eval_broad(eng, path, n_max=4000, suite="broad", key="provenance", split=None):
    from .calibrate import req_of
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    recs = [r for r in recs if r["task"] in ("CHOICE", "NOUL", "SCORE", "SET") and (split is None or r.get("split") == split)]
    random.Random(0).shuffle(recs)
    keep = []
    for r in recs[:n_max]:
        try:
            eng._rows(req_of(r)); keep.append(r)
        except ValueError:
            pass
    outs = eng.run([req_of(r) for r in keep])
    items = []
    for i, (r, o) in enumerate(zip(keep, outs)):
        t = r["task"]
        if t == "CHOICE":
            ok, p = o["answer"] == r["label"], o["probability"]
        elif t == "SCORE":
            a = o.get("answer")
            pred = a if isinstance(a, int) else (r["options"].index(a) if a in r["options"] else -1)
            ok, p = pred == r["label"], o.get("probability")
        elif t == "NOUL":
            ok, p = o["answer"] == bool(r["label"]), max(o["probability"], 1 - o["probability"])
        else:
            sel, gold = set(o["selected"]), set(r["label"])
            ok, p = len(sel & gold) / max(len(sel | gold), 1) >= 0.5, o.get("probability")
        items.append({"suite": suite, "task": r["meta"].get(key, "?"), "id": f"{suite[0]}{i}", "ok": bool(ok), "p": p})
    return items


def eval_spans(eng, path, n_max=1500, suite="spans"):
    from ..runtime.semantic import SemReq
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if '"test"' in l]
    recs = [r for r in recs if r.get("split") == "test" and r.get("task") == "resolve"][:n_max]
    outs = eng.run([SemReq("resolve", r["source"], mode="ENTITY", target=r["target"], threshold=0.5) for r in recs])
    items = []
    for i, (r, ms) in enumerate(zip(recs, outs)):
        pred = {(m.start, m.end) for m in ms.matches}
        gold = {tuple(s) for s in r["spans"]}
        tp = len(pred & gold)
        f1 = 1.0 if not pred and not gold else (2 * tp / (len(pred) + len(gold)) if (pred or gold) else 0.0)
        items.append({"suite": suite, "task": r["target"], "id": f"s{i}", "ok": f1 >= 0.5, "p": None,
                      "f1": round(f1, 4), "tp": tp, "n_pred": len(pred), "n_gold": len(gold)})
    return items


def summarize(items):
    g = defaultdict(lambda: {"n": 0, "ok": 0, "hc_err": 0})
    for it in items:
        for k in (it["suite"], f"{it['suite']}/{it['task']}"):
            x = g[k]; x["n"] += 1; x["ok"] += it["ok"]
            if not it["ok"] and isinstance(it.get("p"), (int, float)) and it["p"] >= 0.9:
                x["hc_err"] += 1
    out = {k: {"n": v["n"], "accuracy": round(v["ok"] / v["n"], 4), "high_conf_errors": v["hc_err"]}
           for k, v in sorted(g.items())}
    for suite in sorted({it["suite"] for it in items if "tp" in it}):
        sp = [it for it in items if it["suite"] == suite]
        tp = sum(i["tp"] for i in sp); npred = sum(i["n_pred"] for i in sp); ngold = sum(i["n_gold"] for i in sp)
        pr, rc = tp / max(1, npred), tp / max(1, ngold)
        out[suite]["micro_precision"], out[suite]["micro_recall"] = round(pr, 4), round(rc, 4)
        out[suite]["micro_f1"] = round(2 * pr * rc / max(1e-9, pr + rc), 4)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--broad", default=None)
    ap.add_argument("--spans", default=None)
    ap.add_argument("--security", default=None, help="security test jsonl (sentinel_data): suite 'seguranca' per threat")
    ap.add_argument("--commands", default=None, help="commands test jsonl (commands_data): intent + slots")
    ap.add_argument("--suite", action="append", default=[], help="extra decision suite: name=path[:meta_key] "
                    "(e.g. ferramentas=checagem_ferramentas.jsonl:group)")
    ap.add_argument("--skip-real", action="store_true", help="skip the djorge-real dev set and the AGG suite")
    ap.add_argument("--real-split", default="dev", choices=["dev", "hidden", "all"])
    ap.add_argument("--agg-n", type=int, default=320)
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args(argv)
    from .. import Djorge, DjorgeConfig
    t0 = time.time()
    cfg = DjorgeConfig(artifact=a.artifact, device=a.device); cfg.context_dir = "/tmp/djorge-eval-ctx"
    z = Djorge(artifact=a.artifact, config=cfg)
    eng = getattr(z.semantic, "engine", z.semantic)
    items = [] if a.skip_real else eval_real(z, a.real_split) + eval_agg(z, build_agg(a.agg_n))
    if a.broad and os.path.exists(a.broad):
        items += eval_broad(eng, a.broad)
    if a.spans and os.path.exists(a.spans):
        items += eval_spans(eng, a.spans)
    if a.security and os.path.exists(a.security):
        items += eval_broad(eng, a.security, n_max=3000, suite="seguranca", key="threat")
    if a.commands and os.path.exists(a.commands):
        items += eval_broad(eng, a.commands, n_max=2000, suite="comandos", key="provenance")
        items += eval_spans(eng, a.commands, n_max=2000, suite="comandos_campos")
    for spec in a.suite:
        name, rest = spec.split("=", 1)
        path, key = (rest.rsplit(":", 1) + ["provenance"])[:2] if ":" in rest and not rest.endswith(".jsonl") else (rest, "provenance")
        if os.path.exists(path):
            items += eval_broad(eng, path, n_max=4000, suite=name, key=key)
    rep = {"artifact": a.artifact, "seconds": round(time.time() - t0, 1), "summary": summarize(items), "items": items}
    json.dump(rep, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in rep["summary"].items() if "/" not in k}, ensure_ascii=False))


if __name__ == "__main__":
    main()
