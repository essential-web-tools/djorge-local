"""Explicit, versioned, tenant-isolated structured context (user-owned adaptation data).

Hard rule: DJORGE remembers what a user explicitly TEACHES it, never what it was
merely ASKED. `execute()` has no write path into this store; only the context
API (add/remove/import/clear/adapt) mutates it, and every mutation creates a new
immutable version snapshot.

Typed records
  class_definition       {name, definition?, examples?[]}           -> query representation (<DEF>)
  alias                  {target, aliases[]}                         -> ID identity expansion
  threshold              {mode, accept?, abstain?}                   -> per-tenant thresholds
  classification_example {input, target, options[]}                 -> adaptation data
  extraction_example     {input, mode, target, spans[[start,end]]}   -> adaptation data
  correction             {input, query, expected}                    -> adaptation data (review)
  entity                 {name, description, routes?[]}              -> registered people/teams for routing
                         (e.g. Fulano: "responsável pelo financeiro; recebe cobranças e notas fiscais")
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from typing import Dict, List, Optional

from ..errors import DjorgeError

_TENANT = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SCHEMA = {
    "class_definition": {"name": str},
    "alias": {"target": str, "aliases": list},
    "threshold": {"mode": str},
    "classification_example": {"input": str, "target": str, "options": list},
    "extraction_example": {"input": str, "mode": str, "target": str, "spans": list},
    "correction": {"input": str, "query": str, "expected": object},
    "entity": {"name": str, "description": str},
}


def validate_record(r: dict) -> dict:
    if not isinstance(r, dict) or r.get("type") not in SCHEMA:
        raise DjorgeError("INVALID_CONTEXT", f"record type must be one of {sorted(SCHEMA)}")
    for k, t in SCHEMA[r["type"]].items():
        if k not in r or (t is not object and not isinstance(r[k], t)):
            raise DjorgeError("INVALID_CONTEXT", f"{r['type']} requires field {k!r} of type {t.__name__}")
    if r["type"] == "extraction_example":
        for sp in r["spans"]:
            if not (isinstance(sp, list) and len(sp) == 2 and 0 <= sp[0] < sp[1] <= len(r["input"])):
                raise DjorgeError("INVALID_CONTEXT", f"bad span {sp} for input of length {len(r['input'])}")
        if r["mode"] not in ("CLASS", "ENTITY"):
            raise DjorgeError("INVALID_CONTEXT", "extraction_example.mode must be CLASS or ENTITY")
    if r["type"] == "classification_example" and r["target"] not in r["options"]:
        raise DjorgeError("INVALID_CONTEXT", "classification_example.target must be one of options")
    if r["type"] == "threshold":
        for k in ("accept", "abstain"):
            if k in r and not (isinstance(r[k], (int, float)) and 0 <= r[k] <= 1):
                raise DjorgeError("INVALID_CONTEXT", f"threshold.{k} must be in [0, 1]")
    if len(json.dumps(r)) > 20_000:
        raise DjorgeError("INVALID_CONTEXT", "record too large")
    r = copy.deepcopy(r)
    r.setdefault("metadata", {})
    body = {k: v for k, v in r.items() if k not in ("id", "metadata")}
    r["id"] = r.get("id") or hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return r


class Snapshot:
    def __init__(self, data: dict):
        self.data = data
        self.tenant, self.version = data["tenant"], data["version"]
        recs = data["records"].values()
        self._recs = list(recs)
        self._classes = {r["name"].casefold(): r for r in recs if r["type"] == "class_definition"}
        self._aliases: Dict[str, List[str]] = {}
        for r in recs:
            if r["type"] == "alias":
                self._aliases.setdefault(r["target"].casefold(), []).extend(r["aliases"])
        self.thresholds = {r["mode"].upper(): r for r in recs if r["type"] == "threshold"}

    def class_def(self, name: str) -> Optional[str]:
        r = self._classes.get(name.casefold())
        if not r:
            return None
        parts = [r.get("definition", "")]
        if r.get("examples"):
            parts.append("e.g. " + ", ".join(r["examples"][:20]))
        return "; ".join(p for p in parts if p) or None

    def entities(self) -> List[dict]:
        return [r for r in self._recs if r["type"] == "entity"]

    def aliases(self, target: str) -> List[str]:
        return self._aliases.get(target.casefold(), [])

    def threshold(self, mode: str, kind: str, default):
        r = self.thresholds.get(mode.upper())
        return r.get(kind, default) if r else default

    @property
    def active_adapter(self) -> Optional[str]:
        return self.data.get("active_adapter")


EMPTY = Snapshot({"tenant": "_", "version": 0, "records": {}})


class ContextStore:
    def __init__(self, root: str):
        self.root = root
        self.lock = threading.Lock()
        os.makedirs(root, exist_ok=True)

    def _dir(self, tenant):
        if not _TENANT.match(tenant or ""):
            raise DjorgeError("INVALID_CONTEXT", "tenant ids are 1-64 chars of [A-Za-z0-9_-]")
        return os.path.join(self.root, tenant)

    def _write(self, tenant, data):
        d = self._dir(tenant)
        os.makedirs(os.path.join(d, "versions"), exist_ok=True)
        p = os.path.join(d, "versions", f"v{data['version']}.json")
        tmp = p + ".tmp"
        json.dump(data, open(tmp, "w"), ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        tmp = os.path.join(d, "current.tmp")
        open(tmp, "w").write(str(data["version"]))
        os.replace(tmp, os.path.join(d, "current"))

    def exists(self, tenant) -> bool:
        return os.path.exists(os.path.join(self._dir(tenant), "current"))

    def get(self, tenant: str, version: Optional[int] = None) -> Snapshot:
        d = self._dir(tenant)
        if not self.exists(tenant):
            raise DjorgeError("INVALID_CONTEXT", f"no context for tenant {tenant!r}")
        v = version or int(open(os.path.join(d, "current")).read())
        p = os.path.join(d, "versions", f"v{v}.json")
        if not os.path.exists(p):
            raise DjorgeError("INVALID_CONTEXT", f"tenant {tenant!r} has no version {v}")
        return Snapshot(json.load(open(p)))

    def create(self, tenant: str) -> Snapshot:
        with self.lock:
            if self.exists(tenant):
                return self.get(tenant)
            data = {"tenant": tenant, "version": 1, "created": time.time(), "records": {}, "note": "created"}
            self._write(tenant, data)
            return Snapshot(data)

    def _mutate(self, tenant, fn, note):
        with self.lock:
            cur = self.get(tenant).data if self.exists(tenant) else {"tenant": tenant, "version": 0, "records": {}}
            new = copy.deepcopy(cur)
            fn(new)
            new["version"] = cur["version"] + 1
            new["parent"] = cur["version"]
            new["note"] = note
            new["updated"] = time.time()
            self._write(tenant, new)
            return Snapshot(new)

    def add(self, tenant: str, records: List[dict]) -> Snapshot:
        recs = [validate_record(r) for r in records]
        return self._mutate(tenant, lambda d: d["records"].update({r["id"]: r for r in recs}), f"add {len(recs)}")

    def remove(self, tenant: str, ids: List[str]) -> Snapshot:
        def f(d):
            for i in ids:
                if i not in d["records"]:
                    raise DjorgeError("INVALID_CONTEXT", f"no record {i}")
                del d["records"][i]
        return self._mutate(tenant, f, f"remove {len(ids)}")

    def clear(self, tenant: str) -> Snapshot:
        return self._mutate(tenant, lambda d: (d["records"].clear(), d.pop("active_adapter", None)), "clear")

    def set_active_adapter(self, tenant: str, adapter: Optional[str]) -> Snapshot:
        return self._mutate(tenant, lambda d: d.__setitem__("active_adapter", adapter), f"adapter {adapter}")

    def versions(self, tenant: str) -> List[dict]:
        d = os.path.join(self._dir(tenant), "versions")
        out = []
        for f in sorted(os.listdir(d), key=lambda x: int(x[1:-5])):
            j = json.load(open(os.path.join(d, f)))
            out.append({"version": j["version"], "note": j.get("note"), "records": len(j["records"])})
        return out

    def export(self, tenant: str) -> dict:
        return self.get(tenant).data

    def import_(self, tenant: str, data: dict) -> Snapshot:
        recs = [validate_record(r) for r in data.get("records", {}).values()]
        return self._mutate(tenant, lambda d: d.__setitem__("records", {r["id"]: r for r in recs}),
                            f"import {len(recs)}")
