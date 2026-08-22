"""GPU E2E: asynchronní start Q4 a skutečné přepnutí na Q5."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import servermgmt
from harness.config import load_config
from harness.model_switch import ModelSwitchController


def wait_ready(controller: ModelSwitchController, key: str) -> None:
    if not controller.wait(timeout=servermgmt.HEALTH_TIMEOUT + 10):
        raise RuntimeError(f"Timeout background přepnutí na {key}")
    snapshot = controller.snapshot()
    if snapshot.status != "ready":
        raise RuntimeError(f"Přepnutí na {key} selhalo: {snapshot.error}")
    if not servermgmt.health(controller.cfg) or servermgmt.running_model(controller.cfg) != key:
        raise RuntimeError(f"Server po přepnutí nehlásí model {key}")


def request_and_check_nonblocking(controller: ModelSwitchController, key: str) -> float:
    started = time.monotonic()
    if not controller.request(key):
        raise RuntimeError(f"Controller odmítl přepnutí na {key}")
    elapsed = time.monotonic() - started
    if elapsed >= 1.0 or not controller.snapshot().busy:
        raise RuntimeError(f"Request na {key} nebyl neblokující ({elapsed:.2f}s)")
    return elapsed


def main() -> int:
    cfg = load_config()
    controller = ModelSwitchController(cfg)
    try:
        for key in ("q4", "q5"):
            request_time = request_and_check_nonblocking(controller, key)
            switch_started = time.monotonic()
            wait_ready(controller, key)
            print(f"[OK] {key}: request {request_time:.3f}s, ready {time.monotonic() - switch_started:.1f}s")
        print("[OK] Asynchronní Q4 → Q5 switch prošel.")
        return 0
    finally:
        servermgmt.stop(cfg, quiet=True)


if __name__ == "__main__":
    raise SystemExit(main())
