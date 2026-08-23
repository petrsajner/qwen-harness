"""Správa dlouhých shell procesů s průběžným výstupem a ukončením."""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import tempfile
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
    proc: subprocess.Popen | None
    timeout: int
    started: float = field(default_factory=time.time)
    chunks: collections.deque[str] = field(default_factory=collections.deque)
    base_cursor: int = 0
    end_cursor: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    pid: int = 0
    log_path: Path | None = None
    meta_path: Path | None = None
    exit_code: int | None = None

    def __post_init__(self) -> None:
        if self.proc is not None:
            self.pid = self.proc.pid

    def append(self, text: str) -> None:
        with self.lock:
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8", errors="replace") as handle:
                    handle.write(text)
            else:
                self.chunks.append(text)
                self.end_cursor += len(text)

    def output_since(self, cursor: int, max_chars: int) -> tuple[str, int, bool]:
        with self.lock:
            if self.log_path and self.log_path.exists():
                text = self.log_path.read_text(encoding="utf-8", errors="replace")
                base = max(0, len(text) - MAX_BUFFER_CHARS)
                truncated = cursor < base
                cursor = max(cursor, base)
                output = text[cursor:cursor + max(1, max_chars)]
                return output, cursor + len(output), truncated
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
        self._storage_dir: Path | None = None

    def bind_session(self, session) -> None:
        self._storage_dir = session.dir / "processes"
        if not self._storage_dir.exists():
            return
        for meta_path in self._storage_dir.glob("*.json"):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                process_id = str(data["process_id"])
                if process_id in self._items:
                    continue
                self._items[process_id] = ManagedProcess(
                    process_id, str(data["command"]), str(data["shell"]),
                    str(data["cwd"]), None, int(data.get("timeout", 0)),
                    started=float(data.get("started", time.time())),
                    pid=int(data.get("pid", 0)),
                    log_path=Path(data["log_path"]), meta_path=meta_path,
                    exit_code=data.get("exit_code"),
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def start(self, command: str, shell: str, cwd: Path, timeout: int) -> ManagedProcess:
        argv = shell_argv(command, shell)
        flags = 0
        if sys.platform == "win32":
            flags = 0x08000000 | subprocess.CREATE_NEW_PROCESS_GROUP
        process_id = uuid.uuid4().hex[:8]
        storage = self._storage_dir or Path(tempfile.gettempdir()) / "qwen-processes"
        storage.mkdir(parents=True, exist_ok=True)
        log_path = storage / f"{process_id}.log"
        meta_path = storage / f"{process_id}.json"
        log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=log_handle,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=flags,
            )
        finally:
            log_handle.close()
        item = ManagedProcess(process_id, command, shell, str(cwd), proc, timeout,
                              log_path=log_path, meta_path=meta_path)
        with self._lock:
            self._prune()
            self._items[item.id] = item
        self._persist(item)
        return item

    def poll(self, process_id: str, cursor: int = 0, max_chars: int = 20_000) -> dict:
        item = self.get(process_id)
        if item is None:
            return {"error": f"unknown process_id '{process_id}'"}
        if self._returncode(item) is None and item.timeout > 0 \
                and time.time() - item.started > item.timeout:
            self.terminate(process_id)
            item.append(f"\n[process timed out after {item.timeout}s]\n")
        output, next_cursor, truncated = item.output_since(
            max(0, int(cursor)), max(100, min(int(max_chars), 100_000)))
        code = self._returncode(item)
        self._persist(item)
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
        if item.proc is None:
            return {"error": "stdin is unavailable after application restart"}
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
        code = self._returncode(item)
        if code is not None:
            return {"process_id": process_id, "already_finished": True,
                    "exit_code": code}
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(item.pid), "/T", "/F"],
                               capture_output=True, creationflags=0x08000000, timeout=10)
            elif item.proc is not None:
                item.proc.terminate()
            if item.proc is not None:
                try:
                    item.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    item.proc.kill()
                item.exit_code = item.proc.poll()
            else:
                item.exit_code = -15
            self._persist(item)
            return {"process_id": process_id, "terminated": True,
                    "exit_code": item.exit_code}
        except OSError as exc:
            return {"error": str(exc)}

    def list(self) -> list[dict]:
        with self._lock:
            items = list(self._items.values())
        return [
            {
                "process_id": item.id,
                "command": item.command,
                "status": "running" if self._returncode(item) is None else "finished",
                "exit_code": self._returncode(item),
                "elapsed_seconds": round(time.time() - item.started, 1),
            }
            for item in items
        ]

    def terminate_all(self) -> list[dict]:
        with self._lock:
            running = [key for key, item in self._items.items()
                       if self._returncode(item) is None]
        return [self.terminate(key) for key in running]

    def get(self, process_id: str) -> ManagedProcess | None:
        with self._lock:
            return self._items.get(process_id)

    def _prune(self) -> None:
        finished = [key for key, item in self._items.items()
                    if self._returncode(item) is not None]
        for key in finished[:-10]:
            self._items.pop(key, None)

    @staticmethod
    def _returncode(item: ManagedProcess) -> int | None:
        if item.proc is not None:
            code = item.proc.poll()
            if code is not None:
                item.exit_code = code
            return code
        if item.pid:
            try:
                import psutil
                process = psutil.Process(item.pid)
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    return None
            except Exception:
                pass
        return item.exit_code if item.exit_code is not None else -1

    @staticmethod
    def _persist(item: ManagedProcess) -> None:
        if item.meta_path is None:
            return
        data = {
            "process_id": item.id,
            "command": item.command,
            "shell": item.shell,
            "cwd": item.cwd,
            "timeout": item.timeout,
            "started": item.started,
            "pid": item.pid,
            "log_path": str(item.log_path),
            "exit_code": item.exit_code,
        }
        temporary = item.meta_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, item.meta_path)
