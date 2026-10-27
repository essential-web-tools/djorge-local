"""Hard-example mining between training rounds.

Scores every training record with the current model and rewrites the JSONL with a `weight` per record:
  wrong answer                      -> weight 3.0  (the next round focuses on these)
  right but unsure (p < 0.75)       -> weight 2.0
  right and sure                    -> weight 0.5  (kept, so nothing is forgotten, but cheaper)
Records without a label, calibration and test records are copied unchanged.

    python -m djorge.sentinel.mine --artifact S1 --data train.jsonl --out train_r2.jsonl [--max 40000]
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter

from ..runtime.semantic import SemReq, SemanticEngine


def _req(r):
    return SemReq(r["task"], r["source"], options=r.get("options", ()), descriptions=r.get("descriptions", ()),
                  ask=r.get("ask", ""))


def _correct(r, o):
    ans = o.get("answer")
    if r["task"] == "SCORE":
        return int(ans) == int(r["label"])
    if r["task"] == "NOUL":
        return bool(ans) == bool(r["label"])
    return ans == r["label"]


def mine(eng, recs, max_n=40000, batch=32, seed=0):
    cand = [i for i, r in enumerate(recs) if r.get("split") == "train" and r.get("label") is not None
            and r.get("known") is None and r["task"] in ("CHOICE", "SCORE", "NOUL")]
    random.Random(seed).shuffle(cand)
    cand = cand[:max_n]
    stats = Counter()
    for s in range(0, len(cand), batch):
        idx = cand[s:s + batch]
        outs = eng.run([_req(recs[i]) for i in idx])
        for i, o in zip(idx, outs):
            r = recs[i]
            ok = _correct(r, o)
            p = float(o.get("probability", 0) or 0)
            w = (4.0 if p >= 0.9 else 3.0) if not ok else (2.0 if p < 0.75 else 0.5)   # confident errors weigh most
            r["weight"] = round(float(r.get("weight", 1.0)) * w, 3)
            stats[("errado_confiante" if p >= 0.9 else "errado") if not ok else ("inseguro" if p < 0.75 else "fácil")] += 1
    return dict(stats)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=40000)
    ap.add_argument("--device", default="auto")
    a = ap.parse_args(argv)
    eng = SemanticEngine.from_artifact(a.artifact, device=a.device)
    recs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
    st = mine(eng, recs, a.max)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"mined": st, "records": len(recs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
