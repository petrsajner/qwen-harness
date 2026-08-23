"""Thread-safe bridge mezi blokujícím agentem a streamujícím UI."""
from __future__ import annotations

import threading
import time
from typing import Any


class SteeringQueue:
    """Thread-safe queue of user clarifications for the active agent run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[str, list[str]]] = []

    def push(self, text: str, files: list[str] | None = None) -> None:
        with self._lock:
            self._items.append((text, list(files or [])))

    def pop_all(self) -> list[tuple[str, list[str]]]:
        with self._lock:
            items, self._items = self._items, []
            return items

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._items)


class StreamHub:
    """Sbírá tokenové a tool události z worker vlákna pro průběžný render."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.tool_call_name = ""
        self.tool_call_chars = 0
        self.tool_call_preview = ""
        self.tools_running: list[tuple[str, Any]] = []
        self.last_tool = ""
        self.rev = 0
        self.last_activity = time.time()

    def reset(self) -> None:
        with self._lock:
            self.text = []
            self.reasoning = []
            self.tool_call_name = ""
            self.tool_call_chars = 0
            self.tool_call_preview = ""
            self.tools_running = []
            self.last_tool = ""
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
            elif kind == "tool_delta" and payload:
                name, arguments = payload
                self.tool_call_name += str(name or "")
                argument_text = str(arguments or "")
                self.tool_call_chars += len(argument_text)
                if len(self.tool_call_preview) < 2000:
                    room = 2000 - len(self.tool_call_preview)
                    self.tool_call_preview += argument_text[:room]
                self.rev += 1
                self.last_activity = time.time()
            elif kind == "tool_start" and payload:
                name = str(payload[0])
                self.tool_call_name = ""
                self.tool_call_chars = 0
                self.tool_call_preview = ""
                self.tools_running.append((name, payload[1]))
                self.last_tool = name
                self.rev += 1
                self.last_activity = time.time()
            elif kind == "tool_result" and payload:
                name = str(payload[0])
                running = next((item for item in self.tools_running if item[0] == name), None)
                if running is not None:
                    self.tools_running.remove(running)
                self.last_tool = name
                self.rev += 1
                self.last_activity = time.time()

    def snapshot(self) -> tuple[str, str, int, float]:
        with self._lock:
            return "".join(self.text), "".join(self.reasoning), self.rev, self.last_activity

    def progress(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tool_call_name": self.tool_call_name,
                "tool_call_chars": self.tool_call_chars,
                "tool_call_preview": self.tool_call_preview,
                "tools_running": list(self.tools_running),
                "last_tool": self.last_tool,
                "generated_chars": (
                    sum(map(len, self.text)) + sum(map(len, self.reasoning))
                    + self.tool_call_chars
                ),
            }


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
