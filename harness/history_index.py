"""SQLite FTS index uživatelských a asistentských zpráv napříč sessions."""
from __future__ import annotations

import json
from contextlib import contextmanager
import sqlite3

from harness.i18n import t
from pathlib import Path


class HistoryIndex:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.path = sessions_dir / "history-index.sqlite3"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "session_id TEXT PRIMARY KEY, title TEXT, workspace TEXT, updated REAL, indexed_mtime REAL)"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
                "session_id UNINDEXED, role UNINDEXED, content, tokenize='unicode61')"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
            if "indexed_count" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN indexed_count INTEGER DEFAULT 0")

    def reindex(self, session_id: str, meta: dict, messages: list[dict],
                source_mtime: float | None = None, incremental: bool = False) -> None:
        rows = []
        start = 0
        if incremental:
            with self._connect() as connection:
                previous = connection.execute("SELECT indexed_count FROM sessions WHERE session_id=?",
                                              (session_id,)).fetchone()
                start = int(previous[0] or 0) if previous else 0
                if start > len(messages):
                    start = 0
        internal = ("[TASK PROTOCOL", "[WRITING PROTOCOL", "[PROGRESS UPDATE",
                    "[FINAL SUMMARY", "[WRITING SUMMARY", "[RESEARCH PLAN",
                    "[The following image", "[Interrupted by user]")
        for message in messages[start:]:
            role = message.get("role")
            content = message.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            if role == "user" and content.startswith(internal):
                continue
            rows.append((session_id, role, content))
        with self._connect() as connection:
            if not incremental or start == 0:
                connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (session_id,))
            connection.executemany(
                "INSERT INTO messages_fts(session_id, role, content) VALUES (?, ?, ?)", rows)
            connection.execute(
                "INSERT INTO sessions(session_id, title, workspace, updated, indexed_mtime) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "title=excluded.title, workspace=excluded.workspace, updated=excluded.updated, "
                "indexed_mtime=excluded.indexed_mtime",
                (session_id, meta.get("title") or t("(untitled)"), meta.get("workspace"),
                 float(meta.get("updated") or 0), float(source_mtime or 0)),
            )
            connection.execute("UPDATE sessions SET indexed_count=? WHERE session_id=?",
                               (len(messages), session_id))

    def remove(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def sync_existing(self) -> None:
        with self._connect() as connection:
            known = {row[0]: float(row[1] or 0) for row in connection.execute(
                "SELECT session_id, indexed_mtime FROM sessions")}
        for directory in self.sessions_dir.iterdir():
            jsonl = directory / "messages.jsonl"
            if not directory.is_dir() or not jsonl.is_file():
                continue
            mtime = jsonl.stat().st_mtime
            if known.get(directory.name, -1) >= mtime:
                continue
            try:
                messages = [json.loads(line) for line in jsonl.read_text(
                    encoding="utf-8", errors="replace").splitlines() if line.strip()]
                meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self.reindex(directory.name, meta, messages, source_mtime=mtime)

    def search(self, query: str, limit: int = 30) -> list[dict]:
        self.sync_existing()
        phrase = '"' + query.replace('"', '""') + '"'
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT s.session_id, s.title, s.workspace, s.updated, "
                "snippet(messages_fts, 2, '', '', '…', 24) "
                "FROM messages_fts JOIN sessions s ON s.session_id = messages_fts.session_id "
                "WHERE messages_fts MATCH ? ORDER BY s.updated DESC LIMIT ?",
                (phrase, max(1, min(int(limit), 100))),
            ).fetchall()
        seen = set()
        results = []
        for session_id, title, workspace, updated, snippet in rows:
            if session_id in seen:
                continue
            seen.add(session_id)
            results.append({"id": session_id, "title": title, "workspace": workspace,
                            "updated": updated, "snippet": " ".join((snippet or "").split())[:180]})
        return results
