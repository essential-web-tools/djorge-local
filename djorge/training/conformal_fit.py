"""Fit conformal prediction sets (and stratified temperature calibration) for an Origo artifact, on the GPU.

    python -m djorge.training.conformal_fit --artifact A [--alpha 0.05] [--per-task 1500] [--recalibrate]

1) --recalibrate: temperature calibration (djorge.training.calibrate.fit) on a STRATIFIED sample of the
   calibration split (quota per task/kind), on the GPU - the stock calibrate runs over every record on the CPU.
2) conformal: half of the stratified CHOICE sample fits qhat per option-count bucket, the other half measures the
   empirical coverage (must be close to 1 - alpha). Writes calibration/conformal.json; the engine attaches
   `prediction_set` to every CHOICE result automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import random
from collections import defaultdict

import numpy as np


def stratified(recs, per, seed=0):
    g = defaultdict(list)
    for r in recs:
        g[(r["task"], r.get("meta", {}).get("kind") or r.get("meta", {}).get("provenance", "?"))].append(r)
    out = []
    for k, rs in g.items():
        random.Random(seed).shuffle(rs); out += rs[:per]
    random.Random(seed).shuffle(out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--per-task", type=int, default=1500)
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--data", default=None, help="held-out labelled jsonl to calibrate on (default: the artifact's "
                    "dataset.jsonl calibration split)")
    a = ap.parse_args(argv)
    from ..runtime.semantic import SemanticEngine, SemReq
    from ..runtime.calibration import bucket
    from ..runtime.refcal import fit_conformal
    from . import calibrate as CAL

    if a.data:
        recs = [json.loads(l) for l in open(a.data, encoding="utf-8") if l.strip()]
        recs = [r for r in recs if r.get("label") is not None and r.get("known") is None]
    else:
        recs = [json.loads(l) for l in open(os.path.join(a.artifact, "dataset.jsonl"), encoding="utf-8")
                if '"calibration"' in l]
        recs = [r for r in recs if r.get("split") == "calibration" and r.get("label") is not None]
    rep = {}
    if a.recalibrate:
        eng = SemanticEngine.from_artifact(a.artifact, device=a.device)
        sample = stratified([r for r in recs if r["task"] in ("CHOICE", "SCORE", "NOUL", "SET")], a.per_task)
        table, report = CAL.fit(eng, sample)
        os.makedirs(os.path.join(a.artifact, "calibration"), exist_ok=True)
        open(os.path.join(a.artifact, "calibration", "calibration.json"), "w").write(table.to_json())
        json.dump(report.get("certainty", {}), open(os.path.join(a.artifact, "calibration", "certainty.json"), "w"), indent=1)
        rep["recalibrated_on"] = len(sample)
        del eng
    eng = SemanticEngine.from_artifact(a.artifact, device=a.device)      # reload with the new calibration
    ch = stratified([r for r in recs if r["task"] == "CHOICE" and r["label"] in r.get("options", [])], a.per_task, 1)
    half = len(ch) // 2
    fit_part, test_part = ch[:half], ch[half:]

    def p_true(part):
        outs = eng.run([SemReq("CHOICE", r["source"], options=r["options"], descriptions=r.get("descriptions") or (),
                               ask=r.get("ask", "")) for r in part])
        res = defaultdict(list)
        for r, o in zip(part, outs):
            res[f"CHOICE/{bucket(len(r['options']))}"].append((float(o["distribution"][r["label"]]), o["distribution"]))
        return res
    pf = p_true(fit_part)
    qhat = fit_conformal({k: [p for p, _ in v] for k, v in pf.items()}, a.alpha)
    pt = p_true(test_part)
    cov = {}
    for k, v in pt.items():
        if k not in qhat:
            continue
        hit = [p >= 1 - qhat[k] for p, _ in v]
        size = [sum(1 for x in d.values() if x >= 1 - qhat[k]) for _, d in v]
        cov[k] = {"n": len(v), "coverage": round(float(np.mean(hit)), 4), "mean_set_size": round(float(np.mean(size)), 3)}
    table = {"alpha": a.alpha, "qhat": qhat, "held_out": cov}
    json.dump(table, open(os.path.join(a.artifact, "calibration", "conformal.json"), "w"), indent=1)
    rep.update(conformal=table)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
