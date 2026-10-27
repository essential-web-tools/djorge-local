"""Public-data pipeline for Origo: acquire -> map to DJORGE tasks -> validate -> filter -> dedup -> decontaminate.

  python -m djorge.training.public_data --out data/public_v1.jsonl [--max-per-source 4000] [--allow-nc]
  python -m djorge.training.public_data --from-jsonl my.jsonl --out data/clean.jsonl     # clean your own data

Every record keeps provenance + license. Non-commercial sources (XNLI) are excluded unless --allow-nc.
Benchmark contamination (8-gram overlap with djorge-real contexts) is removed, never just reported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import unicodedata
from typing import Callable, Dict, Iterable, List

MANIFEST = [
    # id, hf path, config(s), license, commercial_ok, DJORGE task, why
    {"id": "squad_v2", "hf": "rajpurkar/squad_v2", "license": "CC BY-SA 4.0", "commercial": True, "task": "answer",
     "why": "grounded extraction; unanswerable questions teach empty evidence -> abstention"},
    {"id": "mlqa", "hf": "facebook/mlqa", "configs": ["mlqa.es.es", "mlqa.de.de", "mlqa.zh.zh", "mlqa.hi.hi", "mlqa.ar.ar", "mlqa.vi.vi"],
     "license": "CC BY-SA 3.0", "commercial": True, "task": "answer", "why": "multilingual extraction (do not report MLQA scores)"},
    {"id": "wikiann", "hf": "unimelb-nlp/wikiann", "configs": ["en", "pt", "es", "fr", "de", "zh", "hi", "ar", "ja", "ru", "id"],
     "license": "ODC-By", "commercial": True, "task": "entity", "why": "ENTITY spans in the target languages"},
    {"id": "massive", "hf": "AmazonScience/massive", "configs": ["en-US", "pt-PT", "es-ES", "fr-FR", "de-DE", "zh-CN", "hi-IN", "ar-SA", "ja-JP", "ru-RU", "id-ID"],
     "license": "CC BY 4.0", "commercial": True, "task": "choice", "why": "intent/scenario routing in 51 locales (real user utterances)"},
    {"id": "banking77", "hf": "PolyAI/banking77", "license": "CC BY 4.0", "commercial": True, "task": "choice",
     "why": "fine-grained support routing (77 intents) with hard near-miss options"},
    {"id": "paws_x", "hf": "google-research-datasets/paws-x", "configs": ["en", "es", "fr", "de", "zh", "ja"],
     "license": "custom (free use with attribution)", "commercial": True, "task": "noul",
     "why": "adversarial paraphrase/non-paraphrase pairs (word-order traps)"},
    {"id": "xnli", "hf": "facebook/xnli", "configs": ["en", "es", "fr", "de", "zh", "hi", "ar", "ru"], "license": "CC BY-NC 4.0",
     "commercial": False, "task": "noul", "why": "entailment/contradiction (negation, quantifiers) - research only"},
]


# ------------------------------------------------------------------ mappers --
def _answer(ex, lang, src):
    ctx = ex["context"]
    ans = ex["answers"]
    spans = [[s, s + len(t)] for s, t in zip(ans["answer_start"], ans["text"]) if ctx[s:s + len(t)] == t][:1]
    return {"task": "answer", "ask": ex["question"], "source": ctx, "spans": spans, "meta": {"lang": lang}}


def _wikiann(ex, lang, src, rng):
    text, offs = "", []
    for t in ex["tokens"]:
        offs.append((len(text), len(text) + len(t)))
        text += t + " "
    text = text.strip()
    out = []
    for bt, et in ((1, "Person"), (3, "Organization"), (5, "Location")):
        spans, cur = [], None
        for (s, e), tg in zip(offs, ex["ner_tags"]):
            if tg == bt:
                if cur: spans.append(cur)
                cur = [s, e]
            elif tg == bt + 1 and cur:
                cur[1] = e
            else:
                if cur: spans.append(cur); cur = None
        if cur: spans.append(cur)
        if spans or rng.random() < 0.25:                    # keep some negatives (no entity of this type)
            out.append({"task": "resolve", "mode": "ENTITY", "target": et, "source": text, "spans": spans,
                        "polarity": ["asserted"] * len(spans), "meta": {"lang": lang}})
    return out


def _choice_from_labels(text, gold, label_names, rng, k=(3, 6)):
    n = rng.randint(*k)
    negs = rng.sample([l for l in label_names if l != gold], min(n - 1, len(label_names) - 1))
    opts = negs + [gold]
    rng.shuffle(opts)
    return {"task": "CHOICE", "source": text, "options": [o.replace("_", " ") for o in opts],
            "descriptions": [""] * len(opts), "ask": "Which intent does this message express?", "label": gold.replace("_", " ")}


# ---------------------------------------------------------------- acquisition --
def acquire(max_per_source: int, allow_nc: bool, seed: int = 0) -> Iterable[dict]:
    from datasets import load_dataset
    rng = random.Random(seed)
    for src in MANIFEST:
        if not src["commercial"] and not allow_nc:
            print(f"skip {src['id']} (non-commercial license; use --allow-nc for research)")
            continue
        try:
            cfgs = src.get("configs") or [None]
            per = max(1, max_per_source // len(cfgs))
            n = 0
            for cfg in cfgs:
                lang = (cfg or "en").split(".")[-1].split("-")[0]
                ds = load_dataset(src["hf"], cfg, split="train") if cfg else load_dataset(src["hf"], split="train")
                ds = ds.shuffle(seed=seed).select(range(min(per, len(ds))))
                names = ds.features[next(k for k in ("intent", "label") if k in ds.features)].names \
                    if src["task"] == "choice" else None
                for ex in ds:
                    if src["task"] == "answer":
                        recs = [_answer(ex, lang, src)]
                    elif src["task"] == "entity":
                        recs = _wikiann(ex, lang, src, rng)
                    elif src["task"] == "choice":
                        text = ex.get("utt") or ex.get("text")
                        gold = names[ex["intent"] if "intent" in ex else ex["label"]]
                        recs = [_choice_from_labels(text, gold, names, rng)]
                        recs[0]["meta"] = {"lang": lang}
                    elif src["task"] == "noul":
                        if "premise" in ex:                     # XNLI: 0 entail, 1 neutral, 2 contradiction
                            if ex["label"] == 1:
                                continue
                            recs = [{"task": "NOUL", "source": ex["premise"], "options": [ex["hypothesis"]],
                                     "label": ex["label"] == 0, "meta": {"lang": lang}}]
                        else:                                   # PAWS-X paraphrase
                            recs = [{"task": "NOUL", "source": ex["sentence1"],
                                     "options": [f'This means the same as: "{ex["sentence2"]}"'],
                                     "label": bool(ex["label"]), "meta": {"lang": lang}}]
                    for r in recs:
                        r["split"] = "train"
                        r["meta"].update(provenance=src["id"], license=src["license"], commercial=src["commercial"])
                        n += 1
                        yield r
            print(f"{src['id']}: {n} records")
        except Exception as e:                                  # hub ids/configs change; never silently fake data
            print(f"WARNING {src['id']} failed: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- filtering --
def _norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).casefold()).strip()


def valid(r) -> bool:
    src = r.get("source") or ""
    if not (8 <= len(src) <= 4000):
        return False
    for a, b in r.get("spans", []):
        if not (0 <= a < b <= len(src)) or not src[a:b].strip():
            return False
    if r["task"] == "CHOICE" and r.get("label") not in r.get("options", []):
        return False
    letters = sum(ch.isalpha() for ch in src)
    return letters / max(len(src), 1) > 0.4                    # drop tables / symbol soup


def _shingles(s, n=5):
    w = re.findall(r"\w+", _norm(s))
    return {" ".join(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


def _minhash(sh, k=64):
    if not sh:
        return tuple([0] * k)
    return tuple(min(int(hashlib.blake2b(f"{i}:{x}".encode(), digest_size=8).hexdigest(), 16) for x in sh) for i in range(k))


def dedup(records: List[dict], threshold: float = 0.8, bands: int = 16) -> List[dict]:
    """Exact (normalized) + near-duplicate removal on (source, question) with MinHash-LSH."""
    seen, buckets, out = set(), {}, []
    rows = 64 // bands
    for r in records:
        key = _norm(r["source"]) + "||" + _norm(r.get("ask", "") or r.get("target", "") or " ".join(r.get("options", [])))
        h = hashlib.sha1(key.encode()).hexdigest()
        if h in seen:
            continue
        sig = _minhash(_shingles(key))
        cands = set()
        for b in range(bands):
            cands |= set(buckets.get((b, sig[b * rows:(b + 1) * rows]), []))
        if any(sum(x == y for x, y in zip(sig, out[c]["_sig"])) / 64 >= threshold for c in cands):
            continue
        seen.add(h)
        r["_sig"] = sig
        idx = len(out)
        out.append(r)
        for b in range(bands):
            buckets.setdefault((b, sig[b * rows:(b + 1) * rows]), []).append(idx)
    for r in out:
        r.pop("_sig", None)
    return out


def decontaminate(records: List[dict], bench_path: str, n: int = 8) -> List[dict]:
    items = json.load(open(bench_path))
    bad = set()
    for it in items:
        w = re.findall(r"\w+", it["mc"]["context"].casefold())
        bad |= {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}
    out = []
    for r in records:
        w = re.findall(r"\w+", r["source"].casefold())
        if not ({" ".join(w[i:i + n]) for i in range(len(w) - n + 1)} & bad):
            out.append(r)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/public_v1.jsonl")
    ap.add_argument("--max-per-source", type=int, default=4000)
    ap.add_argument("--allow-nc", action="store_true")
    ap.add_argument("--from-jsonl", default=None, help="clean an existing JSONL instead of downloading")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bench = os.path.join(here, "benchmarks", "real", "djorge_real_v1.json")
    raw = [json.loads(l) for l in open(a.from_jsonl)] if a.from_jsonl else list(acquire(a.max_per_source, a.allow_nc, a.seed))
    stats = {"raw": len(raw)}
    v = [r for r in raw if valid(r)]; stats["valid"] = len(v)
    d = dedup(v); stats["dedup"] = len(d)
    c = decontaminate(d, bench); stats["decontaminated"] = len(c)
    for i, r in enumerate(c):
        r["id"] = f"pub-{r['meta'].get('provenance', 'x')}-{i}"
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        for r in c:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    by = {}
    for r in c:
        k = r["meta"].get("provenance", "?"); by[k] = by.get(k, 0) + 1
    stats["by_source"] = by
    json.dump({"stats": stats, "manifest": MANIFEST}, open(a.out.replace(".jsonl", ".manifest.json"), "w"), indent=1)
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
