"""Cross-request micro-batching for the API server.

Parallel HTTP requests each call SemanticEngine.run() with a handful of rows. Running them one after
another (the engine holds a lock) wastes the matrix units of a small CPU/GPU. The MicroBatcher sits in
front of the engine: calls arriving within `window_ms` of each other (same tenant adapter) are merged
into ONE engine.run() and the results are handed back to each caller. Results are identical to running
the calls separately (rows are independent; the engine already length-buckets inside a call).

Nothing is stored between batches: request state lives only until its result is returned.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional


class _Pending:
    __slots__ = ("reqs", "adapter", "event", "result", "error")

    def __init__(self, reqs, adapter):
        self.reqs, self.adapter = reqs, adapter
        self.event = threading.Event()
        self.result = self.error = None


class MicroBatcher:
    def __init__(self, engine, window_ms: float = 8.0, max_requests: int = 64, timeout_s: float = 60.0):
        self.engine = engine
        self.timeout_s = timeout_s
        self.window = window_ms / 1000.0
        self.max_requests = max_requests
        self._q: List[_Pending] = []
        self._cv = threading.Condition()
        self.batches = 0
        self.merged_calls = 0
        threading.Thread(target=self._loop, name="djorge-microbatch", daemon=True).start()

    def __getattr__(self, name):                 # meta, stats, model_id, ensure_adapter, ... -> engine
        return getattr(self.engine, name)

    def run(self, reqs, tenant_adapter: Optional[str] = None, timings: Optional[dict] = None):
        if not reqs:
            return []
        p = _Pending(list(reqs), tenant_adapter)
        with self._cv:
            self._q.append(p)
            self._cv.notify()
        if not p.event.wait(timeout=self.timeout_s):
            from ..errors import DjorgeError
            raise DjorgeError("TIMEOUT", f"semantic engine did not answer within {self.timeout_s:.0f}s")
        if p.error is not None:
            raise p.error
        if timings is not None:
            timings["microbatch"] = True
        return p.result

    def _loop(self):
        while True:
            with self._cv:
                while not self._q:
                    self._cv.wait()
            time.sleep(self.window)                # let concurrent requests arrive
            with self._cv:
                first = self._q[0].adapter
                group = [p for p in self._q if p.adapter == first][: self.max_requests]
                for p in group:
                    self._q.remove(p)
            allreqs = [r for p in group for r in p.reqs]
            try:
                out = self.engine.run(allreqs, tenant_adapter=first)
                i = 0
                for p in group:
                    p.result = out[i:i + len(p.reqs)]
                    i += len(p.reqs)
            except Exception as e:                 # every waiting caller gets the error, nobody hangs
                for p in group:
                    p.error = e
            self.batches += 1
            self.merged_calls += len(group)
            for p in group:
                p.event.set()
