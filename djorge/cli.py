"""DJORGE command line.

  djorge "COUNT ID Arthur FROM \"Arthur foi ao mercado.\""
  djorge --artifact artifacts/djorge-qwen3-0.6b 'REPORT CLASS Fruit FROM source' --source "..."
  djorge --json ...        djorge --explain ...
  djorge serve [--host 0.0.0.0 --port 8750]
  djorge capabilities
  djorge context <tenant> add file.json | show | versions | export | clear
  djorge adapt <tenant>
"""
from __future__ import annotations

import argparse
import json
import sys

from . import Djorge, DjorgeConfig
from .errors import DjorgeError


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="djorge")
    ap.add_argument("--artifact")
    ap.add_argument("--config")
    ap.add_argument("--device")
    ap.add_argument("--source")
    ap.add_argument("--tenant")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8750)
    ap.add_argument("--batch-window-ms", type=float, default=8.0, help="cross-request micro-batching window (0 = off)")
    ap.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = torch default)")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    cfg = DjorgeConfig.load(a.config)
    if a.device:
        cfg.device = a.device
    try:
        z = Djorge(artifact=a.artifact, config=cfg)
        cmd = a.args[0] if a.args else ""
        if cmd == "serve":
            from .server import serve
            serve(z, a.host, a.port, a.batch_window_ms, a.threads)
        elif cmd == "capabilities":
            print(json.dumps(z.capabilities(), indent=2))
        elif cmd == "context":
            tenant, op = a.args[1], a.args[2]
            cs = z.contexts
            if op == "add":
                recs = json.load(open(a.args[3]))
                print(json.dumps({"version": cs.add(tenant, recs if isinstance(recs, list) else recs["records"]).version}))
            elif op == "show":
                print(json.dumps(cs.get(tenant).data, indent=2, ensure_ascii=False))
            elif op == "versions":
                print(json.dumps(cs.versions(tenant), indent=2))
            elif op == "export":
                print(json.dumps(cs.export(tenant), ensure_ascii=False))
            elif op == "clear":
                print(json.dumps({"version": cs.clear(tenant).version}))
        elif cmd == "adapt":
            from .training.adapt import adapt
            print(json.dumps(adapt(z.semantic, z.contexts, a.args[1]), indent=2))
        elif cmd:
            q = " ".join(a.args)
            r = z.execute(q, source=a.source, tenant=a.tenant)
            if a.json:
                print(json.dumps(r, indent=2, ensure_ascii=False))
            elif a.explain:
                print(json.dumps(r["meta"], indent=2))
            else:
                s = r["surface"]
                print(s if isinstance(s, str) else "\n".join(f"{k}: {v}" for k, v in s.items()))
        else:
            ap.print_help()
    except DjorgeError as e:
        print(json.dumps(e.to_dict(), ensure_ascii=False), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
