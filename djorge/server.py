"""DJORGE HTTP server (stdlib only). Semantic execution API - no chat endpoints.

  POST /v1/classify     {text, labels: [..] | {label: description}, question?, multi?, certainty?,
                         calibration?: "context", sentinel?: "check"}                               <- recommended
  POST /v1/aggregate    {text, target, mode?: CLASS|ENTITY|ID, definition?}        AGG: quantities summed by meaning
  POST /v1/tag          {text, labels, mode?: ENTITY|CLASS}        spans with checked offsets, polarity, quantity
  POST /v1/decide       {text, rules: [...], min_p?, tenant?, route?}    composite rules -> actions (never executed)
  POST /v1/document     {text, labels?, question?, k?, chunk_chars?}     long documents: hierarchy + grounded answer
  POST /v1/verify       {text, statement, certainty?}                                               (true/false)
  POST /v1/sentinel     {text, kind?: texto|dominio|email|numero, profile?}  (Djorge Sentinel; off with DJORGE_SENTINEL=0)
  POST /v1/score        {text, levels: [..], question?}                                             (ordered scale)
  POST /execute         {query, source? | sources?, tenant?, context_version?, diagnostics?, certainty?}
  GET  /health   GET /capabilities   GET /model   GET /about
  /contexts/<tenant>/...                                         (explicit teaching; admin key when configured)

Untrusted text NEVER enters the query language in /v1/*: it travels as a named source, so quotes, backslashes,
newlines or DSL keywords inside the text cannot break or change the request. Labels/questions are escaped with
djorge.lang.quote. For /execute, send texts in `sources` and write FROM <name>.

Abuse protection (all configurable by environment):
  DJORGE_API_KEYS      comma-separated keys ("Authorization: Bearer <key>"); DJORGE_API_KEY also accepted
  DJORGE_ADMIN_KEY     required for context writes and /adapt when set (otherwise any valid API key)
  DJORGE_RATE_RPS      sustained requests/second per client (default 20)   DJORGE_RATE_BURST (default 60)
  DJORGE_MAX_BODY      bytes (default 1 MB)    DJORGE_SOCKET_TIMEOUT_S (default 15, stops slow-loris clients)
  DJORGE_TRUST_PROXY   1 = take the client IP from CF-Connecting-IP / X-Forwarded-For (only behind Cloudflare/Caddy)
Constant-time key comparison, per-client token buckets, admission control (503 BUSY instead of queueing),
strict JSON/type/size validation, no payload logging, generic 500s, request ids, security headers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import Djorge
from .errors import DjorgeError
from .runtime.semantic import SemReq
from .lang.quote import NAME as SOURCE_NAME, classify_query, clean_text, score_query, verify_query

STATUS = {"PARSE_ERROR": 400, "TYPE_ERROR": 400, "UNSUPPORTED_OPERATION": 400, "UNSUPPORTED_MODE": 400,
          "UNBOUND_SOURCE": 400, "INVALID_REQUEST": 400, "INVALID_CONTEXT": 400, "UNSAFE_PATTERN": 400,
          "RESOURCE_LIMIT": 413, "MODEL_UNAVAILABLE": 503, "ADAPTATION_REJECTED": 422,
          "SAFETY_BLOCKED": 422, "BUSY": 503, "TIMEOUT": 504, "RATE_LIMITED": 429, "UNAUTHORIZED": 401,
          "FORBIDDEN": 403, "UNSUPPORTED_MEDIA_TYPE": 415, "LENGTH_REQUIRED": 411, "METHOD_NOT_ALLOWED": 405}
MAX_BODY = int(os.environ.get("DJORGE_MAX_BODY", 1_000_000))
TENANT = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
SAFETY_MODES = {"off", "lexical", "auto", "strict"}
LANGS = {"pt", "en", "es"}
MAX_LABELS = 64
MAX_LABEL_CHARS = 300
MAX_QUESTION_CHARS = 400


def _env_float(k, d):
    try:
        return float(os.environ.get(k, d))
    except ValueError:
        return float(d)


class RateLimiter:
    """Token bucket per client id; bounded memory (LRU of 20k clients)."""

    def __init__(self, rps: float, burst: float, max_clients: int = 20_000):
        self.rps, self.burst, self.max = rps, burst, max_clients
        self.b: "OrderedDict[str, list]" = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, client: str, cost: float = 1.0):
        """-> (allowed, retry_after_seconds)"""
        if self.rps <= 0:
            return True, 0.0
        now = time.monotonic()
        with self.lock:
            tok, last = self.b.pop(client, [self.burst, now])
            tok = min(self.burst, tok + (now - last) * self.rps)
            ok = tok >= cost
            if ok:
                tok -= cost
            self.b[client] = [tok, now]
            while len(self.b) > self.max:
                self.b.popitem(last=False)
            return ok, (0.0 if ok else (cost - tok) / self.rps)


class Dedup:
    """Identical requests close in time get the SAME answer without recomputing:
    in-flight requests are coalesced (single-flight) and finished ones are reused for `ttl` seconds."""

    def __init__(self, ttl: float, max_items: int = 5000):
        self.ttl, self.max = ttl, max_items
        self.lock = threading.Lock()
        self.done: "OrderedDict[str, tuple]" = OrderedDict()
        self.inflight: dict = {}

    def run(self, key: str, fn):
        if self.ttl <= 0:
            return fn(), "miss"
        with self.lock:
            hit = self.done.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl:
                self.done.move_to_end(key)
                return hit[1], "hit"
            slot = self.inflight.get(key)
            leader = slot is None
            if leader:
                slot = self.inflight[key] = [threading.Event(), None, None]
        if not leader:
            slot[0].wait(timeout=60)
            if slot[2] is not None:
                raise slot[2]
            if slot[1] is not None:
                return slot[1], "coalesced"
            return fn(), "miss"
        try:
            res = fn()
            slot[1] = res
            with self.lock:
                self.done[key] = (time.monotonic(), res)
                while len(self.done) > self.max:
                    self.done.popitem(last=False)
            return res, "miss"
        except Exception as e:
            slot[2] = e
            raise
        finally:
            with self.lock:
                self.inflight.pop(key, None)
            slot[0].set()


class Meter:
    """Per-client usage and cost, to pass on to end users.
    cost = DJORGE_PRICE_PER_REQUEST + tokens / 1e6 * DJORGE_PRICE_PER_1M_TOKENS ; reused answers (dedup) are charged
    DJORGE_CACHE_PRICE_FACTOR of that (default 0 = free). Currency label: DJORGE_CURRENCY. Optional persistence:
    DJORGE_USAGE_FILE (JSON, written at most every 10 s)."""

    def __init__(self):
        self.per_req = _env_float("DJORGE_PRICE_PER_REQUEST", 0.0)
        self.per_mtok = _env_float("DJORGE_PRICE_PER_1M_TOKENS", 0.10)
        self.cache_factor = _env_float("DJORGE_CACHE_PRICE_FACTOR", 0.0)
        self.currency = os.environ.get("DJORGE_CURRENCY", "USD")
        self.file = os.environ.get("DJORGE_USAGE_FILE")
        self.lock = threading.Lock()
        self.totals: dict = {}
        self._last_write = 0.0
        if self.file and os.path.exists(self.file):
            try:
                self.totals = json.load(open(self.file))
            except Exception:
                self.totals = {}

    def prices(self):
        return {"per_request": self.per_req, "per_1m_tokens": self.per_mtok, "reused_answer_factor": self.cache_factor,
                "currency": self.currency}

    def charge(self, client: str, tokens: int, status: str, tool: bool = False) -> dict:
        base = 0.0 if tool else self.per_req + tokens / 1e6 * self.per_mtok
        cost = base * (self.cache_factor if status in ("hit", "coalesced") else 1.0)
        with self.lock:
            t = self.totals.setdefault(client, {"requests": 0, "reused": 0, "tokens": 0, "cost": 0.0})
            t["requests"] += 1; t["tokens"] += tokens; t["cost"] = round(t["cost"] + cost, 10)
            t["reused"] += status in ("hit", "coalesced")
            if self.file and time.monotonic() - self._last_write > 10:
                self._last_write = time.monotonic()
                try:
                    tmp = self.file + ".tmp"
                    json.dump(self.totals, open(tmp, "w")); os.replace(tmp, self.file)
                except Exception:
                    pass
        return {"tokens": tokens, "reused": status in ("hit", "coalesced"), "cache": status,
                "cost": round(cost, 8), "currency": self.currency}

    def get(self, client: str) -> dict:
        with self.lock:
            return dict(self.totals.get(client, {"requests": 0, "reused": 0, "tokens": 0, "cost": 0.0}))


def _keys():
    raw = os.environ.get("DJORGE_API_KEYS") or ""
    ks = [k.strip() for k in raw.split(",") if k.strip()]
    return ks


def _req_str(b, k, max_chars, required=True, allow_empty=False):
    v = b.get(k)
    if v is None:
        if required:
            raise DjorgeError("INVALID_REQUEST", f"field '{k}' (string) is required")
        return None
    if not isinstance(v, str):
        raise DjorgeError("INVALID_REQUEST", f"field '{k}' must be a string")
    if not allow_empty and not v.strip():
        raise DjorgeError("INVALID_REQUEST", f"field '{k}' must not be empty")
    if len(v) > max_chars:
        raise DjorgeError("RESOURCE_LIMIT", f"field '{k}' is longer than {max_chars} characters")
    return v


def _certainty(b):
    c = b.get("certainty")
    if c is None:
        return None
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0.5 <= c < 1.0:
        raise DjorgeError("INVALID_REQUEST", "'certainty' must be a number in [0.5, 1.0)")
    return float(c)


def _labels(b, key="labels", min_n=2):
    v = b.get(key)
    if isinstance(v, list):
        items = [(x, "") for x in v]
    elif isinstance(v, dict):
        items = list(v.items())
    else:
        raise DjorgeError("INVALID_REQUEST", f"'{key}' must be a list of strings or an object {{label: description}}")
    if not min_n <= len(items) <= MAX_LABELS:
        raise DjorgeError("INVALID_REQUEST", f"'{key}' needs {min_n}..{MAX_LABELS} entries")
    out, seen = {}, set()
    for k, d in items:
        if not isinstance(k, str) or not isinstance(d, str) or not k.strip():
            raise DjorgeError("INVALID_REQUEST", f"every entry of '{key}' must be a non-empty string")
        if len(k) > MAX_LABEL_CHARS or len(d) > MAX_LABEL_CHARS:
            raise DjorgeError("RESOURCE_LIMIT", f"labels/descriptions are limited to {MAX_LABEL_CHARS} characters")
        k2 = clean_text(k).strip()
        if k2.casefold() in seen:
            raise DjorgeError("INVALID_REQUEST", f"duplicate label {k2!r}")
        seen.add(k2.casefold()); out[k2] = clean_text(d).strip()
    return out


def _common(b):
    """Validated optional fields shared by every semantic endpoint."""
    tenant = b.get("tenant")
    if tenant is not None and (not isinstance(tenant, str) or not TENANT.match(tenant)):
        raise DjorgeError("INVALID_REQUEST", "'tenant' must be 1-64 chars of [A-Za-z0-9_-]")
    cv = b.get("context_version")
    if cv is not None and (isinstance(cv, bool) or not isinstance(cv, int) or cv < 0):
        raise DjorgeError("INVALID_REQUEST", "'context_version' must be a non-negative integer")
    safety = b.get("safety")
    if safety is not None and safety not in SAFETY_MODES:
        raise DjorgeError("INVALID_REQUEST", f"'safety' must be one of {sorted(SAFETY_MODES)}")
    lang = b.get("lang", "pt")
    if lang not in LANGS:
        lang = "pt"
    spec = b.get("specialist")
    if spec is not None and not (isinstance(spec, str) and SOURCE_NAME.match(spec)):
        raise DjorgeError("INVALID_REQUEST", "'specialist' must be an identifier")
    return dict(tenant=tenant, context_version=cv, certainty=_certainty(b), specialist=spec, safety=safety, lang=lang,
                diagnostics=b.get("diagnostics") is True)


def make_handler(z: Djorge):
    gate = threading.BoundedSemaphore(max(1, int(getattr(z.config, "max_inflight", 64) or 64)))
    keys = _keys() + ([z.config.api_key] if z.config.api_key else [])
    key_digests = [hashlib.sha256(k.encode()).digest() for k in keys]
    admin = os.environ.get("DJORGE_ADMIN_KEY") or ""
    admin_digest = hashlib.sha256(admin.encode()).digest() if admin else None
    trust_proxy = os.environ.get("DJORGE_TRUST_PROXY", "0") == "1"
    limiter = RateLimiter(_env_float("DJORGE_RATE_RPS", 20), _env_float("DJORGE_RATE_BURST", 60))
    heavy = RateLimiter(1 / 60.0, 1)                     # /adapt: at most ~1 per minute per client
    lim = z.config.limits
    daily_cap = int(_env_float("DJORGE_DAILY_CAP", 0))              # whole server, per UTC day (0 = off)
    daily_cap_key = int(_env_float("DJORGE_DAILY_CAP_PER_KEY", 0))  # per API key / client
    day = {"d": None, "all": 0, "by": {}}
    day_lock = threading.Lock()

    def over_daily(client):
        if not daily_cap and not daily_cap_key:
            return False
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with day_lock:
            if day["d"] != today:
                day.update(d=today, all=0, by={})
            if (daily_cap and day["all"] >= daily_cap) or (daily_cap_key and day["by"].get(client, 0) >= daily_cap_key):
                return True
            day["all"] += 1; day["by"][client] = day["by"].get(client, 0) + 1
            if len(day["by"]) > 50_000:
                day["by"].clear()
            return False
    dedup = Dedup(_env_float("DJORGE_DEDUP_TTL_S", 30))
    model_tag = str(getattr(getattr(z.semantic, "engine", z.semantic), "model_id", None) or "none")
    brain_cookie = {   # policy the browser SDK (clients/brain-cookie.js) follows; the server can switch it off any time
        "enabled": os.environ.get("DJORGE_BRAIN_COOKIE", "1") == "1", "model": model_tag,
        "ttl_s": int(_env_float("DJORGE_BC_TTL_S", 7 * 86400)), "min_probability": _env_float("DJORGE_BC_MIN_P", 0.95),
        "min_repeats": int(_env_float("DJORGE_BC_MIN_REPEATS", 2)), "max_entries": int(_env_float("DJORGE_BC_MAX_ENTRIES", 500)),
        "max_bytes": int(_env_float("DJORGE_BC_MAX_BYTES", 256_000)), "shadow_rate": _env_float("DJORGE_BC_SHADOW", 0.05),
        "max_disagreement": _env_float("DJORGE_BC_MAX_DISAGREE", 0.01), "min_shadow_checks": 50,
        "cacheable_paths": ["/v1/classify", "/v1/verify", "/v1/score", "/v1/select"]}
    meter = Meter()
    refcal_box = {}

    _thr = {}

    def sentinel_thresholds():
        """Minimum P(safe) per kind before answering 'safe' (metadata/sentinel_thresholds.json); {} = off."""
        if "v" not in _thr:
            ap = getattr(getattr(z.semantic, "engine", z.semantic), "artifact_path", None)
            p = os.path.join(ap, "metadata", "sentinel_thresholds.json") if ap else None
            try:
                _thr["v"] = {k: float(v) for k, v in json.load(open(p)).items()} if p and os.path.exists(p) else {}
            except Exception:
                _thr["v"] = {}
        return _thr["v"]

    def sentinel_kinds():
        """Kinds this artifact was trained on (metadata/sentinel_kinds.json, written at export); None = no list."""
        ap = getattr(getattr(z.semantic, "engine", z.semantic), "artifact_path", None)
        p = os.path.join(ap, "metadata", "sentinel_kinds.json") if ap else None
        if p and os.path.exists(p):
            try:
                return set(json.load(open(p)))
            except Exception:
                return None
        return None
    _eng = getattr(z.semantic, "engine", z.semantic)
    tok = getattr(_eng, "tok", None)

    def count_tokens(*texts) -> int:
        txt = "\n".join(t for t in texts if t)
        if tok is None:
            return max(1, len(txt) // 4)
        try:
            return len(tok(txt, add_special_tokens=False)["input_ids"])
        except Exception:
            return max(1, len(txt) // 4)

    class H(BaseHTTPRequestHandler):
        server_version = "djorge"
        sys_version = ""
        timeout = _env_float("DJORGE_SOCKET_TIMEOUT_S", 15)        # slow clients cannot hold a thread forever

        def log_message(self, fmt, *args):          # metadata only, never payloads
            pass

        # ------------------------------------------------------------------ plumbing
        def _send_text(self, code, text, extra=None):
            body = (text or "").encode()
            self.send_response(code)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.send_header("x-request-id", self.rid)
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("cache-control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send(self, code, obj, extra=None):
            obj = dict(obj) if isinstance(obj, dict) else {"data": obj}
            if code >= 400:
                obj.setdefault("request_id", self.rid)
            body = json.dumps(obj, ensure_ascii=False, allow_nan=False, default=str).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.send_header("x-request-id", self.rid)
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("cache-control", "no-store")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("x-djorge-model", model_tag)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            if getattr(self, "cache_status", None):
                self.send_header("x-djorge-cache", self.cache_status)
            if self.close_after:
                self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _client(self):
            if trust_proxy:
                ip = self.headers.get("cf-connecting-ip") or (self.headers.get("x-forwarded-for") or "").split(",")[0].strip()
                if ip:
                    return ip
            return self.client_address[0]

        def _token(self):
            h = self.headers.get("authorization") or ""
            return h[7:].strip() if h[:7].lower() == "bearer " else ""

        def _auth(self):
            if not key_digests:
                return True, "ip:" + self._client()
            tok = self._token()
            d = hashlib.sha256(tok.encode()).digest()
            ok = False
            for kd in key_digests:                    # constant time, check every key
                ok |= hmac.compare_digest(d, kd)
            return ok, "key:" + d.hex()[:16]

        def _is_admin(self):
            if admin_digest is None:
                return True
            return hmac.compare_digest(hashlib.sha256(self._token().encode()).digest(), admin_digest)

        def _body(self):
            if "chunked" in (self.headers.get("transfer-encoding") or "").lower():
                raise DjorgeError("LENGTH_REQUIRED", "send a content-length (chunked bodies are not accepted)")
            raw = self.headers.get("content-length")
            if raw is None:
                raise DjorgeError("LENGTH_REQUIRED", "content-length header is required")
            try:
                n = int(raw)
            except ValueError:
                raise DjorgeError("INVALID_REQUEST", "bad content-length")
            if n < 0 or n > MAX_BODY:
                self.close_after = True
                raise DjorgeError("RESOURCE_LIMIT", f"request body too large (max {MAX_BODY} bytes)")
            ctype = (self.headers.get("content-type") or "").lower()
            if n and "application/json" not in ctype:
                self.close_after = True
                raise DjorgeError("UNSUPPORTED_MEDIA_TYPE", "content-type must be application/json")
            data = self.rfile.read(n) if n else b""
            if not data:
                return {}
            try:
                b = json.loads(data.decode("utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
            except (UnicodeDecodeError, ValueError):
                raise DjorgeError("INVALID_REQUEST", "body must be valid UTF-8 JSON")
            if not isinstance(b, dict):
                raise DjorgeError("INVALID_REQUEST", "body must be a JSON object")
            return b

        def _run(self, b, query, source=None, sources=None):
            c = _common(b)
            masked = 0
            if b.get("mask_pii") is True:                       # privacy: PII never reaches the model
                from .runtime.tools import mask_pii
                if source is not None:
                    m = mask_pii(source); source = m["text"]; masked += len(m["found"])
                if sources:
                    ms = {k: mask_pii(v) for k, v in sources.items()}
                    sources = {k: m["text"] for k, m in ms.items()}; masked += sum(len(m["found"]) for m in ms.values())
            key = hashlib.sha256(json.dumps({"who": self.client, "q": query, "s": source, "ss": sources,
                                             "o": {k: c[k] for k in sorted(c)}, "m": getattr(_eng, "model_id", None)},
                                            sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
            if c["tenant"] and c["context_version"] is None:     # tenant context may change: never reuse
                out, status = self._exec(b, c, query, source, sources), "miss"
            else:
                out, status = dedup.run(key, lambda: self._exec(b, c, query, source, sources))
            out = dict(out)
            self.cache_status = status
            out["usage"] = meter.charge(self.client, count_tokens(query, source, *((sources or {}).values())), status)
            if masked:
                out["pii_masked"] = masked
            return out

        def _exec(self, b, c, query, source, sources):
            try:
                wait = min(max(float(b.get("wait_s", 2.0)), 0.0), 5.0)
            except (TypeError, ValueError):
                wait = 2.0
            if not gate.acquire(timeout=wait):           # admission control: never pile up
                raise DjorgeError("BUSY", "server at capacity, retry shortly")
            try:
                now = b.get("now") if isinstance(b.get("now"), str) and len(b.get("now")) <= 40 else None
                if now:
                    from .runtime.temporal import now as _now
                    try:
                        _now(now)
                    except ValueError:
                        raise DjorgeError("INVALID_REQUEST", "now must be ISO 8601, e.g. 2026-10-26T19:30:00-03:00")
                return z.execute(query, source, sources, c["tenant"], c["context_version"], c["diagnostics"],
                                 c["certainty"], c["specialist"], c["safety"], c["lang"], now=now)
            finally:
                gate.release()

        def _text(self, b, key="text"):
            return clean_text(_req_str(b, key, lim.max_source_chars))

        # ------------------------------------------------------------------ routing
        def _route(self, method):
            self.rid = uuid.uuid4().hex[:16]
            self.close_after = False
            self.cache_status = None
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            if parts[:1] == ["v1"] and parts[1:2] == ["execute"]:
                parts = ["execute"]
            try:
                if parts == ["health"] and method == "GET":
                    return self._send(200, {"status": "ok", "semantic_engine": z.semantic is not None})
                ok, client = self._auth()
                if not ok:
                    allowed, retry = limiter.allow("bad:" + self._client(), 5.0)   # key guessing costs 5x
                    if not allowed:
                        return self._send(429, {"error": "RATE_LIMITED", "message": "too many failed attempts"},
                                          {"retry-after": str(max(1, math.ceil(retry)))})
                    return self._send(401, {"error": "UNAUTHORIZED", "message": "missing or invalid API key"},
                                      {"www-authenticate": "Bearer"})
                self.client = client
                allowed, retry = limiter.allow(client)
                if allowed and method == "POST" and over_daily(client):
                    return self._send(429, {"error": "RATE_LIMITED", "message": "daily request limit reached"},
                                      {"retry-after": "3600"})
                if not allowed:
                    return self._send(429, {"error": "RATE_LIMITED", "message": "too many requests"},
                                      {"retry-after": str(max(1, math.ceil(retry)))})
                if method == "GET" and parts == ["capabilities"]:
                    return self._send(200, z.capabilities())
                if method == "GET" and parts == ["model"]:
                    return self._send(200, z.model_info())
                if method == "GET" and parts == ["about"]:
                    return self._send(200, z.about())
                if method == "GET" and parts == ["v1", "brain-cookie"]:
                    return self._send(200, brain_cookie)
                if method == "GET" and parts == ["v1", "usage"]:
                    return self._send(200, {"client": client.split(":")[0], "usage": meter.get(client),
                                            "prices": meter.prices()})
                if parts[:2] == ["v1", "tools"] and len(parts) == 3:
                    if method != "POST":
                        raise DjorgeError("METHOD_NOT_ALLOWED", "use POST")
                    return self._send(200, self._tool(parts[2], self._body()))
                if parts == ["v1", "select"]:
                    if method != "POST":
                        raise DjorgeError("METHOD_NOT_ALLOWED", "use POST")
                    return self._select(self._body(), u)
                if parts == ["v1", "sentinel"] and getattr(z.config, "sentinel_endpoint", os.environ.get("DJORGE_SENTINEL", "1") != "0"):
                    if method != "POST":
                        raise DjorgeError("METHOD_NOT_ALLOWED", "use POST")
                    return self._send(200, self._sentinel(self._body()))
                if parts == ["execute"]:
                    if method != "POST":
                        raise DjorgeError("METHOD_NOT_ALLOWED", "use POST")
                    b = self._body()
                    q = _req_str(b, "query", lim.max_query_chars)
                    src = b.get("source")
                    if src is not None:
                        src = clean_text(_req_str(b, "source", lim.max_source_chars, allow_empty=True))
                    srcs = b.get("sources")
                    if srcs is not None:
                        if not isinstance(srcs, dict) or len(srcs) > 32:
                            raise DjorgeError("INVALID_REQUEST", "'sources' must be an object with at most 32 entries")
                        total = 0
                        for k, v in srcs.items():
                            if not SOURCE_NAME.match(k) or not isinstance(v, str):
                                raise DjorgeError("INVALID_REQUEST", "source names must be identifiers and values strings")
                            total += len(v)
                        if total > lim.max_source_chars:
                            raise DjorgeError("RESOURCE_LIMIT", "sources are too long")
                        srcs = {k: clean_text(v) for k, v in srcs.items()}
                    return self._send(200, self._run(b, q, src, srcs))
                if parts[:1] == ["v1"] and len(parts) == 2 and parts[1] in ("classify", "verify", "score"):
                    if method != "POST":
                        raise DjorgeError("METHOD_NOT_ALLOWED", "use POST")
                    b = self._body()
                    text = self._text(b)
                    ask = _req_str(b, "question", MAX_QUESTION_CHARS, required=False, allow_empty=True) or ""
                    if parts[1] == "classify":
                        q = classify_query(_labels(b), ask=ask, multi=b.get("multi") is True)
                    elif parts[1] == "verify":
                        q = verify_query(_req_str(b, "statement", MAX_LABEL_CHARS))
                    else:
                        lv = b.get("levels")
                        if not isinstance(lv, list):
                            raise DjorgeError("INVALID_REQUEST", "'levels' must be a list of strings, lowest first")
                        q = score_query(list(_labels({"levels": lv}, "levels")), ask=ask)
                    out = self._run(b, q, sources={"text": text})
                    r = out.get("result", {})
                    if r.get("decision") == "SCORE" and "label" in r:          # scale: return the level, not its index
                        r = dict(r, answer=r["label"], proposed_value=r.get("label"))
                    compact = {"answer": None if r.get("abstain") else r.get("answer"),
                               "proposed": r.get("proposed_value", r.get("answer")),
                               "probability": r.get("probability"), "abstain": bool(r.get("abstain")),
                               "distribution": r.get("distribution"), "selected": r.get("selected"),
                               "grounding": r.get("grounding"), "safety": out.get("safety"), "model": r.get("model"),
                               "refused": r.get("refused"), "message": r.get("message"), "support": r.get("support"),
                               "safety_notice": out.get("safety_notice"), "advice": out.get("advice"),
                               "domain_flags": out.get("domain_flags"), "disclaimers": out.get("disclaimers"),
                               "usage": out.get("usage"), "pii_masked": out.get("pii_masked"),
                               "needs_review": out.get("needs_review"), "second_opinion": out.get("second_opinion"),
                               "cascade": out.get("cascade"), "sentinel": out.get("sentinel"),
                               "prediction_set": r.get("prediction_set")}
                    if parts[1] == "classify" and b.get("calibration") == "context" and r.get("distribution") \
                            and not r.get("refused") and b.get("multi") is not True:
                        labs = _labels(b)                       # reference-adjusted (contextual) calibration
                        cc = refcal_box.get("cc")
                        if cc is None:
                            from .runtime.refcal import ContextualCalibrator
                            cc = refcal_box["cc"] = ContextualCalibrator(_eng)
                        adj = cc.apply({"distribution": r["distribution"]}, list(labs), list(labs.values()), ask)
                        thr = r.get("abstain_threshold")
                        ab = bool(thr) and adj["probability"] < thr
                        compact.update(answer=None if ab else adj["answer"], proposed=adj["answer"],
                                       probability=adj["probability"], abstain=ab, distribution=adj["distribution"],
                                       uncorrected=r["distribution"], reference=adj["reference_distribution"],
                                       calibration="contextual")
                    if b.get("sentinel") == "check" and compact.get("sentinel") and \
                            os.environ.get("DJORGE_SENTINEL", "1") != "0":
                        from .runtime.sentinel_link import check as _sck
                        kind = (compact["sentinel"].get("sentinel_kinds") or ["texto"])[0]
                        compact["sentinel"] = dict(compact["sentinel"], check=_sck(text, kind))
                        if compact["sentinel"]["check"].get("fail_closed"):
                            compact["needs_review"] = True
                    if b.get("diagnostics") is True:
                        compact["full"] = out
                    return self._send(200, {k: v for k, v in compact.items() if v is not None or k == "answer"})
                if parts[:1] == ["v1"] and len(parts) == 2 and parts[1] in ("aggregate", "tag", "decide", "document"):
                    if method != "POST":
                        raise DjorgeError("METHOD_NOT_ALLOWED", "use POST")
                    b = self._body()
                    fn = {"aggregate": self._aggregate, "tag": self._tag, "decide": self._decide,
                          "document": self._document}[parts[1]]
                    return self._send(200, fn(b))
                if parts and parts[0] == "contexts" and len(parts) >= 2:
                    return self._context(method, parts[1], parts[2:], u)
                return self._send(404, {"error": "NOT_FOUND"})
            except DjorgeError as e:
                return self._send(STATUS.get(e.code, 500), e.to_dict())
            except Exception as e:                      # never leak internals or inputs
                return self._send(500, {"error": "MODEL_ERROR", "message": type(e).__name__})

        def _sentinel(self, b):
            """POST /v1/sentinel {text, kind?, profile?, trechos?: "marcar"|"remover"}. Plain text only, same call
            style as /v1/classify: the question and labels are fixed by the kind (djorge/sentinel/presets.py), so the
            server asks exactly what the model was trained on. Nothing in the text is opened or fetched."""
            from .sentinel import presets as P
            text = self._text(b)
            kind = b.get("kind", "texto")
            trained = sentinel_kinds()
            if kind not in P.KINDS or (trained is not None and kind not in trained):
                ok = sorted(k for k in P.KINDS if trained is None or k in trained)
                raise DjorgeError("INVALID_REQUEST", f"'kind' must be one of {ok}")
            profile = b.get("profile", "equilibrado")
            if profile not in P.PROFILES:
                raise DjorgeError("INVALID_REQUEST", f"'profile' must be one of {sorted(P.PROFILES)}")
            k = P.KINDS[kind]
            q1 = classify_query(dict(zip(k["options"], k["descriptions"])), ask=k["ask"])
            q2 = score_query(dict(zip(P.DANGER_LEVELS, P.DANGER_DESC)), ask=P.DANGER_ASK)
            o1 = self._run(b, q1, sources={"text": text})
            o2 = self._run(b, q2, sources={"text": text})
            r1, r2 = o1.get("result", {}), o2.get("result", {})
            dist = r1.get("distribution") or {}
            tau = sentinel_thresholds().get(kind)
            lowered = False
            if tau and r1.get("answer") == k["safe"] and float(dist.get(k["safe"], 1.0)) < tau:
                risky_opts = {o: float(v) for o, v in dist.items() if o != k["safe"]}
                if risky_opts:                       # not sure enough it is safe -> report the most likely risk
                    best = max(risky_opts, key=risky_opts.get)
                    r1 = dict(r1, answer=best, probability=risky_opts[best]); lowered = True
            ab = bool(r1.get("abstain"))
            lvl = r2.get("answer", 0)
            lvl = P.DANGER_LEVELS.index(lvl) if isinstance(lvl, str) and lvl in P.DANGER_LEVELS else int(lvl or 0)
            prob = float(r1.get("probability") or 0.0)
            ans = None if ab else r1.get("answer")
            usage = dict(o1.get("usage") or {})
            for kk, v in (o2.get("usage") or {}).items():
                if isinstance(v, (int, float)) and isinstance(usage.get(kk), (int, float)):
                    usage[kk] = usage[kk] + v
            out = {"kind": kind, "answer": ans, "proposed": r1.get("answer"), "probability": r1.get("probability"),
                   "abstain": ab, "distribution": r1.get("distribution"),
                   "danger": {"level": P.DANGER_LEVELS[lvl], "score": P.DANGER_SCORE[lvl],
                              "probability": r2.get("probability")},
                   "action": P.action(kind, ans, lvl, prob, ab, profile), "profile": profile,
                   "model": r1.get("model"), "usage": usage}
            if lowered:
                out["safe_threshold"] = {"min_p_safe": tau, "p_safe": dist.get(k["safe"])}
            mode = b.get("trechos")
            if mode in ("marcar", "remover"):            # passage level: judge each sentence with the same question
                segs = P.segments(text)
                hits = []
                if len(segs) > 1:
                    reqs = [SemReq("CHOICE", text[a:e], options=k["options"], descriptions=k["descriptions"],
                                   ask=k["ask"]) for a, e in segs]
                    if not gate.acquire(timeout=2.0):
                        raise DjorgeError("BUSY", "server at capacity, retry shortly")
                    try:
                        res = _eng.run(reqs)
                    finally:
                        gate.release()
                    pmin = P.PROFILES[profile]["min_p"]
                    for (a, e), r in zip(segs, res):
                        if r.get("answer") != k["safe"] and float(r.get("probability") or 0) >= pmin:
                            hits.append({"start": a, "end": e, "text": text[a:e], "answer": r.get("answer"),
                                         "probability": r.get("probability")})
                elif ans is not None and ans != k["safe"]:
                    hits.append({"start": 0, "end": len(text), "text": text, "answer": ans, "probability": prob})
                out["trechos"] = hits
                out["texto_tratado"] = P.sanitize(text, [(h["start"], h["end"]) for h in hits], mode)
            if b.get("diagnostics") is True:
                out["full"] = {"kind": o1, "danger": o2}
            return out

        # ─────────────────────── Origo v2: AGG, etiquetagem, decisões compostas, documentos longos ───────────────────────
        def _sem(self, reqs):
            if _eng is None:
                raise DjorgeError("MODEL_UNAVAILABLE", "this endpoint needs the semantic engine")
            if not gate.acquire(timeout=2.0):
                raise DjorgeError("BUSY", "server at capacity, retry shortly")
            try:
                return _eng.run(reqs)
            finally:
                gate.release()

        def _aggregate(self, b):
            """POST /v1/aggregate {text, target, mode?: CLASS|ENTITY|ID, definition?} -> AGG result."""
            from .lang.quote import quote
            text = self._text(b)
            target = _req_str(b, "target", MAX_LABEL_CHARS)
            mode = b.get("mode", "CLASS")
            if mode not in ("CLASS", "ENTITY", "ID", "AUTO"):
                raise DjorgeError("INVALID_REQUEST", "'mode' must be CLASS, ENTITY, ID or AUTO")
            q = f"AGG {mode} {quote(target)} FROM text"
            d = _req_str(b, "definition", MAX_LABEL_CHARS, required=False, allow_empty=True)
            if d and mode in ("CLASS", "ENTITY", "AUTO"):
                q += f" DEFINE {quote(d)}"
            out = self._run(b, q, sources={"text": text})
            r = dict(out.get("result", {}))
            r["usage"] = out.get("usage")
            if out.get("sentinel"):
                r["sentinel"] = out["sentinel"]
            return r

        def _tag(self, b):
            """POST /v1/tag {text, labels: [..] | {label: description}, mode?: ENTITY|CLASS}
            -> {schema, entities:[{label, text, start, end, probability, polarity, quantity}]} with checked offsets."""
            from .runtime.semantic import SemReq
            from .runtime.agg import quantity_for
            text = self._text(b)
            labs = _labels(b, min_n=1)
            mode = b.get("mode", "ENTITY")
            if mode not in ("ENTITY", "CLASS"):
                raise DjorgeError("INVALID_REQUEST", "'mode' must be ENTITY or CLASS")
            outs = self._sem([SemReq("resolve", text, mode=mode, target=k, definition=v or None, threshold=0.5)
                              for k, v in labs.items()])
            ents, dropped = [], 0
            for lab, ms in zip(labs, outs):
                for m in ms.matches:
                    if not (0 <= m.start < m.end <= len(text)) or text[m.start:m.end] != m.text:
                        dropped += 1; continue
                    q = quantity_for(text, m.start)
                    ents.append({"label": lab, "text": m.text, "start": m.start, "end": m.end,
                                 "probability": m.probability,
                                 "polarity": (m.attributes or {}).get("polarity", "asserted"),
                                 "quantity": q["value"] if q["explicit"] else None,
                                 "unit": q["unit"] if q["explicit"] else None})
            ents.sort(key=lambda e: (e["start"], e["end"]))
            usage = meter.charge(self.client, count_tokens(text, *labs), "miss")
            return {"schema": "djorge.tag/1.0", "entities": ents, "invalid_dropped": dropped, "usage": usage}

        def _decide(self, b):
            """POST /v1/decide {text, rules:[...], min_p?, tenant?, route?} -> actions (never executed) + trace."""
            from .runtime import rules as RU
            from .runtime.semantic import SemReq
            text = self._text(b)
            try:
                labels = RU.validate(b.get("rules"))
            except RU.RuleError as e:
                raise DjorgeError("INVALID_REQUEST", str(e))
            min_p = b.get("min_p", 0.5)
            if not isinstance(min_p, (int, float)) or not 0 < min_p < 1:
                raise DjorgeError("INVALID_REQUEST", "'min_p' must be between 0 and 1")
            reqs = [SemReq("SET", text, options=list(labels), descriptions=list(labels.values()),
                           ask=_req_str(b, "question", MAX_QUESTION_CHARS, required=False, allow_empty=True) or "")]
            ents = []
            if b.get("route") is True and b.get("tenant") and z.contexts:
                ents = z.contexts.get(b["tenant"]).entities()
                if ents:
                    reqs.append(SemReq("SET", text, options=[e["name"] for e in ents],
                                       descriptions=[e.get("description", "") for e in ents],
                                       ask="Quem deve receber ou tratar esta mensagem?"))
            outs = self._sem(reqs)
            probs = outs[0].get("probabilities", {})
            res = RU.decide(b["rules"], probs, float(min_p))
            if ents:
                res["recipient"] = RU.route(ents, outs[1].get("probabilities", {}), float(min_p))
            res["usage"] = meter.charge(self.client, count_tokens(text, *labels), "miss")
            return res

        def _document(self, b):
            """POST /v1/document {text, labels?, question?, k?, chunk_chars?} -> hierarchy, consolidated entities,
            retrieval and (optional) a grounded answer with offsets in the ORIGINAL text."""
            from .runtime.hag import Document
            from .runtime.semantic import SemReq
            text = b.get("text")
            if not isinstance(text, str) or not text.strip():
                raise DjorgeError("INVALID_REQUEST", "'text' is required")
            text = clean_text(text)
            try:
                chunk_chars = int(b.get("chunk_chars", 700)); k = int(b.get("k", 4))
            except (TypeError, ValueError):
                raise DjorgeError("INVALID_REQUEST", "'chunk_chars' and 'k' must be integers")
            chunk_chars, k = max(200, min(chunk_chars, 2000)), max(1, min(k, 10))
            try:
                doc = Document(text, max_chars=chunk_chars)
            except ValueError as e:
                raise DjorgeError("RESOURCE_LIMIT", str(e))
            labs = _labels(b, min_n=1) if b.get("labels") else {}
            if labs:
                pieces = [text[c["start"]:c["end"]] for c in doc.chunks]
                reqs = [SemReq("resolve", pc, mode="ENTITY", target=lab, definition=d or None, threshold=0.5)
                        for pc in pieces for lab, d in labs.items()]
                outs = self._sem(reqs)
                found, i = {}, 0
                from .runtime.agg import quantity_for
                for ci, pc in enumerate(pieces):
                    fl = []
                    for lab in labs:
                        for m in outs[i].matches:
                            q = quantity_for(pc, m.start)
                            fl.append({"label": lab, "text": m.text, "start": m.start, "end": m.end,
                                       "probability": m.probability,
                                       "polarity": (m.attributes or {}).get("polarity", "asserted"),
                                       "quantity": q["value"] if q["explicit"] else None})
                        i += 1
                    found[pc] = fl
                doc.extract(lambda pc: found.get(pc, []))
            res = {"schema": "djorge.document/1.0", **doc.tree()}
            question = _req_str(b, "question", MAX_QUESTION_CHARS, required=False, allow_empty=True)
            if question:
                hits = doc.search(question, k)
                res["retrieved"] = [{kk: h[kk] for kk in ("chunk", "start", "end", "score")} for h in hits]
                if hits:
                    outs = self._sem([SemReq("answer", h["text"], target=question, threshold=0.3) for h in hits])
                    best = None
                    for h, ms in zip(hits, outs):
                        for m in ms.matches:
                            if best is None or m.probability > best["probability"]:
                                best = {"text": m.text, "start": h["start"] + m.start, "end": h["start"] + m.end,
                                        "probability": m.probability, "chunk": h["chunk"]}
                    if best and text[best["start"]:best["end"]] == best["text"]:
                        res["answer"] = best
                    else:
                        res["answer"] = None
            res["usage"] = meter.charge(self.client, count_tokens(text), "miss")
            return res

        def _tool(self, name, b):
            from .runtime import tools as T
            from .runtime.advice import lang_of
            if name in ("url", "email", "ip") and os.environ.get("DJORGE_SENTINEL", "1") == "0":
                return {"error": "NOT_FOUND", "tools": ["pii", "mask", "lang", "validate", "math", "time"]}
            if name in ("url", "email", "ip"):
                from .runtime import sentinel as SEN
                rep = getattr(z, "reputation", None)
                if name == "url":
                    r = SEN.analyze_url(_req_str(b, "url", 2000), rep)
                elif name == "email":
                    r = SEN.analyze_email_address(_req_str(b, "address", 320), rep, b.get("display", ""))
                else:
                    r = SEN.analyze_ip(_req_str(b, "ip", 64), rep)
                r["usage"] = meter.charge(self.client, 0, "miss", tool=True)
                return r
            text = _req_str(b, "text", lim.max_source_chars, required=name in ("pii", "mask", "lang"))
            try:
                if name == "pii":
                    out = {"found": T.find_pii(text)}
                elif name == "mask":
                    out = T.mask_pii(text)
                elif name == "lang":
                    out = {"lang": lang_of(text)}
                elif name == "validate":
                    out = T.validate(_req_str(b, "kind", 16), _req_str(b, "value", 512))
                elif name == "time":                       # local clock + every date in the text, resolved
                    from .runtime.temporal import annotate
                    nw = b.get("now") if isinstance(b.get("now"), str) and len(b.get("now")) <= 40 else None
                    out = annotate(text or "", nw)
                elif name == "math":
                    lc = b.get("locale", "pt")
                    out = T.safe_math(_req_str(b, "expression", 200), lc if lc in ("pt", "es", "en", "de", "fr") else "pt")
                else:
                    return {"error": "NOT_FOUND", "tools": ["pii", "mask", "lang", "validate", "math", "time", "url", "email", "ip"]}
            except ValueError as e:
                raise DjorgeError("INVALID_REQUEST", str(e))
            out["usage"] = meter.charge(self.client, 0, "miss", tool=True)
            return out

        def _select(self, b, u):
            """Direct answer by selection: returns ONLY the chosen option (options given) or the exact passage of the
            text that answers the question (no options). ?format=text -> plain text body with just the selection."""
            from .lang.quote import quote
            text = self._text(b)
            ask = _req_str(b, "question", MAX_QUESTION_CHARS, required="options" not in b, allow_empty=True) or ""
            if "options" in b:
                out = self._run(b, classify_query(_labels(b, "options"), ask=ask), sources={"text": text})
                r = out.get("result", {})
                sel = None if (r.get("abstain") or r.get("refused")) else r.get("answer")
                resp = {"selected": sel, "probability": r.get("probability"), "abstain": sel is None,
                        "proposed": r.get("proposed_value", r.get("answer"))}
            else:
                out = self._run(b, f"REPORT ANSWER {quote(ask)} FROM text", sources={"text": text})
                r = out.get("result", {})
                ms = sorted(r.get("matches") or [], key=lambda m: -(m.get("probability") or 0))
                top = ms[0] if ms and not r.get("refused") else None
                resp = {"selected": top["text"] if top else None, "probability": top.get("probability") if top else None,
                        "start": top.get("start") if top else None, "end": top.get("end") if top else None,
                        "abstain": top is None}
            for k in ("usage", "advice", "disclaimers", "safety_notice"):
                if out.get(k):
                    resp[k] = out[k]
            if r.get("refused"):
                resp.update(refused=True, message=r.get("message"), support=r.get("support"))
            fmt = (parse_qs(u.query).get("format") or [b.get("format", "json")])[0]
            if fmt == "text":
                return self._send_text(200, resp["selected"] or (resp.get("message") or ""),
                                       {"x-djorge-abstain": "1" if resp["abstain"] else "0",
                                        "x-djorge-probability": str(resp.get("probability"))})
            return self._send(200, resp)

        def _context(self, method, tenant, rest, u):
            if not TENANT.match(tenant):
                raise DjorgeError("INVALID_CONTEXT", "tenant ids are 1-64 chars of [A-Za-z0-9_-]")
            cs = z.contexts
            write = method in ("POST", "DELETE")
            if write and not self._is_admin():
                return self._send(403, {"error": "FORBIDDEN", "message": "context changes need the admin key"})
            if method == "POST" and not rest:
                return self._send(200, cs.create(tenant).data)
            if method == "GET" and not rest:
                v = parse_qs(u.query).get("version")
                try:
                    ver = int(v[0]) if v else None
                except ValueError:
                    raise DjorgeError("INVALID_REQUEST", "version must be an integer")
                return self._send(200, cs.get(tenant, ver).data)
            if rest == ["records"] and method == "POST":
                recs = self._body().get("records", [])
                if not isinstance(recs, list) or len(recs) > 5000:
                    raise DjorgeError("INVALID_REQUEST", "'records' must be a list of at most 5000 items")
                return self._send(200, {"version": cs.add(tenant, recs).version})
            if rest == ["records"] and method == "DELETE":
                ids = self._body().get("ids", [])
                if not isinstance(ids, list) or len(ids) > 5000:
                    raise DjorgeError("INVALID_REQUEST", "'ids' must be a list of at most 5000 items")
                return self._send(200, {"version": cs.remove(tenant, ids).version})
            if rest == ["versions"] and method == "GET":
                return self._send(200, {"versions": cs.versions(tenant)})
            if rest == ["export"] and method == "GET":
                return self._send(200, cs.export(tenant))
            if rest == ["import"] and method == "POST":
                return self._send(200, {"version": cs.import_(tenant, self._body()).version})
            if rest == ["clear"] and method == "POST":
                return self._send(200, {"version": cs.clear(tenant).version})
            if rest == ["adapt"] and method == "POST":
                if z.semantic is None:
                    raise DjorgeError("MODEL_UNAVAILABLE", "adaptation needs a loaded model")
                ok, retry = heavy.allow("adapt:" + tenant)
                if not ok:
                    return self._send(429, {"error": "RATE_LIMITED", "message": "adaptation is limited to 1 per minute"},
                                      {"retry-after": str(max(1, math.ceil(retry)))})
                try:
                    steps = int(self._body().get("steps", 150))
                except (TypeError, ValueError):
                    raise DjorgeError("INVALID_REQUEST", "'steps' must be an integer")
                from .training.adapt import adapt
                if not gate.acquire(timeout=2.0):
                    raise DjorgeError("BUSY", "server at capacity, retry shortly")
                try:
                    return self._send(200, adapt(z.semantic, cs, tenant, steps=min(max(steps, 1), 500)))
                finally:
                    gate.release()
            return self._send(404, {"error": "NOT_FOUND"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

        def do_PUT(self):
            self.rid = uuid.uuid4().hex[:16]; self.close_after = False
            self._send(405, {"error": "METHOD_NOT_ALLOWED"})

        do_PATCH = do_PUT
    return H


def prepare_for_serving(z: Djorge, batch_window_ms: float = 8.0, threads: int = 0, warmup: bool = True):
    """Production setup: CPU thread count, cross-request micro-batching, one warm-up pass
    (so the first real request does not pay allocation/JIT costs)."""
    import torch
    threads = threads or int(os.environ.get("DJORGE_THREADS", "0") or 0)
    if threads:
        torch.set_num_threads(threads)
    if z.semantic is not None and batch_window_ms > 0:
        from .runtime.batching import MicroBatcher
        mb = MicroBatcher(z.semantic, window_ms=batch_window_ms)
        z.semantic = mb
        z.executor.sem = mb
    if getattr(z.config, "cascade_artifact", None):          # load the stronger model now, not on the first unsure call
        prepare_for_serving(z.cascade_model(), batch_window_ms, threads, warmup)
    if warmup and z.semantic is not None:
        z.execute('COUNT CLASS Fruit FROM "warm up: one apple and a banana."')
        z.execute('CHOICE ["billing", "technical"] FROM "I was charged twice." ASK "Which team?"')
    return z


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


def serve(z: Djorge, host="127.0.0.1", port=8750, batch_window_ms: float = 8.0, threads: int = 0):
    prepare_for_serving(z, batch_window_ms, threads)
    srv = _Server((host, port), make_handler(z))
    auth = "on" if (_keys() or z.config.api_key) else "OFF (set DJORGE_API_KEYS before exposing this server)"
    print(f"DJORGE listening on http://{host}:{port}  (semantic engine: {'loaded' if z.semantic else 'not loaded'}, "
          f"micro-batching: {batch_window_ms} ms, auth: {auth})", flush=True)
    srv.serve_forever()
