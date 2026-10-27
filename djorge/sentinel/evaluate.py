"""Acceptance metrics for the Sentinel: accuracy, abstention, high-confidence errors and calibration (ECE) per kind,
plus danger-level accuracy (exact and within one level). Metrics only.

    python -m djorge.sentinel.evaluate --artifact PATH --test sentinel_text_test.jsonl [--linear-int8] [--embed-int8]
           [--prune-vocab] [--out result.json]
The same test with and without the int8 / pruning options tells whether a compression loses anything.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from ..runtime.semantic import SemReq, SemanticEngine


def _ece(pairs, bins=10):
    """pairs: [(confidence, correct)] -> expected calibration error."""
    if not pairs:
        return 0.0
    tot = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(c, ok) for c, ok in pairs if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if sel:
            tot += len(sel) / len(pairs) * abs(sum(c for c, _ in sel) / len(sel) - sum(ok for _, ok in sel) / len(sel))
    return round(tot, 4)


def _danger(eng, records):
    d = defaultdict(lambda: {"n": 0, "exact": 0, "within1": 0})
    for r in records:
        if r.get("task") != "SCORE" or r.get("label") is None:
            continue
        o = eng.run([SemReq("SCORE", r["source"], options=r["options"], descriptions=r.get("descriptions", ()),
                            ask=r.get("ask", ""))])[0]
        a = int(o.get("answer", 0))
        for key in (r.get("meta", {}).get("kind") or r.get("meta", {}).get("channel", "?"), "TOTAL"):
            s = d[key]
            s["n"] += 1; s["exact"] += a == int(r["label"]); s["within1"] += abs(a - int(r["label"])) <= 1
    return {k: {"n": s["n"], "exact": round(s["exact"] / s["n"], 4), "within1": round(s["within1"] / s["n"], 4)}
            for k, s in d.items() if s["n"]}


def evaluate(eng, records, high_conf=0.9):
    g = defaultdict(lambda: {"n": 0, "ok": 0, "abstain": 0, "high_conf_errors": 0, "pairs": []})
    for r in records:
        if r.get("task") != "CHOICE" or r.get("label") is None:
            continue
        o = eng.run([SemReq("CHOICE", r["source"], options=r["options"], descriptions=r["descriptions"], ask=r["ask"])])[0]
        ans = o.get("answer")
        if isinstance(ans, int) and 0 <= ans < len(r["options"]):
            ans = r["options"][ans]
        conf = float(o.get("confidence", o.get("probability", 0)) or 0)
        for key in (r.get("meta", {}).get("kind") or r.get("meta", {}).get("channel", "?"), "TOTAL"):
            s = g[key]
            s["n"] += 1
            if o.get("abstain") or ans is None:
                s["abstain"] += 1
                continue
            ok = ans == r["label"]
            s["ok"] += ok
            s["pairs"].append((conf, int(ok)))
            if not ok and conf >= high_conf:
                s["high_conf_errors"] += 1
    out = {}
    for k, s in g.items():
        answered = s["n"] - s["abstain"]
        out[k] = {"n": s["n"], "accuracy": round(s["ok"] / s["n"], 4) if s["n"] else 0,
                  "coverage": round(answered / s["n"], 4) if s["n"] else 0,
                  "accuracy_when_answering": round(s["ok"] / answered, 4) if answered else 0,
                  "high_conf_errors": s["high_conf_errors"], "ece": _ece(s["pairs"])}
    dg = _danger(eng, records)
    if dg:
        out["danger"] = dg
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--max", type=int, default=1500)
    ap.add_argument("--out", default=None)
    ap.add_argument("--linear-int8", action="store_true", help="int8 per-channel weights in every backbone Linear")
    ap.add_argument("--embed-int8", action="store_true", help="int8 token table")
    ap.add_argument("--prune-vocab", action="store_true", help="use metadata/vocab_keep.json (tools/prune_vocab.py)")
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args(argv)
    if a.threads:
        import torch
        torch.set_num_threads(a.threads)
    import time
    t0 = time.time()
    eng = SemanticEngine.from_artifact(a.artifact, device="cpu", linear_int8=a.linear_int8, embed_int8=a.embed_int8,
                                       prune_vocab=a.prune_vocab)
    load_s = time.time() - t0
    recs = [json.loads(l) for l in open(a.test, encoding="utf-8") if l.strip()][: a.max]
    t0 = time.time()
    res = evaluate(eng, recs)
    res["config"] = {"linear_int8": a.linear_int8, "embed_int8": a.embed_int8, "prune_vocab": a.prune_vocab,
                     "load_s": round(load_s, 1), "eval_s": round(time.time() - t0, 1),
                     "ms_per_request": round((time.time() - t0) * 1000 / max(1, len(recs)), 1),
                     "compression": eng.meta.get("compression"),
                     "vocab_pruning": eng.meta.get("vocab_pruning")}
    try:
        import psutil, os
        res["config"]["rss_mb"] = round(psutil.Process(os.getpid()).memory_info().rss / 1e6)
    except Exception:
        pass
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.out:
        json.dump(res, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
