"""Thread-safe bridge mezi blokujícím agentem a streamujícím UI."""
from __future__ import annotations

import threading
import time
from typing import Any


class StreamHub:
    """Sbírá tokenové a tool události z worker vlákna pro průběžný render."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.rev = 0
        self.last_activity = time.time()

    def reset(self) -> None:
        with self._lock:
            self.text = []
            self.reasoning = []
            self.rev += 1
            self.last_activity = time.time()

    def on_event(self, kind: str, payload: Any) -> None:
        with self._lock:
            if kind == "text" and payload:
                self.text.append(str(payload))
                self.rev += 1
                self.last_activity = time.time()
            elif kind == "reasoning" and payload:
                self.reasoning.append(str(payload))
                self.rev += 1
                self.last_activity = time.time()
            elif kind in ("tool_start", "tool_result"):
                self.last_activity = time.time()

    def snapshot(self) -> tuple[str, str, int, float]:
        with self._lock:
            return "".join(self.text), "".join(self.reasoning), self.rev, self.last_activity


def step_threaded(agent, approve: bool | None):
    """Spustí jeden agent.step ve worker vlákně a vrátí thread + result box."""
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["r"] = agent.step(approve=approve)
        except BaseException as exc:  # výjimku zpracuje UI vlákno
            box["e"] = exc

    thread = threading.Thread(target=worker, daemon=True, name="agent-step")
    thread.start()
    return thread, box
