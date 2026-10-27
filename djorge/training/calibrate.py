"""Fit calibration on the `calibration` split only, then write it into the artifact.

Span probabilities: binary temperature per mode, fitted on decoded candidate
spans labelled correct iff they exactly match a gold span (char offsets).
Decisions: temperature per (decision, option-count bucket).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import numpy as np

from ..runtime.calibration import CalibrationTable, IsotonicCalibrator, TemperatureScaler, ece, sigmoid, threshold_for_accuracy
from ..runtime.semantic import SemanticEngine, SemReq


def req_of(r, thr=0.5) -> SemReq:
    if r["task"] == "answer":
        return SemReq("answer", r["source"], target=r["ask"], threshold=thr)
    if r["task"] == "resolve":
        return SemReq("resolve", r["source"], mode=r["mode"], target=r["target"], definition=r.get("definition"),
                      threshold=thr)
    return SemReq(r["task"], r["source"], options=r["options"], descriptions=r.get("descriptions") or (),
                  ask=r.get("ask", ""))


def collect(engine: SemanticEngine, recs: List[dict]):
    """Raw (uncalibrated) outputs: span candidates and decision logits."""
    saved = engine.cal
    engine.cal = CalibrationTable()            # identity -> raw
    outs = engine.run([req_of(r) for r in recs])
    engine.cal = saved
    spans: Dict[str, list] = {}
    dec: Dict[tuple, list] = {}
    for r, o in zip(recs, outs):
        if r["task"] in ("resolve", "answer"):
            gold = {tuple(s) for s in r["spans"]}
            for m in o.all_scored():
                spans.setdefault(r.get("mode") or "ANSWER", []).append((m.raw_score, int((m.start, m.end) in gold)))
        elif r["task"] == "CHOICE":
            z = np.log(np.maximum([o["raw_distribution"][x] for x in r["options"]], 1e-9))
            dec.setdefault(("CHOICE", len(z)), []).append((z, r["options"].index(r["label"])))
        elif r["task"] == "SCORE":
            z = np.log(np.maximum([o["raw_distribution"][x] for x in r["options"]], 1e-9))
            dec.setdefault(("SCORE", len(z)), []).append((z, r["label"]))
        elif r["task"] == "NOUL":
            dec.setdefault(("NOUL", 2), []).append((o["raw_score"], int(bool(r["label"]))))
    return spans, dec


MIN_ERRORS = 5


def fit(engine: SemanticEngine, recs: List[dict]) -> CalibrationTable:
    cal = [r for r in recs if r["split"] == "calibration"]
    spans, dec = collect(engine, cal)
    table = CalibrationTable()
    report = {}
    for mode, xs in spans.items():
        if len(xs) >= 20 and MIN_ERRORS <= sum(y_ for _, y_ in xs) <= len(xs) - MIN_ERRORS:
            z, y = [a for a, _ in xs], [b for _, b in xs]
            ts = TemperatureScaler().fit_binary(z, y)
            table.set(mode, ts)
            report[mode] = {"n": len(xs), "t": round(ts.t, 4),
                            "ece_raw": round(ece([sigmoid(v) for v in z], y), 4),
                            "ece_cal": round(ece([ts.transform_binary(v) for v in z], y), 4)}
    for (kind, k), xs in dec.items():
        if len(xs) < 15:
            continue
        if kind == "NOUL":
            errors = sum(int((a > 0) != bool(b)) for a, b in xs)
        else:
            errors = sum(int(int(np.argmax(a)) != b) for a, b in xs)
        if errors < MIN_ERRORS:      # no evidence about over/under-confidence: keep identity
            report[f"{kind}/{k}"] = {"n": len(xs), "errors": errors, "t": None, "note": "identity (too few errors to fit)"}
            continue
        if kind == "NOUL":
            ts = TemperatureScaler().fit_binary([a for a, _ in xs], [b for _, b in xs])
            table.set("NOUL", ts)
        else:
            ts = TemperatureScaler().fit([a for a, _ in xs], [b for _, b in xs])
            table.set(kind, ts, k)
        report[f"{kind}/{k}"] = {"n": len(xs), "t": round(ts.t, 4)}
    # ---- certainty table: user target accuracy -> minimum calibrated probability (fails closed)
    certainty = {}
    targets = (0.8, 0.9, 0.95, 0.97, 0.99)
    for mode, xs in spans.items():
        c = table.get(mode, 2)
        conf = [c.transform_binary(z) for z, _ in xs]
        certainty[mode] = {str(t): threshold_for_accuracy(conf, [y for _, y in xs], t, min_support=20) for t in targets}
    for (kind, k), xs in dec.items():
        c = table.get(kind, k)
        if kind == "NOUL":
            conf = [max(c.transform_binary(a), 1 - c.transform_binary(a)) for a, _ in xs]
            ok = [int((c.transform_binary(a) >= 0.5) == bool(b)) for a, b in xs]
        else:
            ps = [c.transform_logits(a) for a, _ in xs]
            conf = [float(max(p)) for p in ps]
            ok = [int(int(np.argmax(p)) == b) for p, (_, b) in zip(ps, xs)]
        certainty.setdefault(kind, {})
        for t in targets:
            v = threshold_for_accuracy(conf, ok, t, min_support=15)
            prev = certainty[kind].get(str(t), -1)
            certainty[kind][str(t)] = None if v is None else max(v, prev if prev is not None else 0)
    report["certainty"] = certainty
    return table, report


def main(path: str):
    eng = SemanticEngine.from_artifact(path, device="cpu")
    recs = [json.loads(l) for l in open(os.path.join(path, "dataset.jsonl"))]
    table, report = fit(eng, recs)
    open(os.path.join(path, "calibration", "calibration.json"), "w").write(table.to_json())
    json.dump(report.get("certainty", {}), open(os.path.join(path, "calibration", "certainty.json"), "w"), indent=1)
    meta_p = os.path.join(path, "metadata", "djorge.json")
    meta = json.load(open(meta_p))
    meta.setdefault("versions", {})["calibration"] = f"cal-{abs(hash(table.to_json())) % 10**8:08d}"
    meta["calibration_report"] = report
    json.dump(meta, open(meta_p, "w"), indent=2)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
