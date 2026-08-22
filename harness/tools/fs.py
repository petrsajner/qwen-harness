"""Nástroje pro práci se soubory: list_dir, read_file, write_file, search_files."""
from __future__ import annotations

from pathlib import Path

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool, truncate

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "runtime", "sessions", ".mypy_cache", "dist", "build", ".idea", ".vscode"}


class ListDirTool(Tool):
    name = "list_dir"
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
        f.parent.mkdir(parents=True, exist_ok=True)
        existed = f.exists()
        f.write_text(content, encoding="utf-8", newline="\n")
        return f"OK: {'overwritten' if existed else 'created'} {f} ({len(content):,} chars)"


class SearchFilesTool(Tool):
    name = "search_files"
    description = ("Search for a text string (substring, case-insensitive) in files under a directory. "
                   "Returns file:line: match. Skips binary files and junk dirs. "
                   "glob filters filenames, e.g. '*.py'.")
    parameters = {
        "query": {"type": "string", "description": "Text to search for (case-insensitive substring)"},
        "path": {"type": "string", "description": "Directory to search (default: workspace)"},
        "glob": {"type": "string", "description": "Filename filter glob (optional, e.g. '*.py')"},
        "max_results": {"type": "integer", "description": "Max matches (default 50)"},
    }
    required = ["query"]

    def run(self, ctx: AgentContext, query: str, path: str = ".", glob: str | None = None, max_results: int = 50) -> str:
        from harness.tools.fs import IGNORED_DIRS
        root = ctx.resolve(path)
        if not root.exists():
            return f"ERROR: Path does not exist: {root}"
        needle = query.lower()
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
                if needle in line.lower():
                    results.append(f"{f}:{i}: {line.strip()[:200]}")
                    if len(results) >= max_results:
                        return "\n".join(results) + f"\n... (limit {max_results} reached, {files_scanned} files scanned)"
        if not results:
            return f"No matches for '{query}' ({files_scanned} files scanned in {root})"
        return "\n".join(results) + f"\n({files_scanned} files scanned)"


def register_fs_tools(registry) -> None:
    registry.register(ListDirTool())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(SearchFilesTool())
