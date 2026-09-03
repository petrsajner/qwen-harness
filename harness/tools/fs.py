"""Nástroje pro práci se soubory: list_dir, read_file, write_file, search_files."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from harness.changes import atomic_write_text, file_sha256
from harness.safety import Risk
from harness.tools.base import AgentContext, Tool, truncate

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "runtime", "sessions", ".mypy_cache", "dist", "build", ".idea", ".vscode"}


class ListDirTool(Tool):
    name = "list_dir"
    parallel_safe = True
    description = ("List directory contents. Returns subdirectories and files with sizes. "
                   "Use '.' for the workspace root. Ignores common junk dirs (.git, node_modules, venv...).")
    parameters = {
        "path": {"type": "string", "description": "Directory path (relative to workspace or absolute)"},
        "max_entries": {"type": "integer", "description": "Max entries to return (default 100)"},
    }

    def run(self, ctx: AgentContext, path: str = ".", max_entries: int = 100) -> str:
        d = ctx.resolve(path)
        if not d.exists():
            return f"ERROR: Path does not exist: {d}"
        if not d.is_dir():
            return f"ERROR: Not a directory: {d}"
        entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines = [f"{d}"]
        count = 0
        for p in entries:
            if p.name in IGNORED_DIRS:
                continue
            if count >= max_entries:
                lines.append(f"... ({len(entries) - count} more entries omitted)")
                break
            if p.is_dir():
                lines.append(f"  [DIR]  {p.name}/")
            else:
                lines.append(f"  {p.stat().st_size:>10,} B  {p.name}")
            count += 1
        return "\n".join(lines)


class ReadFileTool(Tool):
    name = "read_file"
    parallel_safe = True
    description = ("Read a text file. Returns content with line numbers (1-based). "
                   "Optionally read a line range for large files.")
    parameters = {
        "path": {"type": "string", "description": "File path (relative to workspace or absolute)"},
        "start_line": {"type": "integer", "description": "First line to read (1-based, optional)"},
        "end_line": {"type": "integer", "description": "Last line to read (inclusive, optional)"},
    }

    def run(self, ctx: AgentContext, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        f = ctx.resolve(path)
        if not f.exists():
            return f"ERROR: File not found: {f}"
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"ERROR: Cannot read file: {e}"
        lines = text.splitlines()
        total = len(lines)
        start = max(1, start_line or 1)
        end = min(total, end_line or total)
        if start > total:
            return f"ERROR: start_line {start} beyond file end ({total} lines)"
        numbered = "\n".join(f"{i:>5}| {lines[i - 1]}" for i in range(start, end + 1))
        header = f"{f} (lines {start}-{end} of {total})"
        return truncate(f"{header}\n{numbered}", limit=100_000)


def validate_syntax_pre_write(path: Path, content: str) -> str | None:
    """Ověří základní syntaktickou validitu před zápisem na disk."""
    suffix = path.suffix.lower()
    if suffix in (".py", ".pyi"):
        import ast
        try:
            ast.parse(content, filename=str(path))
        except SyntaxError as e:
            return f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}"
    elif suffix == ".json":
        import json
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return f"JSONDecodeError at line {e.lineno}, col {e.colno}: {e.msg}"
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
            yaml.safe_load(content)
        except Exception as e:
            return f"YAMLError: {e}"
    return None


class WriteFileTool(Tool):
    name = "write_file"
    description = ("Write/create a file with full content (overwrites existing). "
                   "Parent directories are created automatically.")
    parameters = {
        "path": {"type": "string", "description": "Target file path"},
        "content": {"type": "string", "description": "Full file content to write"},
    }
    required = ["path", "content"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, path: str, content: str) -> str:
        f = ctx.resolve(path)
        syntax_err = validate_syntax_pre_write(f, content)
        if syntax_err:
            return f"ERROR: Syntax validation failed before saving: {syntax_err}. File was not modified."
        existed = f.exists()
        if ctx.changes:
            ctx.changes.record_before(f)
        atomic_write_text(f, content)
        if ctx.changes:
            ctx.changes.record_after(f)
        return f"OK: {'overwritten' if existed else 'created'} {f} ({len(content):,} chars)"


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = ("Apply exact text replacements to one file atomically. Each edit must match "
                   "the expected number of occurrences; no partial change is written on failure.")
    parameters = {
        "path": {"type": "string", "description": "Target text file"},
        "edits": {
            "type": "array",
            "description": "Ordered exact replacements",
            "items": {
                "type": "object",
                "properties": {
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "expected_count": {"type": "integer", "minimum": 1},
                },
                "required": ["old", "new"],
            },
        },
        "expected_sha256": {"type": "string", "description": "Optional precondition hash"},
    }
    required = ["path", "edits"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, path: str, edits: list[dict[str, Any]],
            expected_sha256: str | None = None) -> str:
        target = ctx.resolve(path)
        if not target.is_file():
            return f"ERROR: File not found: {target}"
        current_hash = file_sha256(target)
        if expected_sha256 and current_hash != expected_sha256:
            return f"ERROR: File changed since it was read (expected {expected_sha256}, got {current_hash})"
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return f"ERROR: Cannot read file: {exc}"
        if not edits:
            return "ERROR: edits must not be empty"
        updated = text
        for index, edit in enumerate(edits, 1):
            old = str(edit.get("old", ""))
            new = str(edit.get("new", ""))
            expected = int(edit.get("expected_count", 1))
            if not old:
                return f"ERROR: edit {index} has empty old text"
            actual = updated.count(old)
            if actual != expected:
                return f"ERROR: edit {index} expected {expected} matches, found {actual}; file unchanged"
            updated = updated.replace(old, new, expected)
        if updated == text:
            return "OK: patch produced no content change"
        syntax_err = validate_syntax_pre_write(target, updated)
        if syntax_err:
            return f"ERROR: Syntax validation failed after applying patch: {syntax_err}. File was not modified."
        if ctx.changes:
            ctx.changes.record_before(target)
        atomic_write_text(target, updated)
        if ctx.changes:
            ctx.changes.record_after(target)
        return f"OK: patched {target} ({len(edits)} edits, sha256={file_sha256(target)})"


class ListTaskChangesTool(Tool):
    name = "list_task_changes"
    parallel_safe = True
    description = "List files changed in the current task checkpoint."
    parameters = {}

    def run(self, ctx: AgentContext) -> str:
        import json
        if not ctx.changes:
            return "ERROR: change journal unavailable"
        return json.dumps(ctx.changes.summary(), ensure_ascii=False, indent=2)


class UndoTaskChangesTool(Tool):
    name = "undo_task_changes"
    description = "Restore every file changed by the current task to its pre-task state."
    parameters = {}
    risk = Risk.WRITE

    def run(self, ctx: AgentContext) -> str:
        import json
        if not ctx.changes:
            return "ERROR: change journal unavailable"
        return json.dumps(ctx.changes.undo(), ensure_ascii=False, indent=2)


class SearchFilesTool(Tool):
    name = "search_files"
    parallel_safe = True
    description = ("Fast project text search powered by ripgrep when available. Returns "
                   "file:line:match, skips ignored/binary files, and supports literal or regex "
                   "queries plus a filename glob such as '*.py'.")
    parameters = {
        "query": {"type": "string", "description": "Text to search for (case-insensitive substring)"},
        "path": {"type": "string", "description": "Directory to search (default: workspace)"},
        "glob": {"type": "string", "description": "Filename filter glob (optional, e.g. '*.py')"},
        "max_results": {"type": "integer", "description": "Max matches (default 50)"},
        "regex": {"type": "boolean", "description": "Interpret query as regex (default false)"},
        "case_sensitive": {"type": "boolean", "description": "Case-sensitive search (default false)"},
    }
    required = ["query"]

    def run(self, ctx: AgentContext, query: str, path: str = ".", glob: str | None = None,
            max_results: int = 50, regex: bool = False,
            case_sensitive: bool = False) -> str:
        from harness.tools.fs import IGNORED_DIRS
        root = ctx.resolve(path)
        if not root.exists():
            return f"ERROR: Path does not exist: {root}"
        if not query:
            return "ERROR: query must not be empty"
        limit = max(1, min(int(max_results), 1000))
        rg = shutil.which("rg")
        if rg and root.is_dir():
            argv = [rg, "--line-number", "--no-heading", "--color", "never"]
            if not regex:
                argv.append("--fixed-strings")
            if not case_sensitive:
                argv.append("--ignore-case")
            if glob:
                argv.extend(["--glob", glob])
            argv.extend(["--", query, "."])
            try:
                proc = subprocess.run(
                    argv, cwd=root, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                lines = (proc.stdout or "").splitlines()
                if proc.returncode not in (0, 1):
                    return f"ERROR: ripgrep failed ({proc.returncode}): {(proc.stderr or '').strip()}"
                if not lines:
                    return f"No matches for {query!r} in {root}"
                suffix = f"\n... (limit {limit} reached)" if len(lines) > limit else ""
                return "\n".join(lines[:limit]) + suffix
            except (OSError, subprocess.TimeoutExpired):
                pass
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if regex else re.escape(query), flags)
        except re.error as exc:
            return f"ERROR: invalid regex: {exc}"
        results: list[str] = []
        files_scanned = 0
        for f in root.rglob(glob or "*"):
            if not f.is_file():
                continue
            if any(part in IGNORED_DIRS for part in f.parts):
                continue
            if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".zip", ".gz", ".gguf", ".exe", ".dll", ".pdf", ".bin", ".pyc"}:
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            files_scanned += 1
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    results.append(f"{f}:{i}: {line.strip()[:200]}")
                    if len(results) >= limit:
                        return "\n".join(results) + f"\n... (limit {limit} reached, {files_scanned} files scanned)"
        if not results:
            return f"No matches for '{query}' ({files_scanned} files scanned in {root})"
        return "\n".join(results) + f"\n({files_scanned} files scanned)"


class FindFilesTool(Tool):
    name = "find_files"
    parallel_safe = True
    description = ("List project files matching a glob, preferably through ripgrep's fast file "
                   "index. Example patterns: '**/*.py', 'src/**/*.ts', 'AGENTS.md'.")
    parameters = {
        "pattern": {"type": "string"},
        "path": {"type": "string", "description": "Search root (default workspace)"},
        "max_results": {"type": "integer", "description": "Maximum paths (default 200)"},
    }
    required = ["pattern"]

    def run(self, ctx: AgentContext, pattern: str, path: str = ".",
            max_results: int = 200) -> str:
        root = ctx.resolve(path)
        if not root.is_dir():
            return f"ERROR: Not a directory: {root}"
        limit = max(1, min(int(max_results), 2000))
        rg = shutil.which("rg")
        lines: list[str] = []
        if rg:
            try:
                proc = subprocess.run(
                    [rg, "--files", "--glob", pattern], cwd=root,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=30, creationflags=0x08000000 if os.name == "nt" else 0,
                )
                if proc.returncode not in (0, 1):
                    return f"ERROR: ripgrep failed ({proc.returncode}): {(proc.stderr or '').strip()}"
                lines = (proc.stdout or "").splitlines()
            except (OSError, subprocess.TimeoutExpired):
                lines = []
        if not rg:
            lines = [str(item.relative_to(root)) for item in root.glob(pattern)
                     if item.is_file() and not any(part in IGNORED_DIRS for part in item.parts)]
        if not lines:
            return f"No files match {pattern!r} in {root}"
        suffix = f"\n... (limit {limit} reached)" if len(lines) > limit else ""
        return "\n".join(lines[:limit]) + suffix


class MakeDirectoryTool(Tool):
    name = "make_directory"
    description = "Create a directory and any missing parent directories."
    parameters = {"path": {"type": "string"}}
    required = ["path"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, path: str) -> str:
        target = ctx.resolve(path)
        missing: list[Path] = []
        current = target
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        missing.reverse()
        if ctx.changes:
            for directory in missing:
                ctx.changes.record_directory_before(directory)
        target.mkdir(parents=True, exist_ok=True)
        if ctx.changes:
            for directory in missing:
                ctx.changes.record_directory_after(directory)
        return f"OK: directory ready {target}"


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move or rename one file. The operation is included in the task rollback journal."
    parameters = {"src": {"type": "string"}, "dst": {"type": "string"}}
    required = ["src", "dst"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, src: str, dst: str) -> str:
        source = ctx.resolve(src)
        target = ctx.resolve(dst)
        if not source.is_file():
            return f"ERROR: Source file not found: {source}"
        if target.exists() and target.is_dir():
            target = target / source.name
        if ctx.changes:
            ctx.changes.record_before(source)
            ctx.changes.record_before(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        if ctx.changes:
            ctx.changes.record_after(source)
            ctx.changes.record_after(target)
        return f"OK: moved {source} -> {target}"


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Delete one file. The file can be restored through the current task rollback."
    parameters = {"path": {"type": "string"}}
    required = ["path"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, path: str) -> str:
        target = ctx.resolve(path)
        if not target.is_file():
            return f"ERROR: File not found: {target}"
        if ctx.changes:
            ctx.changes.record_before(target)
        target.unlink()
        if ctx.changes:
            ctx.changes.record_after(target)
        return f"OK: deleted {target}"


def register_fs_tools(registry) -> None:
    registry.register(ListDirTool())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(ApplyPatchTool())
    registry.register(ListTaskChangesTool())
    registry.register(UndoTaskChangesTool())
    registry.register(SearchFilesTool())
    registry.register(FindFilesTool())
    registry.register(MakeDirectoryTool())
    registry.register(MoveFileTool())
    registry.register(DeleteFileTool())
