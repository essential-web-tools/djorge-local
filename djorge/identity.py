"""Model identity. Origo does not generate text, so it does not "say" who it is: its identity is DATA that travels
with the artifact (metadata + MODEL_CARD.md + NOTICE) and is returned by GET /model and GET /about.
Facts are computed from the loaded weights, never typed by hand."""
from __future__ import annotations

import re

from .version import DJORGE_VERSION, DSL_VERSION

FAMILY = "Djorge"
FAMILY_EXPANSION = "Decision & Judgment Oriented Reasoning on Grounded Evidence"
TIER_CODES = {"Origo": "OR", "Sentinel": "SE", "Mosaic": "MS", "Ascend": "AS", "Monet": "MN", "Gauss": "GS"}


def catalog_id(tier: str = "Origo", generation: int = 1, build: str = "", layers: int = None, variant: str = "") -> str:
    """DJ-<tier><generation>.<build>-<layers>L, e.g. DJ-OR1.6-21L = Djorge Origo, generation 1, training build E6,
    21 decoder layers. Build is the experiment number the weights came from (E6b -> 6)."""
    m = re.search(r"(\d+)", build or "")
    b = m.group(1) if m else "0"
    return (f"DJ-{TIER_CODES.get(tier, tier[:2].upper())}{generation}.{b}" + (f"-{layers}L" if layers else "")
            + ("-F" if variant == "flash" else ""))


DEFAULT_IDENTITY = {
    "name": "Djorge Origo",
    "family": FAMILY,
    "tier": "Origo",
    "family_expansion": FAMILY_EXPANSION,
    "acronym": {"D": "Decision", "J": "Judgment", "O": "Oriented", "R": "Reasoning", "G": "Grounded", "E": "Evidence"},
    "owner": "Essential Web Tools",
    "kind": "non-generative semantic decision engine (structured results + calibrated probabilities)",
    "family_members": {"Djorge Origo": "base, dense (this model)", "Djorge Mosaic": "planned, routed specialists",
                       "Djorge Ascend": "planned, larger reasoning", "Djorge Monet": "planned, multimodal",
                       "Djorge Gauss": "planned, flagship"},
    "base_model": {"name": "Qwen/Qwen3-0.6B", "license": "Apache-2.0", "author": "Qwen team, Alibaba Cloud"},
}


def describe(semantic=None, artifact_meta: dict = None) -> dict:
    meta = artifact_meta or (semantic.meta if semantic is not None else {}) or {}
    ident = {**DEFAULT_IDENTITY, **meta.get("identity", {})}
    ident["version"] = DJORGE_VERSION
    ident.setdefault("catalog", catalog_id(ident.get("tier", "Origo"), 1, meta.get("model_id", ""),
                                           (meta.get("model_config") or {}).get("layers")))
    ident["dsl_version"] = DSL_VERSION
    if semantic is not None:
        m = getattr(semantic, "model", None) or getattr(getattr(semantic, "engine", None), "model", None)
        if m is not None:
            bb = m.backbone
            n_bb = sum(p.numel() for p in bb.parameters()) + sum(b.numel() for n, b in bb.named_buffers() if n.endswith((".q", "q")))
            n_core = sum(p.numel() for n, p in m.named_parameters() if not n.startswith("backbone."))
            cfg = bb.config
            ident["parameters"] = {"backbone": int(n_bb), "origo_core": int(n_core), "total": int(n_bb + n_core),
                                   "layers": cfg.num_hidden_layers, "hidden_size": cfg.hidden_size,
                                   "attention_heads": cfg.num_attention_heads, "vocabulary": cfg.vocab_size,
                                   "window_tokens": meta.get("max_tokens"), "lm_head": "removed (no text generation)",
                                   "kv_cache": "not used (single forward pass)"}
        ident["model_id"] = getattr(semantic, "model_id", None)
        ident["compression"] = meta.get("compression")
        ident["specialists"] = sorted(getattr(semantic, "specialists", {}) or [])
    ident["languages"] = ["pt", "en", "es", "fr", "de"]
    ident["operations"] = ["GET", "COUNT", "TOTAL", "REPORT", "EXISTS", "CHOICE", "RANK", "SET", "SCORE", "NOUL"]
    ident["resolution_modes"] = ["ID", "CLASS", "ENTITY", "TYPE", "REGEX", "ANSWER", "AUTO"]
    return ident
