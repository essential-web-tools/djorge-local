"""Load PUBLIC labelled datasets and map them to the Sentinel's fixed questions (presets.py).

Nothing is generated here: each record is a public text with its public label. Column names are read from each
dataset's own metadata (a list of candidates per source); if none is present the source is skipped and listed in the
manifest, so a mapping can be fixed without opening the data. Brazil first: Portuguese sources get full weight and a
higher cap; other languages are kept at weight 0.7. Each record carries its source licence; non-commercial sources
need --allow-nc; unreachable/gated sources are skipped with a warning.

    python -m djorge.sentinel.public_data --out-dir DATA [--per-source 6000] [--allow-nc]
writes sentinel_pub_train.jsonl, sentinel_pub_test.jsonl, sentinel_pub.manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from collections import Counter, defaultdict

from .presets import DANGER_ASK, DANGER_DESC, DANGER_LEVELS, KINDS

# id, repo, config, splits, kind, text-column candidates, label-column candidates,
#   value->option map (lowercased keys) OR None for a constant label, constant-label, lang, licence, commercial
SOURCES = [
    # mensagens / SMS  (pt primeiro)
    ("moz_smishing", "MOZNLP/MOZ-Smishing", None, ("train", "test"), "texto",
     ["text", "sms", "message", "Message"], ["label", "class"],
     {"smishing": "golpe", "fraud": "golpe", "1": "golpe", "legitimate": "seguro", "ham": "seguro", "0": "seguro"},
     None, "pt", "OpenRAIL-M", True),
    ("sms_spam_pt", "dbarbedillo/SMS_Spam_Multilingual_Collection_Dataset", None, ("train",), "texto",
     ["text_pt"], ["labels", "label"], {"spam": "spam", "ham": "seguro"}, None, "pt", "GPL", True),
    ("all_scam_spam", "FredZhang7/all-scam-spam", None, ("train",), "texto",
     ["text"], ["is_spam", "label"], {"1": "spam", "0": "seguro"}, None, "multi", "Apache-2.0", True),
    ("spam_detection", "Deysi/spam-detection-dataset", None, ("train", "test"), "texto",
     ["text"], ["label"], {"spam": "spam", "not_spam": "seguro"}, None, "en", "Apache-2.0", True),
    # e-mail
    ("phishing_email", "zefang-liu/phishing-email-dataset", None, ("train",), "email",
     ["Email Text", "text"], ["Email Type", "label"],
     {"phishing email": "phishing", "phishing": "phishing", "safe email": "legítimo", "safe": "legítimo"},
     None, "en", "LGPL-3.0", True),
    ("enron_spam", "SetFit/enron_spam", None, ("train", "test"), "email",
     ["text"], ["label"], {"1": "spam", "0": "legítimo"}, None, "en", "public corpus", True),
    # domínios / URLs
    ("phishing_url_bench", "pirocheto/phishing-url", None, ("train", "test"), "dominio",
     ["url"], ["status", "label"], {"phishing": "phishing", "legitimate": "legítimo", "legit": "legítimo"},
     None, "en", "CC-BY-4.0", True),
    # prompt (texto enviado a uma IA)
    ("prompt_injections", "deepset/prompt-injections", None, ("train", "test"), "prompt",
     ["text"], ["label"], {"1": "injeção de prompt", "0": "seguro"}, None, "multi", "Apache-2.0", True),
    ("prompt_inj_multi", "rikka-snow/prompt-injection-multilingual", None, ("train", "test"), "prompt",
     ["text"], ["label"], {"1": "injeção de prompt", "0": "seguro"}, None, "multi", "MIT", True),
    # ofensa (conteúdo nocivo em pt)
    ("told_br", "JAugusto97/told-br", "binary", ("train", "test", "validation"), "ofensa",
     ["text"], ["label"], {"1": "ofensivo", "toxic": "ofensivo", "0": "respeitoso", "not-toxic": "respeitoso"},
     None, "pt", "CC-BY-SA-4.0", True),
    ("hate_speech_pt", "hate-speech-portuguese/hate_speech_portuguese", None, ("train",), "ofensa",
     ["text"], ["label"], {"1": "ofensivo", "hate": "ofensivo", "0": "respeitoso", "no-hate": "respeitoso"},
     None, "pt", "unknown", True),
]


def _as_text(v):
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v)
    return str(v) if v is not None else ""


def _pick(row, cands):
    for c in cands:
        if c in row and row[c] not in (None, ""):
            return c
    return None


def _label(row, cands, vmap, const):
    if const is not None:
        return const
    col = _pick(row, cands)
    if col is None:
        return None
    return vmap.get(str(row[col]).strip().lower())


def _records(sid, repo, cfg, splits, kind, tcands, lcands, vmap, const, lang, lic, rng):
    from datasets import load_dataset
    k = KINDS[kind]
    w = 1.0 if lang == "pt" else 0.7
    seen_cols = set()
    for sp in splits:
        try:
            ds = load_dataset(repo, cfg, split=sp) if cfg else load_dataset(repo, split=sp)
        except Exception as e:
            print(f"  skip {sid}:{sp} ({type(e).__name__}: {str(e)[:90]})", flush=True)
            continue
        for row in ds:
            seen_cols.update(row.keys())
            tcol = _pick(row, tcands)
            lab = _label(row, lcands, vmap, const)
            if tcol is None or lab not in k["options"]:
                continue
            text = _as_text(row[tcol]).strip()
            if not (3 <= len(text) <= 4000):
                continue
            yield text, lab, w
    _records.cols[sid] = sorted(seen_cols)      # discovered columns, for the manifest


_records.cols = {}


def _choice(kind, text, lab, rng, prov, w):
    k = KINDS[kind]
    ask = k["ask"] if rng.random() < 0.7 else rng.choice(k["asks"])
    return {"task": "CHOICE", "source": text, "options": list(k["options"]), "descriptions": list(k["descriptions"]),
            "label": lab, "ask": ask, "weight": w, "split": "train",
            "meta": {"area": "security", "kind": kind, "channel": kind, "provenance": prov, "public": True}}


def _danger(kind, text, lab, prov, w):
    band = KINDS[kind]["danger"].get(lab, 0)
    return {"task": "SCORE", "source": text, "options": list(DANGER_LEVELS), "descriptions": list(DANGER_DESC),
            "label": int(band), "ask": DANGER_ASK, "weight": w, "split": "train",
            "meta": {"area": "security", "kind": kind, "channel": kind, "provenance": prov, "public": True}}


def _is_test(text, pct=0.05):
    return int(hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest(), 16) % 10000 < pct * 10000


def build(out_dir, per_source=6000, allow_nc=False, seed=0, only_kinds=None):
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    train, test, stats, seen = [], [], {}, set()
    for sid, repo, cfg, splits, kind, tc, lc, vmap, const, lang, lic, com in SOURCES:
        if only_kinds and kind not in only_kinds:
            continue
        if not com and not allow_nc:
            stats[sid] = {"skipped": "non-commercial (use --allow-nc)"}
            continue
        cap = per_source * (2 if lang == "pt" else 1)
        ntr = nte = 0
        try:
            it = _records(sid, repo, cfg, splits, kind, tc, lc, vmap, const, lang, lic, rng)
            for text, lab, w in it:
                key = (kind, text[:200])
                if key in seen:
                    continue
                seen.add(key)
                dst_test = _is_test(text)
                if dst_test and nte < max(50, cap // 20):
                    for r in (_choice(kind, text, lab, rng, sid, w), _danger(kind, text, lab, sid, w)):
                        r["split"] = "test"; test.append(r)
                    nte += 1
                elif ntr < cap:
                    train += [_choice(kind, text, lab, rng, sid, w), _danger(kind, text, lab, sid, w)]
                    ntr += 1
                if ntr >= cap and nte >= max(50, cap // 20):
                    break
        except Exception as e:
            stats[sid] = {"error": f"{type(e).__name__}: {str(e)[:120]}", "columns": _records.cols.get(sid, [])}
            print(f"  error {sid}: {e}", flush=True)
            continue
        stats[sid] = {"kind": kind, "train": ntr, "test": nte, "lang": lang, "license": lic,
                      "columns": _records.cols.get(sid, [])}
        print(f"{sid:<20} {kind:<13} train {ntr:>6}  test {nte:>4}", flush=True)
    rng.shuffle(train)
    for r in train:                                   # ~7% calibration carve-out
        if int(hashlib.sha1(r["source"][:200].encode("utf-8", "ignore")).hexdigest(), 16) % 100 < 7:
            r["split"] = "calibration"
    for fn, recs in (("sentinel_pub_train.jsonl", train), ("sentinel_pub_test.jsonl", test)):
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    m = {"train": len(train), "test": len(test), "sources": stats,
         "by_kind": dict(Counter(r["meta"]["kind"] for r in train)),
         "by_split": dict(Counter(r["split"] for r in train))}
    json.dump(m, open(os.path.join(out_dir, "sentinel_pub.manifest.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({k: m[k] for k in ("train", "test", "by_kind", "by_split")}, ensure_ascii=False))
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-source", type=int, default=6000)
    ap.add_argument("--allow-nc", action="store_true")
    ap.add_argument("--kinds", default=None, help="comma-separated subset of kinds to build")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    build(a.out_dir, a.per_source, a.allow_nc, a.seed, set(a.kinds.split(",")) if a.kinds else None)


if __name__ == "__main__":
    main()
