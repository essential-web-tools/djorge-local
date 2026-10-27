"""Plain-text training set of the Djorge Sentinel (texto, dominio, email, prompt).

Every record is an Origo record (CHOICE / SCORE) whose `source` is the plain text exactly as a developer would send it
to the API, and whose question/options come from presets.py, so training and serving see the same input.

Smart-data choices:
  * same question as the API most of the time (70%), paraphrased questions otherwise -> robust to wording;
  * hard negatives (legit bank notices, real brand domains, OTP messages) next to every threat type;
  * danger SCORE on every item -> the model learns "how bad", separately from "what kind";
  * a held-out test set built from a different seed and de-duplicated against train;
  * a ~7% calibration slice (split="calibration") for djorge.training.calibrate.

    python -m djorge.sentinel.text_data --out-dir DATA --per-source 4000 [--with-public]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from collections import Counter
from typing import List

from . import channels
from .presets import DANGER_ASK, DANGER_DESC, DANGER_LEVELS, KINDS

THREAT_TO_TEXTO = {"phishing": "phishing", "scam": "golpe", "spam": "spam", "safe": "seguro"}   # prompt_injection -> kind 'prompt'


def _band(kind, label):
    """Danger band (0..4) for a kind+label, taken straight from presets so it never drifts."""
    return KINDS[kind]["danger"].get(label, 0)


def _ask(kind, rng):
    k = KINDS[kind]
    return k["ask"] if rng.random() < 0.7 else rng.choice(k["asks"])


def choice(kind, text, label, rng, prov, weight=1.0):
    k = KINDS[kind]
    return {"task": "CHOICE", "source": text, "options": list(k["options"]), "descriptions": list(k["descriptions"]),
            "label": label, "ask": _ask(kind, rng), "weight": weight, "split": "train",
            "meta": {"area": "security", "kind": kind, "channel": kind, "provenance": prov}}


def danger(kind, text, band, prov, weight=1.0):
    return {"task": "SCORE", "source": text, "options": list(DANGER_LEVELS), "descriptions": list(DANGER_DESC),
            "label": int(band), "ask": DANGER_ASK, "weight": weight, "split": "train",
            "meta": {"area": "security", "kind": kind, "channel": kind, "provenance": prov}}


def _domains(rng, n):
    out = []
    for _ in range(n):
        dom, risky, typ = channels.gen_domain(rng)
        r = rng.random()
        text = dom if r < 0.4 else (("https://" if r < 0.75 else "http://") + dom +
                                    rng.choice(["", "/", "/login", "/acesso", "/app", "/verificar", "/promo"]))
        if risky is None:                                # shortener: destination unknown -> only "baixo" danger
            out.append(danger("dominio", text, 1, "gen_domain", 0.5))
            continue
        lab = "malicioso" if (risky and typ == "personificacao") else ("phishing" if risky else "legítimo")
        out += [choice("dominio", text, lab, rng, "gen_domain"), danger("dominio", text, _band("dominio", lab), "gen_domain")]
    return out


def _emails(rng, n):
    out = []
    for _ in range(n):
        frm, risky = channels.gen_email(rng)
        lab = "phishing" if risky else "legítimo"
        out += [choice("email", frm, lab, rng, "gen_email"), danger("email", frm, _band("email", lab), "gen_email")]
    return out


def _numbers(rng, n):
    """A phone number + its message is a message -> kind 'texto' (número isn't a separate kind in presets)."""
    out = []
    for _ in range(n):
        number, body, _intl, _known, tech = channels.gen_number_message(rng)
        text = f"{number}: {body}"
        lab = "golpe" if tech else "seguro"
        out += [choice("texto", text, lab, rng, "gen_number"), danger("texto", text, _band("texto", lab), "gen_number")]
    return out


def _messages(recs, rng):
    """scams_br / public message records -> preset CHOICE + danger SCORE. Prompt-injection text feeds kind 'prompt'."""
    out = []
    for r in recs:
        out.append(r)
        if r.get("task") != "CHOICE":
            continue
        th = r.get("meta", {}).get("threat")
        prov = r["meta"].get("provenance", "msg")
        if th in THREAT_TO_TEXTO:
            lab = THREAT_TO_TEXTO[th]
            out += [choice("texto", r["source"], lab, rng, prov), danger("texto", r["source"], _band("texto", lab), prov)]
        elif th == "prompt_injection":
            out += [choice("prompt", r["source"], "injeção de prompt", rng, prov),
                    danger("prompt", r["source"], _band("prompt", "injeção de prompt"), prov)]
    return out


def _gen_all(rng, per_source, held, with_public, out_dir, allow_nc, seed):
    from ..training import scams_br
    msgs = []
    for gen in (scams_br.generate, scams_br.hard_negatives, scams_br.spoofed_senders):
        msgs += gen(rng, per_source, held=held)
    if with_public and not held:
        try:
            from ..training import sentinel_data as SD
            SD.build(out_dir, per_source, 0, allow_nc, seed)
            p = os.path.join(out_dir, "sentinel_train.jsonl")
            msgs += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        except Exception as e:                      # public sources are optional
            print(f"WARNING public sources skipped: {type(e).__name__}: {str(e)[:160]}", flush=True)
    return _domains(rng, per_source) + _emails(rng, per_source) + _numbers(rng, per_source) + _messages(msgs, rng)


def _dedup(recs, seen=None):
    seen = set() if seen is None else seen
    out = []
    for r in recs:
        k = (r["task"], r.get("ask"), r["source"][:300])
        if k not in seen:
            seen.add(k); out.append(r)
    return out, seen


def build(out_dir: str, per_source: int = 4000, test_per_source: int = 300, with_public: bool = False,
          allow_nc: bool = False, seed: int = 0) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    test = _gen_all(random.Random(seed + 1000), test_per_source, True, False, out_dir, allow_nc, seed)
    for r in test:
        r["split"] = "test"
    test, _ = _dedup(test)
    test_src = {r["source"][:300] for r in test}
    train = [r for r in _gen_all(rng, per_source, False, with_public, out_dir, allow_nc, seed)
             if r["source"][:300] not in test_src]                # no test text ever appears in train
    train, _ = _dedup(train)
    rng.shuffle(train)
    for r in train:
        if r.get("label") is not None and \
                int(hashlib.sha1(r["source"][:300].encode("utf-8", "ignore")).hexdigest(), 16) % 100 < 7:
            r["split"] = "calibration"
    for fn, recs in (("sentinel_text_train.jsonl", train), ("sentinel_text_test.jsonl", test)):
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    m = {"train": len(train), "test": len(test), "with_public": with_public,
         "by_task": dict(Counter(r["task"] for r in train)),
         "by_kind": dict(Counter(r.get("meta", {}).get("kind", "outro") for r in train)),
         "by_split": dict(Counter(r["split"] for r in train))}
    json.dump(m, open(os.path.join(out_dir, "sentinel_text.manifest.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(m, ensure_ascii=False))
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-source", type=int, default=4000)
    ap.add_argument("--test", type=int, default=300)
    ap.add_argument("--with-public", action="store_true")
    ap.add_argument("--allow-nc", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    build(a.out_dir, a.per_source, a.test, a.with_public, a.allow_nc, a.seed)


if __name__ == "__main__":
    main()
