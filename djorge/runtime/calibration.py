"""Calibration and probability-quality metrics.

Design rule: a backend emits RAW scores (logits / log-scores). Calibration is a
separate, fitted, serializable transform keyed by (operator, option-count
bucket). The public API reports both raw and calibrated values and defaults to
the calibrated one.

Transforms: TemperatureScaler (Guo et al. 2017), IsotonicCalibrator (PAV),
BetaCalibrator (Kull et al. 2017).
Metrics: NLL, Brier, ECE (top-label), reliability bins, risk-coverage, AURC,
threshold-for-target-accuracy (fails closed).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

EPS = 1e-12


def softmax(z: Sequence[float], t: float = 1.0) -> np.ndarray:
    z = np.asarray(z, dtype=np.float64) / max(t, 1e-6)
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def bucket(n_options: int) -> str:
    """Option-count bucket: temperature does not transfer across option counts."""
    for hi, name in ((2, "2"), (3, "3"), (4, "4"), (8, "5-8"), (16, "9-16"), (64, "17-64")):
        if n_options <= hi:
            return name
    return "65+"


def _golden(f, lo: float, hi: float, iters: int = 60) -> float:
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    return (a + b) / 2


class Calibrator:
    kind = "identity"

    def transform_logits(self, logits: Sequence[float]) -> np.ndarray:
        return softmax(logits)

    def transform_binary(self, z: float) -> float:
        return sigmoid(z)

    def to_dict(self) -> dict:
        return {"kind": self.kind}


class TemperatureScaler(Calibrator):
    kind = "temperature"

    def __init__(self, t: float = 1.0):
        self.t = float(t)

    def fit(self, logits: List[Sequence[float]], labels: List[int]) -> "TemperatureScaler":
        """Minimize NLL over log T in [log 0.05, log 20]; variable option counts OK."""
        L = [np.asarray(z, dtype=np.float64) for z in logits]

        def nll_(logt):
            t = math.exp(logt)
            tot = 0.0
            for z, y in zip(L, labels):
                zz = z / t
                m = zz.max()
                tot -= zz[y] - (m + math.log(np.exp(zz - m).sum()))
            return tot / max(len(L), 1)

        self.t = math.exp(_golden(nll_, math.log(0.05), math.log(20.0)))
        return self

    def fit_binary(self, z: Sequence[float], y: Sequence[int]) -> "TemperatureScaler":
        return self.fit([[0.0, float(v)] for v in z], [int(v) for v in y])

    def transform_logits(self, logits):
        return softmax(logits, self.t)

    def transform_binary(self, z):
        return sigmoid(z / self.t)

    def to_dict(self):
        return {"kind": self.kind, "t": self.t}


class IsotonicCalibrator(Calibrator):
    """Pool-adjacent-violators on (p_raw -> empirical frequency)."""
    kind = "isotonic"

    def __init__(self, xs=None, ys=None):
        self.xs, self.ys = list(xs or []), list(ys or [])

    def fit_binary(self, z: Sequence[float], y: Sequence[int]) -> "IsotonicCalibrator":
        p = np.array([sigmoid(v) for v in z])
        order = np.argsort(p)
        p, yy = p[order], np.asarray(y, dtype=np.float64)[order]
        out: List[list] = []                      # [sum_y, n, lo_p, hi_p]
        for i in range(len(p)):
            out.append([float(yy[i]), 1.0, float(p[i]), float(p[i])])
            while len(out) > 1 and out[-2][0] / out[-2][1] > out[-1][0] / out[-1][1]:
                s2, n2, _, hi2 = out.pop()
                s, n, lo, _ = out[-1]
                out[-1] = [s + s2, n + n2, lo, hi2]
        self.xs = [(b[2] + b[3]) / 2 for b in out]
        self.ys = [min(max(b[0] / b[1], 1e-4), 1 - 1e-4) for b in out]
        return self

    def transform_binary(self, z):
        if not self.xs:
            return sigmoid(z)
        return float(np.interp(sigmoid(z), self.xs, self.ys))

    def to_dict(self):
        return {"kind": self.kind, "xs": self.xs, "ys": self.ys}


class BetaCalibrator(Calibrator):
    """p' = sigmoid(a*ln p - b*ln(1-p) + c), fitted with Newton's method."""
    kind = "beta"

    def __init__(self, a=1.0, b=1.0, c=0.0):
        self.a, self.b, self.c = a, b, c

    def fit_binary(self, z, y, iters: int = 50, l2: float = 1e-3):
        p = np.clip(np.array([sigmoid(v) for v in z]), 1e-6, 1 - 1e-6)
        X = np.stack([np.log(p), -np.log(1 - p), np.ones_like(p)], 1)
        yy = np.asarray(y, dtype=np.float64)
        w = np.array([1.0, 1.0, 0.0])
        for _ in range(iters):
            q = 1 / (1 + np.exp(-X @ w))
            g = X.T @ (q - yy) + l2 * w
            H = (X * (q * (1 - q))[:, None]).T @ X + l2 * np.eye(3)
            step = np.linalg.solve(H, g)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        self.a, self.b, self.c = map(float, w)
        return self

    def transform_binary(self, z):
        p = min(max(sigmoid(z), 1e-6), 1 - 1e-6)
        return sigmoid(self.a * math.log(p) - self.b * math.log(1 - p) + self.c)

    def to_dict(self):
        return {"kind": self.kind, "a": self.a, "b": self.b, "c": self.c}


def calibrator_from_dict(d: dict) -> Calibrator:
    k = d.get("kind", "identity")
    if k == "temperature":
        return TemperatureScaler(d["t"])
    if k == "isotonic":
        return IsotonicCalibrator(d["xs"], d["ys"])
    if k == "beta":
        return BetaCalibrator(d["a"], d["b"], d["c"])
    return Calibrator()


@dataclass
class CalibrationTable:
    """(op, bucket) -> Calibrator, with (op, '*') and ('*', '*') fallbacks."""
    entries: Dict[str, Calibrator] = field(default_factory=dict)

    def get(self, op: str, n_options: int = 2) -> Calibrator:
        for key in (f"{op}/{bucket(n_options)}", f"{op}/*", "*/*"):
            if key in self.entries:
                return self.entries[key]
        return Calibrator()

    def set(self, op: str, cal: Calibrator, n_options: Optional[int] = None):
        self.entries[f"{op}/{bucket(n_options) if n_options else '*'}"] = cal

    def to_json(self) -> str:
        return json.dumps({k: v.to_dict() for k, v in self.entries.items()}, indent=2)

    @classmethod
    def from_json(cls, s: str) -> "CalibrationTable":
        return cls({k: calibrator_from_dict(v) for k, v in json.loads(s).items()})


# ----------------------------------------------------------------- metrics -- #

def nll(probs: List[Sequence[float]], labels: List[int]) -> float:
    return float(np.mean([-math.log(max(p[y], EPS)) for p, y in zip(probs, labels)]))


def brier(probs: List[Sequence[float]], labels: List[int]) -> float:
    """Multiclass Brier score (sum over classes), in [0, 2]."""
    tot = 0.0
    for p, y in zip(probs, labels):
        p = np.asarray(p, dtype=np.float64)
        oh = np.zeros_like(p)
        oh[y] = 1.0
        tot += float(((p - oh) ** 2).sum())
    return tot / max(len(probs), 1)


def reliability_bins(conf, correct, n_bins: int = 10) -> List[dict]:
    conf, correct = np.asarray(conf, float), np.asarray(correct, float)
    out = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        m = ((conf > lo) if i else (conf >= lo)) & (conf <= hi)
        if m.any():
            out.append({"lo": lo, "hi": hi, "n": int(m.sum()),
                        "confidence": float(conf[m].mean()), "accuracy": float(correct[m].mean())})
    return out


def ece(conf, correct, n_bins: int = 15) -> float:
    n = max(len(conf), 1)
    return float(sum(b["n"] / n * abs(b["accuracy"] - b["confidence"])
                     for b in reliability_bins(conf, correct, n_bins)))


def risk_coverage(conf, correct) -> List[Tuple[float, float, float]]:
    """(threshold, coverage, selective_accuracy), by descending confidence."""
    c = np.asarray(conf, float)
    order = np.argsort(-c, kind="stable")
    c, ok = c[order], np.asarray(correct, float)[order]
    cum = np.cumsum(ok)
    n = len(c)
    return [(float(c[i]), (i + 1) / n, float(cum[i] / (i + 1))) for i in range(n)]


def aurc(conf, correct) -> float:
    rc = risk_coverage(conf, correct)
    return float(np.mean([1 - acc for _, _, acc in rc])) if rc else 0.0


def threshold_for_accuracy(conf, correct, target: float, min_support: int = 20) -> Optional[float]:
    """Lowest cut with selective accuracy >= target and >= min_support accepted.
    Returns None (fail closed) when unreachable."""
    best = None
    for i, (t, cov, acc) in enumerate(risk_coverage(conf, correct)):
        if i + 1 >= min_support and acc >= target:
            best = t
    return best
