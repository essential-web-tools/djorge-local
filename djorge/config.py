"""Central configuration. Load order: defaults < JSON file (DJORGE_CONFIG) < env (DJORGE_*)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional


@dataclass
class Thresholds:
    accept: Dict[str, float] = field(default_factory=lambda: {"CLASS": 0.5, "ENTITY": 0.5, "ANSWER": 0.3})
    abstain: Dict[str, float] = field(default_factory=lambda: {"CLASS": 0.6, "ENTITY": 0.6, "ANSWER": 0.5, "DECISION": 0.0, "UNGROUNDED": 0.97,
                                                                   "CLASSIFY": 0.5})
    prune: float = 0.05            # candidate spans below this are dropped before counting


@dataclass
class Limits:
    max_query_chars: int = 8_000
    max_source_chars: int = 200_000
    max_statements: int = 64
    max_regex_chars: int = 256
    max_options: int = 255
    max_matches: int = 10_000


@dataclass
class DjorgeConfig:
    artifact: Optional[str] = None          # path to a trained DJORGE artifact directory
    device: str = "auto"                    # auto | cpu | cuda | cuda:0 | mps
    dtype: str = "auto"                     # auto | float32 | bfloat16 | float16
    max_seq_tokens: int = 512               # per window; longer sources are windowed
    window_stride: int = 384
    batch_size: int = 16
    option_permutations: int = 1            # >1 averages option-order bias (cost: k rows)
    thresholds: Thresholds = field(default_factory=Thresholds)
    limits: Limits = field(default_factory=Limits)
    context_dir: str = os.path.expanduser("~/.djorge/contexts")
    certainty: Optional[float] = None       # user target accuracy, e.g. 0.95 (None = fixed thresholds)
    log_raw_inputs: bool = False            # privacy: never log sources unless explicitly enabled
    api_key: Optional[str] = None
    use_artifact_thresholds: bool = True    # apply calibration/thresholds.json shipped with the artifact
    safety_mode: str = "auto"               # off | lexical | auto (learned check only when unsure) | strict
    safety_policy: Dict[str, str] = field(default_factory=dict)   # category -> allow | flag | block (overrides)
    max_inflight: int = 64                  # server: concurrent requests admitted before answering 503 BUSY
    cascade_artifact: Optional[str] = None  # stronger model consulted when this one is unsure (Flash -> Origo)
    cascade_below: float = 0.9              # ... i.e. when it abstains or probability < this

    @classmethod
    def load(cls, path: Optional[str] = None) -> "DjorgeConfig":
        cfg = cls()
        path = path or os.environ.get("DJORGE_CONFIG")
        if path and os.path.exists(path):
            data = json.load(open(path))
            th = data.pop("thresholds", None)
            li = data.pop("limits", None)
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            if th:
                cfg.thresholds = Thresholds(**{**asdict(cfg.thresholds), **th})
            if li:
                cfg.limits = Limits(**{**asdict(cfg.limits), **li})
        for k in ("artifact", "device", "dtype", "context_dir", "api_key", "safety_mode", "cascade_artifact"):
            v = os.environ.get("DJORGE_" + k.upper())
            if v:
                setattr(cfg, k, v)
        for k in ("max_seq_tokens", "batch_size", "max_inflight", "option_permutations"):
            v = os.environ.get("DJORGE_" + k.upper())
            if v:
                setattr(cfg, k, int(v))
        v = os.environ.get("DJORGE_CASCADE_BELOW")
        if v:
            cfg.cascade_below = float(v)
        return cfg

    def to_dict(self):
        d = asdict(self)
        d.pop("api_key", None)
        return d
