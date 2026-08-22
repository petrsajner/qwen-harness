"""CLI wrapper pro správu llama-serveru (logika v harness/servermgmt.py).

Použití:
    python scripts/server.py start [--model q4|q5] [--ctx N]
    python scripts/server.py stop | restart | status
    python scripts/server.py switch q5
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.config import load_config  # noqa: E402
from harness import servermgmt  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["start", "stop", "restart", "switch", "status"])
    ap.add_argument("model", nargs="?", help="model key (q4/q5) pro start/switch/restart")
    ap.add_argument("--ctx", type=int, help="override ctx size")
    args = ap.parse_args()
    cfg = load_config()

    if args.cmd == "start":
        return servermgmt.start(cfg, args.model, args.ctx)
    if args.cmd == "stop":
        servermgmt.stop(cfg)
        return 0
    if args.cmd == "restart":
        servermgmt.stop(cfg, quiet=True)
        time.sleep(1)
        return servermgmt.start(cfg, args.model, args.ctx)
    if args.cmd == "switch":
        if not args.model:
            print("Použití: server.py switch <q4|q5>")
            return 1
        servermgmt.stop(cfg, quiet=True)
        time.sleep(1)
        return servermgmt.start(cfg, args.model, args.ctx)
    if args.cmd == "status":
        return servermgmt.status(cfg)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
