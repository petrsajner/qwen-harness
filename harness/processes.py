"""Správa dlouhých shell procesů s průběžným výstupem a ukončením."""
from __future__ import annotations

import collections
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


MAX_BUFFER_CHARS = 1_000_000


def shell_argv(command: str, shell: str) -> list[str]:
    if shell == "powershell":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    if shell == "cmd":
        return ["cmd", "/c", command]
    from harness.tools.shell import find_bash
    bash = find_bash()
    if not bash:
        raise FileNotFoundError("bash not found; use shell='powershell'")
    return [bash, "-lc", command]


@dataclass
class ManagedProcess:
    id: str
    command: str
    shell: str
    cwd: str
    proc: subprocess.Popen
    timeout: int
    started: float = field(default_factory=time.time)
    chunks: collections.deque[str] = field(default_factory=collections.deque)
    base_cursor: int = 0
    end_cursor: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, text: str) -> None:
        with self.lock:
            self.chunks.append(text)
            self.end_cursor += len(text)
            while self.end_cursor - self.base_cursor > MAX_BUFFER_CHARS and self.chunks:
                removed = self.chunks.popleft()
                self.base_cursor += len(removed)

    def output_since(self, cursor: int, max_chars: int) -> tuple[str, int, bool]:
        with self.lock:
            truncated = cursor < self.base_cursor
            cursor = max(cursor, self.base_cursor)
            position = self.base_cursor
            parts: list[str] = []
            remaining = max(1, max_chars)
            for chunk in self.chunks:
                chunk_end = position + len(chunk)
                if chunk_end > cursor and remaining > 0:
                    piece = chunk[max(0, cursor - position):][:remaining]
                    parts.append(piece)
                    remaining -= len(piece)
                position = chunk_end
                if remaining <= 0:
                    break
            output = "".join(parts)
            return output, cursor + len(output), truncated


class ProcessManager:
    def __init__(self):
        self._items: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()

    def start(self, command: str, shell: str, cwd: Path, timeout: int) -> ManagedProcess:
        argv = shell_argv(command, shell)
        flags = 0
        if sys.platform == "win32":
            flags = 0x08000000 | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1, creationflags=flags,
        )
        item = ManagedProcess(uuid.uuid4().hex[:8], command, shell, str(cwd), proc, timeout)
        with self._lock:
            self._prune()
            self._items[item.id] = item
        threading.Thread(target=self._reader, args=(item,), daemon=True,
                         name=f"process-output-{item.id}").start()
        return item

    def poll(self, process_id: str, cursor: int = 0, max_chars: int = 20_000) -> dict:
        item = self.get(process_id)
        if item is None:
            return {"error": f"unknown process_id '{process_id}'"}
        if item.proc.poll() is None and item.timeout > 0 \
                and time.time() - item.started > item.timeout:
            self.terminate(process_id)
            item.append(f"\n[process timed out after {item.timeout}s]\n")
        output, next_cursor, truncated = item.output_since(
            max(0, int(cursor)), max(100, min(int(max_chars), 100_000)))
        code = item.proc.poll()
        return {
            "process_id": item.id,
            "status": "running" if code is None else "finished",
            "exit_code": code,
            "cursor": next_cursor,
            "output": output,
            "output_truncated_before_cursor": truncated,
            "elapsed_seconds": round(time.time() - item.started, 1),
        }

    def send_stdin(self, process_id: str, text: str) -> dict:
        item = self.get(process_id)
        if item is None:
            return {"error": f"unknown process_id '{process_id}'"}
        if item.proc.poll() is not None or item.proc.stdin is None:
            return {"error": "process is not accepting input"}
        try:
            item.proc.stdin.write(text)
            item.proc.stdin.flush()
            return {"process_id": process_id, "written_chars": len(text)}
        except OSError as exc:
            return {"error": str(exc)}

    def terminate(self, process_id: str) -> dict:
        item = self.get(process_id)
        if item is None:
            return {"error": f"unknown process_id '{process_id}'"}
        if item.proc.poll() is not None:
            return {"process_id": process_id, "already_finished": True,
                    "exit_code": item.proc.returncode}
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(item.proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=0x08000000, timeout=10)
            else:
                item.proc.terminate()
            try:
                item.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                item.proc.kill()
            return {"process_id": process_id, "terminated": True,
                    "exit_code": item.proc.poll()}
        except OSError as exc:
            return {"error": str(exc)}

    def list(self) -> list[dict]:
        with self._lock:
            items = list(self._items.values())
        return [
            {
                "process_id": item.id,
                "command": item.command,
                "status": "running" if item.proc.poll() is None else "finished",
                "exit_code": item.proc.poll(),
                "elapsed_seconds": round(time.time() - item.started, 1),
            }
            for item in items
        ]

    def terminate_all(self) -> list[dict]:
        with self._lock:
            running = [key for key, item in self._items.items() if item.proc.poll() is None]
        return [self.terminate(key) for key in running]

    def get(self, process_id: str) -> ManagedProcess | None:
        with self._lock:
            return self._items.get(process_id)

    @staticmethod
    def _reader(item: ManagedProcess) -> None:
        if item.proc.stdout is None:
            return
        try:
            for line in iter(item.proc.stdout.readline, ""):
                if not line:
                    break
                item.append(line)
        finally:
            item.proc.stdout.close()

    def _prune(self) -> None:
        finished = [key for key, item in self._items.items() if item.proc.poll() is not None]
        for key in finished[:-10]:
            self._items.pop(key, None)
