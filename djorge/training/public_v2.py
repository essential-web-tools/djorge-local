"""Broad public-data catalog for Origo (v2): many areas, all mapped to DJORGE decisions, licence-gated.

    python -m djorge.training.public_v2 --out-dir /content/drive/MyDrive/djorge/data [--allow-nc] [--base 900 --spec 3000]

Writes
  public_v2.jsonl            base training mix (every area, small slice each) + Jev-style agent patterns + v1 sources
  <specialist>.jsonl         bigger slices per area for specialist LoRAs: safety, support, legal, medical, finance, knowledge
  broad_test.jsonl           held-out TEST-split samples per source (never trained on) for benchmarks/broad_eval.py
  public_v2.manifest.json    sources, licences, counts

Design (usage patterns of System-One models: routing, triage, field selection, moderation, gating, scoring):
  * label sets with many classes (Banking77, CLINC, MASSIVE, LEDGAR, DBpedia) become CHOICE over a SAMPLED option list
    (3-8 options incl. gold) - callers bring their own option lists, so the model learns to read options, not memorise ids;
  * CLINC out-of-scope rows and ~15% of other rows carry an "other" option -> the model learns "none of these";
  * binary labels -> NOUL (probability that a statement is true); ordered labels -> SCORE; multi-label -> SET;
  * every record keeps provenance + licence; non-commercial sources are skipped unless --allow-nc;
  * the same validation / dedup / benchmark decontamination as public_data v1.
Hub ids and schemas change: a source that fails to load is reported and skipped, never faked.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from typing import Callable, Dict, List

from .public_data import MANIFEST as V1, acquire as acquire_v1, decontaminate, dedup, valid

FIN_TOPICS = ["Analyst Update", "Fed | Central Banks", "Company | Product News", "Treasuries | Corporate Debt", "Dividend",
              "Earnings", "Energy | Oil", "Financials", "Currencies", "General News | Opinion", "Gold | Metals | Materials",
              "IPO", "Legal | Regulation", "M&A | Investments", "Macro", "Markets", "Politics", "Personnel Change",
              "Stock Commentary", "Stock Movement"]
SAFETY_MAP = [("minor", None), ("suicide", "self_harm"), ("self harm", "self_harm"), ("violence", "violence_weapons"),
              ("weapon", "violence_weapons"), ("threat", "violence_weapons"), ("hate", "hate_harassment"),
              ("harass", "hate_harassment"), ("substance", "illegal_drugs"), ("malware", "cyber_abuse"),
              ("pii", "cyber_abuse"), ("privacy", "cyber_abuse"), ("manipulation", "manipulation"), ("fraud", "manipulation")]


def _options(gold: str, names: List[str], rng, k=(3, 8), other_p=0.15, force_other=False):
    names = [n for n in names if n]
    n = rng.randint(*k)
    pool = [x for x in names if x != gold]
    opts = rng.sample(pool, min(n - 1, len(pool)))
    if not force_other:
        opts.append(gold)
    if force_other or rng.random() < other_p:
        opts.append("other")
    rng.shuffle(opts)
    return opts


def _human(s: str) -> str:
    return s.replace("_", " ").strip()


def choice_labels(text_key, label_key, ask, names=None, label_text_key=None, oos=None):
    """Single-label classification -> CHOICE over a sampled option list. Label names come from (in order):
    the explicit `names`, the ClassLabel feature, or a prescan of the label-text column."""
    def f(ex, rng, feats):
        text = ex.get(text_key)
        if not text:
            return []
        if label_text_key:
            gold, nm = ex[label_text_key], f.names
        else:
            nm = names or f.names or feats[label_key].names
            gold = nm[ex[label_key]]
        gold, nm = _human(gold), [_human(x) for x in nm]
        if oos and gold == _human(oos):
            opts = _options(None, [x for x in nm if x != gold], rng, force_other=True)
            return [{"task": "CHOICE", "source": text, "options": opts, "label": "other", "ask": ask, "descriptions": [""] * len(opts)}]
        opts = _options(gold, [x for x in nm if not oos or x != _human(oos)], rng)
        return [{"task": "CHOICE", "source": text, "options": opts, "label": gold, "ask": ask, "descriptions": [""] * len(opts)}]

    def prescan(ds):
        if label_text_key and not f.names:
            f.names = sorted(set(ds[label_text_key]))
        elif label_key and not names and not f.names and hasattr(ds.features.get(label_key), "names"):
            f.names = list(ds.features[label_key].names)
    f.names = list(names) if names else None
    f.prescan = prescan
    return f


def mc_question(q_key, choices_key, answer_key, ctx_key=None):
    """ARC / CommonsenseQA style: choices = {text: [...], label: [...]}, answerKey = label."""
    def f(ex, rng, feats):
        ch = ex[choices_key]
        texts, labels = list(ch["text"]), list(ch["label"])
        if ex[answer_key] not in labels:
            return []
        gold = texts[labels.index(ex[answer_key])]
        src = ex[ctx_key] if ctx_key else ex[q_key]
        return [{"task": "CHOICE", "source": src, "options": texts, "label": gold, "ask": ex[q_key] if ctx_key else "Which answer is correct?",
                 "descriptions": [""] * len(texts)}]
    return f


def medmcqa(ex, rng, feats):
    opts = [ex.get(k) for k in ("opa", "opb", "opc", "opd")]
    if not all(opts) or ex.get("cop") not in (0, 1, 2, 3) or len(set(opts)) < 4:
        return []
    return [{"task": "CHOICE", "source": ex["question"], "options": opts, "label": opts[ex["cop"]],
             "ask": "Which answer is correct?", "descriptions": [""] * 4}]


def case_hold(ex, rng, feats):
    ends = [e[:140] for e in ex["endings"]]
    if len(set(ends)) < len(ends):
        return []
    return [{"task": "CHOICE", "source": ex["context"][-600:], "options": ends, "label": ends[ex["label"]],
             "ask": "Which holding completes the citation?", "descriptions": [""] * len(ends)}]


def nli(p_key, h_key, entail=0, contra=2):
    def f(ex, rng, feats):
        if ex["label"] not in (entail, contra):
            return []
        return [{"task": "NOUL", "source": ex[p_key], "options": [ex[h_key]], "label": ex["label"] == entail}]
    return f


def binary(text_key, label_key, statement, positive=1):
    def f(ex, rng, feats):
        t, y = ex.get(text_key), ex.get(label_key)
        if not t or y is None:
            return []
        if isinstance(y, str):
            y = y.strip().lower() in ("1", "true", "jailbreak", "spam", "toxic", "hate", "unsafe")
        return [{"task": "NOUL", "source": t, "options": [statement], "label": bool(y == positive if not isinstance(y, bool) else y)}]
    return f


def tupy(ex, rng, feats):
    """TuPyE binary: text + a hate flag column (name varies across versions)."""
    t = ex.get("text") or ex.get("tweet")
    y = next((ex[k] for k in ("hate", "is_hate", "label", "hateful", "aggressive") if k in ex and ex[k] is not None), None)
    if not t or y is None:
        return []
    return [{"task": "NOUL", "source": t, "options": ["Este texto contém discurso de ódio."], "label": bool(int(y))}]


def sentiment_score(text_key, label_key, rubric):
    def f(ex, rng, feats):
        return [{"task": "SCORE", "source": ex[text_key], "options": rubric, "label": int(ex[label_key]),
                 "ask": "What is the sentiment?", "descriptions": [""] * len(rubric)}]
    return f


def go_emotions(ex, rng, feats):
    names = feats["labels"].feature.names
    gold = [names[i] for i in ex["labels"] if names[i] != "neutral"]
    if not gold:
        return []
    opts = list(dict.fromkeys(gold + rng.sample([n for n in names if n not in gold and n != "neutral"], 6 - min(len(gold), 5))))
    rng.shuffle(opts)
    return [{"task": "SET", "source": ex["text"], "options": opts, "label": gold, "ask": "Which emotions are expressed?"}]


def aegis(ex, rng, feats):
    from .domains import SAFETY_DESC, SAFETY_LABELS
    text = ex.get("prompt")
    if not text or text == "REDACTED":
        return []
    if (ex.get("prompt_label") or "").lower() == "safe":
        lab = "safe"
    else:
        cats = (ex.get("violated_categories") or "").lower()
        lab = None
        for key, target in SAFETY_MAP:
            if key in cats:
                if target is None:          # content involving minors is never used as training text
                    return []
                lab = lab or target
        if lab is None:
            return []
    return [{"task": "CHOICE", "source": text[:2000], "options": SAFETY_LABELS, "label": lab,
             "ask": "Which safety category fits this text?", "descriptions": [SAFETY_DESC[x] for x in SAFETY_LABELS]}]


def manipulation(text_key, label_key, positive):
    def f(ex, rng, feats):
        from .domains import SAFETY_DESC, SAFETY_LABELS
        t = ex.get(text_key)
        if not t:
            return []
        lab = "manipulation" if str(ex.get(label_key)).lower() == str(positive).lower() else "safe"
        return [{"task": "CHOICE", "source": t[:2000], "options": SAFETY_LABELS, "label": lab,
                 "ask": "Which safety category fits this text?", "descriptions": [SAFETY_DESC[x] for x in SAFETY_LABELS]}]
    return f


# id, hf, config, train split, test split, licence, commercial, area, targets(base/specialist), mapper, lang
CATALOG: List[dict] = [
    # ---- routing / intents (support, assistants, agents)
    dict(id="banking77", hf="mteb/banking77", train="train", test="test", license="MIT (orig. CC BY 4.0)", commercial=True,
         area="support", to=["base", "support"], lang="en",
         map=choice_labels("text", None, "Which intent does this message express?", label_text_key="label_text")),
    dict(id="clinc_oos", hf="clinc/clinc_oos", config="plus", train="train", test="test", license="CC BY 3.0", commercial=True,
         area="support", to=["base", "support"], lang="en",
         map=choice_labels("text", "intent", "Which intent does this message express?", oos="oos")),
    *[dict(id=f"massive_{l}", hf="mteb/amazon_massive_intent", config=l, train="train", test="test", license="Apache-2.0 (orig. CC BY 4.0)",
           commercial=True, area="support", to=["base", "support"], lang=l,
           map=choice_labels("text", None, "Which intent does this message express?", label_text_key="label_text"))
      for l in ("pt", "en", "es", "fr", "de")],
    # ---- topics / documents (warehouse classification)
    dict(id="dbpedia14", hf="fancyzhx/dbpedia_14", train="train", test="test", license="CC BY-SA 3.0", commercial=True,
         area="knowledge", to=["base", "knowledge"], lang="en", map=choice_labels("content", "label", "What kind of entity is described?")),
    dict(id="ag_news", hf="fancyzhx/ag_news", train="train", test="test", license="unknown (AG corpus: non-commercial)", commercial=False,
         area="news", to=["base"], lang="en", map=choice_labels("text", "label", "What is the topic of this news?")),
    dict(id="fin_topic", hf="zeroshot/twitter-financial-news-topic", train="train", test="validation", license="MIT", commercial=True,
         area="finance", to=["base", "finance"], lang="en", map=choice_labels("text", "label", "What is the topic of this finance post?", names=FIN_TOPICS)),
    dict(id="ledgar", hf="coastalcph/lex_glue", config="ledgar", train="train", test="test", license="CC BY 4.0", commercial=True,
         area="legal", to=["base", "legal"], lang="en", map=choice_labels("text", "label", "Which type of contract provision is this?")),
    dict(id="case_hold", hf="coastalcph/lex_glue", config="case_hold", train="train", test="test", license="CC BY 4.0", commercial=True,
         area="legal", to=["legal"], lang="en", map=case_hold),
    # ---- knowledge / reasoning multiple choice
    dict(id="commonsense_qa", hf="tau/commonsense_qa", train="train", test="validation", license="MIT", commercial=True,
         area="knowledge", to=["base", "knowledge"], lang="en", map=mc_question("question", "choices", "answerKey")),
    dict(id="arc_easy", hf="allenai/ai2_arc", config="ARC-Easy", train="train", test="test", license="CC BY-SA 4.0", commercial=True,
         area="knowledge", to=["base", "knowledge"], lang="en", map=mc_question("question", "choices", "answerKey")),
    dict(id="arc_challenge", hf="allenai/ai2_arc", config="ARC-Challenge", train="train", test="test", license="CC BY-SA 4.0", commercial=True,
         area="knowledge", to=["knowledge"], lang="en", map=mc_question("question", "choices", "answerKey")),
    dict(id="medmcqa", hf="openlifescienceai/medmcqa", train="train", test="validation", license="Apache-2.0", commercial=True,
         area="medical", to=["base", "medical"], lang="en", map=medmcqa),
    # ---- inference / truth (NOUL)
    dict(id="snli", hf="stanfordnlp/snli", train="train", test="test", license="CC BY-SA 4.0", commercial=True,
         area="reasoning", to=["base"], lang="en", map=nli("premise", "hypothesis")),
    dict(id="mnli_pt", hf="MoritzLaurer/multilingual-NLI-26lang-2mil7", train="pt_mnli", test=None, license="unstated (MNLI-derived)",
         commercial=False, area="reasoning", to=["base"], lang="pt", map=nli("premise", "hypothesis", 0, 2)),
    # ---- sentiment / emotion (SCORE, SET)
    dict(id="go_emotions", hf="google-research-datasets/go_emotions", config="simplified", train="train", test="test",
         license="Apache-2.0", commercial=True, area="emotion", to=["base"], lang="en", map=go_emotions),
    *[dict(id=f"tweet_sent_{l}", hf="cardiffnlp/tweet_sentiment_multilingual", config=l, train="train", test="test",
           license="unstated", commercial=False, area="sentiment", to=["base"], lang=l[:2],
           map=sentiment_score("text", "label", ["negative", "neutral", "positive"])) for l in ("portuguese", "spanish", "english")],
    # ---- messaging hygiene / moderation
    dict(id="sms_spam", hf="ucirvine/sms_spam", train="train", test=None, license="CC BY 4.0 (UCI)", commercial=True,
         area="moderation", to=["base"], lang="en", map=binary("sms", "label", "This message is spam.")),
    *[dict(id=f"toxicity_{l}", hf="textdetox/multilingual_toxicity_dataset", train=l, test=None, license="OpenRAIL++", commercial=True,
           area="moderation", to=["base"], lang=l, map=binary("text", "toxic", "This text is toxic or offensive."))
      for l in ("en", "es", "fr", "de")],
    dict(id="tupye_pt", hf="Silly-Machine/TuPyE-Dataset", config="binary", train="train", test="test", license="CC BY 4.0",
         commercial=True, area="moderation", to=["base"], lang="pt", map=tupy),
    # ---- safety specialist (labelled categories)
    dict(id="aegis2", hf="nvidia/Aegis-AI-Content-Safety-Dataset-2.0", train="train", test="test", license="CC BY 4.0",
         commercial=True, area="safety", to=["safety"], lang="en", map=aegis),
    dict(id="jailbreak", hf="jackhhao/jailbreak-classification", train="train", test="test", license="Apache-2.0", commercial=True,
         area="safety", to=["safety"], lang="en", map=manipulation("prompt", "type", "jailbreak")),
    dict(id="prompt_injections", hf="deepset/prompt-injections", train="train", test="test", license="Apache-2.0", commercial=True,
         area="safety", to=["safety"], lang="multi", map=manipulation("text", "label", 1)),
]


def _load(src: dict, split: str):
    from datasets import load_dataset
    args = (src["hf"], src["config"]) if src.get("config") else (src["hf"],)
    return load_dataset(*args, split=split)


def build(out_dir: str, base_n: int = 900, spec_n: int = 3000, test_n: int = 150, allow_nc: bool = False, seed: int = 0,
          with_v1: bool = True) -> dict:
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    buckets: Dict[str, List[dict]] = defaultdict(list)
    tests: List[dict] = []
    report = {}
    for src in CATALOG:
        if not src["commercial"] and not allow_nc:
            report[src["id"]] = "skipped (non-commercial licence; --allow-nc for research)"
            continue
        try:
            ds = _load(src, src["train"]).shuffle(seed=seed)
            if hasattr(src["map"], "prescan"):
                src["map"].prescan(ds)
            need = max(base_n if "base" in src["to"] else 0, spec_n if len(src["to"]) > ("base" in src["to"]) else 0)
            feats = ds.features
            recs = []
            for ex in ds.select(range(min(len(ds), int(need * 1.6) + 50))):
                for r in src["map"](ex, rng, feats):
                    r.setdefault("descriptions", [""] * len(r.get("options", [])))
                    r["meta"] = {"lang": src["lang"], "provenance": src["id"], "license": src["license"],
                                 "commercial": src["commercial"], "area": src["area"]}
                    r["split"] = "train"
                    recs.append(r)
                if len(recs) >= need:
                    break
            for tgt in src["to"]:
                n = base_n if tgt == "base" else spec_n
                buckets[tgt] += recs[:n]
            if src.get("test"):
                tds = _load(src, src["test"]).shuffle(seed=seed + 1)
                got = []
                for ex in tds.select(range(min(len(tds), test_n * 2))):
                    got += src["map"](ex, rng, tds.features)
                    if len(got) >= test_n:
                        break
                for r in got[:test_n]:
                    r.setdefault("descriptions", [""] * len(r.get("options", [])))
                    r["meta"] = {"lang": src["lang"], "provenance": src["id"], "area": src["area"]}
                    r["split"] = "test"
                tests += got[:test_n]
            report[src["id"]] = {"train": len(recs), "to": src["to"], "license": src["license"]}
            print(f"{src['id']:<20} {len(recs):>6} records  → {', '.join(src['to'])}", flush=True)
        except Exception as e:
            report[src["id"]] = f"FAILED: {type(e).__name__}: {str(e)[:160]}"
            print(f"WARNING {src['id']} failed: {type(e).__name__}: {str(e)[:160]}", flush=True)
    # Jev-style agent patterns (built-in, MIT) + safe hard negatives for the safety specialist
    from .domains import agent, safety
    buckets["base"] += [r for r in agent(seed) if r["split"] == "train"]
    buckets["safety"] += [r for r in safety(seed) if r["split"] == "train"]
    tests += [dict(r, split="test") for r in agent(seed) if r["split"] == "validation"]
    if with_v1:                                       # SQuAD v2 / WikiANN / PAWS-X from v1 (already licence-checked)
        buckets["base"] += list(acquire_v1(base_n * 3, allow_nc, seed))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    bench = os.path.join(here, "benchmarks", "real", "djorge_real_v1.json")
    stats = {}
    test_keys = {(r["source"].strip().casefold()) for r in tests}
    for name, recs in buckets.items():
        v = [r for r in recs if valid(r) and r["source"].strip().casefold() not in test_keys]   # broad test never trained
        c = decontaminate(dedup(v), bench)
        rng.shuffle(c)
        for i, r in enumerate(c):
            r["id"] = f"{name}-{r['meta'].get('provenance', 'x')}-{i}"
            if name != "base":                         # specialists: deterministic 85/15 train/validation
                import hashlib
                r["split"] = "validation" if int(hashlib.sha1(r["source"].encode()).hexdigest(), 16) % 100 < 15 else "train"
        fn = "public_v2.jsonl" if name == "base" else f"{name}.jsonl"
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            for r in c:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        stats[fn] = {"records": len(c), "by_area": dict(Counter(r["meta"].get("area", r["meta"].get("domain", "?")) for r in c))}
    with open(os.path.join(out_dir, "broad_test.jsonl"), "w", encoding="utf-8") as f:
        for r in tests:
            if valid(r) or r["task"] in ("NOUL", "SET"):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["broad_test.jsonl"] = {"records": len(tests), "by_source": dict(Counter(r["meta"].get("provenance", r["meta"].get("pattern")) for r in tests))}
    json.dump({"stats": stats, "sources": report, "catalog": [{k: v for k, v in s.items() if k != "map"} for s in CATALOG]},
              open(os.path.join(out_dir, "public_v2.manifest.json"), "w"), indent=1, ensure_ascii=False)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--base", type=int, default=900, help="records per source in the base mix")
    ap.add_argument("--spec", type=int, default=3000, help="records per source in specialist files")
    ap.add_argument("--test", type=int, default=150, help="held-out test records per source (broad_eval)")
    ap.add_argument("--allow-nc", action="store_true")
    a = ap.parse_args()
    print(json.dumps(build(a.out_dir, a.base, a.spec, a.test, a.allow_nc), indent=1, ensure_ascii=False))
