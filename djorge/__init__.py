"""DJORGE: a semantic programming and decision engine built on a Qwen-derived model.

    from djorge import Djorge
    z = Djorge(artifact="artifacts/djorge-qwen3-0.6b")
    z.execute('COUNT CLASS Fruit FROM "Arthur comprou maçãs, uma banana e duas laranjas."')

Not a chatbot: no generation, no conversation history, no hidden memory.
"""
from __future__ import annotations

import os

from typing import Dict, List, Optional

from .config import DjorgeConfig
from .errors import DjorgeError
from .runtime import registry
from .runtime.context import ContextStore
from .runtime.executor import Executor
from .version import DJORGE_VERSION

__version__ = "0.8.0"


class Djorge:
    def __init__(self, artifact: Optional[str] = None, config: Optional[DjorgeConfig] = None, semantic=None):
        self.config = config or DjorgeConfig.load()
        if artifact:
            self.config.artifact = artifact
        if semantic is None and self.config.artifact:
            from .runtime.semantic import SemanticEngine
            semantic = SemanticEngine.from_artifact(self.config.artifact, self.config.device, self.config.dtype,
                                                    batch_size=self.config.batch_size,
                                                    prune=self.config.thresholds.prune,
                                                    option_permutations=self.config.option_permutations)
        self.semantic = semantic
        self.artifact_thresholds = self._load_artifact_thresholds()
        self.specialists = self._load_specialists()
        from .runtime.guard import SafetyGuard
        self.guard = SafetyGuard(self.config.safety_policy, self.config.safety_mode, engine=semantic)
        self.contexts = ContextStore(self.config.context_dir)
        self.reputation = None
        import os as _os
        _rep = _os.environ.get("DJORGE_REPUTATION_FILE") or (
            _os.path.join(self.config.artifact, "reputation.json") if self.config.artifact else None)
        if _rep and _os.path.exists(_rep) and _os.environ.get("DJORGE_SENTINEL", "1") != "0":
            try:
                from .runtime.sentinel import Reputation
                self.reputation = Reputation(_rep)
            except Exception:
                self.reputation = None
        self.executor = Executor(self.config, semantic, self.contexts)

    def _load_artifact_thresholds(self) -> dict:
        """Acceptance thresholds tuned on real calibration data travel WITH the artifact
        (calibration/thresholds.json). They replace only values the user left at their defaults."""
        import json, os
        from .config import Thresholds
        path = getattr(self.semantic, "artifact_path", None) or self.config.artifact
        f = os.path.join(path, "calibration", "thresholds.json") if path else None
        if not f or not os.path.exists(f) or not self.config.use_artifact_thresholds:
            return {}
        tuned = {k: float(v) for k, v in json.load(open(f)).get("accept", {}).items()}
        default = Thresholds().accept
        applied = {k: v for k, v in tuned.items() if self.config.thresholds.accept.get(k) == default.get(k)}
        self.config.thresholds.accept.update(applied)
        tuned_ab = {k: float(v) for k, v in json.load(open(f)).get("abstain", {}).items()}
        default_ab = Thresholds().abstain
        applied_ab = {k: v for k, v in tuned_ab.items() if self.config.thresholds.abstain.get(k) == default_ab.get(k)}
        self.config.thresholds.abstain.update(applied_ab)
        return {**applied, **{f"abstain_{k}": v for k, v in applied_ab.items()}}

    def _load_specialists(self) -> dict:
        """Specialist LoRAs shipped in artifact/specialists/registry.json (loaded lazily on first use)."""
        import json, os
        path = getattr(self.semantic, "artifact_path", None) or self.config.artifact
        f = os.path.join(path, "specialists", "registry.json") if path else None
        if not f or not os.path.exists(f):
            return {}
        reg = json.load(open(f))
        out = {k: {"name": v["name"], "path": os.path.join(path, "specialists", v["file"]), "alpha_over_r": v["alpha_over_r"]}
               for k, v in reg.items()}
        if self.semantic is not None:
            self.semantic.specialists = out
            if "safety" in out:                        # the safety specialist is small: keep it warm
                self.semantic.ensure_adapter(out["safety"])
        return out

    def execute(self, query: str, source: Optional[str] = None, sources: Optional[Dict[str, str]] = None,
                tenant: Optional[str] = None, context_version: Optional[int] = None, diagnostics: bool = False,
                certainty: Optional[float] = None, specialist: Optional[str] = None, safety: Optional[str] = None,
                lang: str = "pt", now: Optional[str] = None):
        """now: reference time (ISO 8601) for dates in the text; default = server clock in DJORGE_TZ.
        certainty: desired accuracy of answered decisions (e.g. 0.95). Below it, DJORGE abstains.
        specialist: name of a specialist LoRA ("code", "web", ...) to use for this request only.
        safety: override the safety mode for this request ("off" | "lexical" | "auto" | "strict")."""
        spec = None
        if specialist:
            if specialist not in self.specialists:
                raise DjorgeError("INVALID_REQUEST", f"unknown specialist {specialist!r}", {"available": sorted(self.specialists)})
            spec = self.specialists[specialist]
        from .lang.parser import parse
        from .lang.quote import clean_text
        from .runtime import advice as A
        if source is not None and isinstance(source, str):
            source = clean_text(source)
        if sources:
            sources = {k: clean_text(v) if isinstance(v, str) else v for k, v in sources.items()}
        prog = parse(query)                                   # PARSE_ERROR surfaces here, with the quote hint
        lang = lang if lang in A.SAFETY_RESPONSE else "pt"
        report = None
        if (safety or self.config.safety_mode) != "off":
            g = self.guard
            if safety and safety != g.mode:
                from .runtime.guard import SafetyGuard
                g = SafetyGuard(self.config.safety_policy, safety, engine=self.semantic)
            q_parts = A.question_parts(prog)
            q_rep = g.check(q_parts, targets=_targets(query), lang=lang) if q_parts else {"level": "ok", "actions": {}}
            # flags describe the ANALYSED text only: a label such as "risco de automutilação" in a moderation query
            # is not a risk signal (it made every moderation request look flagged)
            t_parts = A.text_parts(prog, source, sources)
            report = g.check(t_parts, targets=_targets(query), lang=lang) if t_parts else {"level": "ok", "actions": {}}
            if q_rep.get("level") == "block":
                report = q_rep
            refuse = q_rep.get("level") == "block" or bool(set(q_rep.get("actions", {})) & A.QUESTION_REFUSE) \
                or report.get("level") == "block"
            if refuse:                                        # generic safety response instead of a decision
                res = A.safety_response(lang, report if report.get("level") == "block" else q_rep)
                return {"result": res, "surface": res["message"], "safety": report,
                        "meta": {"refused": True, "versions": {"djorge": __import__("djorge.version", fromlist=["x"]).DJORGE_VERSION}}}
        from .runtime import temporal as _tmp
        _tok = _tmp.REF.set(now) if now else None
        try:
            out = self.executor.execute(query, source, sources, tenant, context_version, diagnostics, certainty, spec)
        except DjorgeError:
            raise
        except Exception as e:                                # a bug must never look like a model answer
            raise DjorgeError("MODEL_ERROR", "internal error while executing the request; it was not answered",
                              {"type": type(e).__name__})
        finally:
            if _tok is not None:
                _tmp.REF.reset(_tok)
        out.setdefault("meta", {})["now"] = _tmp.now().isoformat(timespec="seconds") if not now else now
        if report is not None:
            out["safety"] = report
            if report.get("level") == "flag":
                out["safety_notice"] = A.NOTICE[lang]
        res0 = out.get("result")
        if self.config.cascade_artifact and isinstance(res0, dict) and not res0.get("refused") \
                and res0.get("decision") in ("CHOICE", "SCORE", "NOUL", "SET") \
                and (res0.get("abstain") or (isinstance(res0.get("probability"), (int, float))
                                             and res0["probability"] < self.config.cascade_below)):
            try:                                              # unsure -> ask the stronger model (Flash -> Origo)
                big = self.cascade_model()
                o2 = big.executor.execute(query, source, sources, tenant, context_version, diagnostics, certainty, spec)
                r2 = o2.get("result", {})
                out["cascade"] = {"from": getattr(self.semantic, "model_id", None), "to": getattr(big.semantic, "model_id", None),
                                  "first_answer": res0.get("answer"), "first_probability": res0.get("probability"),
                                  "used": "second"}
                out["result"] = r2
            except Exception:
                out["cascade"] = {"used": "first", "error": "cascade model unavailable"}
        so = A.second_opinion(out.get("result"), A.text_parts(prog, source, sources), self.guard, lang)
        if so:
            out["needs_review"] = True
            out["second_opinion"] = so
            out.setdefault("advice", []).append({"code": "safety_disagreement",
                                                 "message": A.ADVICE["safety_disagreement"][lang].format(cat=so["category"])})
        try:                                                  # hand-off to the security specialist (Sentinel)
            if os.environ.get("DJORGE_SENTINEL", "1") == "0":
                raise ImportError("sentinel disabled")
            from .runtime.sentinel_link import recommend as _rec
            rec = _rec(out.get("safety"), out.get("second_opinion"))
            if rec:
                out["sentinel"] = rec
        except Exception:
            pass
        dom = A.domain_flags([*A.question_parts(prog), *A.text_parts(prog, source, sources)], lang)
        if dom:
            out["domain_flags"] = dom["flags"]
            out["disclaimers"] = dom["disclaimers"]
        adv = A.advise(prog, out.get("result", out.get("results")), source, sources, lang)
        if adv:
            out["advice"] = out.get("advice", []) + adv
        if spec:
            out["specialist"] = specialist
        return out

    def cascade_model(self):
        if getattr(self, "_cascade", None) is None:
            import copy
            cfg = copy.deepcopy(self.config)
            cfg.cascade_artifact = None
            cfg.artifact = self.config.cascade_artifact
            self._cascade = Djorge(config=cfg)
        return self._cascade

    def about(self) -> dict:
        from .identity import describe
        return describe(self.semantic)

    def capabilities(self) -> dict:
        return registry.capabilities()

    def model_info(self) -> dict:
        if not self.semantic:
            return {"loaded": False}
        m = self.semantic.meta
        from .identity import describe
        return {"loaded": True, "identity": describe(self.semantic), "model_id": self.semantic.model_id, "backbone": m.get("backbone"),
                "versions": m.get("versions"), "device": self.semantic.device,
                "accept_thresholds": self.config.thresholds.accept, "tuned_from_artifact": self.artifact_thresholds}


def _targets(query: str):
    """Targets/options named in the query (CLASS targets, CHOICE options) - used for profiling checks."""
    import re
    return re.findall(r'(?:CLASS|ENTITY|ID)\s+("[^"]*"|\S+)', query) + re.findall(r'"([^"]{1,80})"\s*[,\]:]', query)


__all__ = ["Djorge", "DjorgeConfig", "DjorgeError"]
