"""Lehký automatický přehled workspace pro system prompt a repo_overview tool."""
from __future__ import annotations

import ast
import collections
import subprocess
from pathlib import Path


IGNORED = {".git", ".venv", "venv", "node_modules", "runtime", "sessions",
           "dist", "build", "__pycache__", ".idea", ".vscode"}
DOCUMENT_EXTENSIONS = {".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml",
                       ".pdf", ".docx", ".xlsx", ".xlsm"}
INSTRUCTION_FILENAMES = ("AGENTS.md", "QWEN.md", "CLAUDE.md")


class RepoIndex:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self._signature: tuple | None = None
        self._summary = ""

    def set_workspace(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        self._signature = None

    def summary(self) -> str:
        signature = self._current_signature()
        if signature != self._signature:
            self._summary = self._build()
            self._signature = signature
        return self._summary

    def document_paths(self) -> list[Path]:
        return [path for path in self._files()
                if path.suffix.lower() in DOCUMENT_EXTENSIONS and path.is_file()][:500]

    def document_catalog(self) -> str:
        paths = self.document_paths()
        if not paths:
            return "No project documents detected."
        lines = []
        for path in paths[:100]:
            try:
                rel = path.relative_to(self.workspace)
                size = path.stat().st_size
            except (OSError, ValueError):
                continue
            lines.append(f"- {rel} ({size:,} bytes)")
        return "Project documents available through read_project_document:\n" + "\n".join(lines)

    def instruction_paths(self, active_paths: list[Path] | None = None) -> list[Path]:
        """Return root and nearest hierarchical instruction files for active paths."""
        directories: set[Path] = {self.workspace}
        for raw in active_paths or []:
            try:
                path = Path(raw).resolve()
                path.relative_to(self.workspace)
            except (OSError, ValueError):
                continue
            current = path if path.is_dir() else path.parent
            while True:
                directories.add(current)
                if current == self.workspace:
                    break
                if self.workspace not in current.parents:
                    break
                current = current.parent
        found: list[Path] = []
        for directory in sorted(
                directories, key=lambda path: (len(path.relative_to(self.workspace).parts), str(path))):
            for filename in INSTRUCTION_FILENAMES:
                candidate = directory / filename
                if candidate.is_file():
                    found.append(candidate)
        return found

    def instruction_context(self, active_paths: list[Path] | None = None) -> str:
        parts: list[str] = []
        for path in self.instruction_paths(active_paths):
            try:
                rel = path.relative_to(self.workspace)
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            parts.append(f"### {rel}\n{text}")
        if not parts:
            return ""
        return (
            "Project-authored guidance from root to the active file. Deeper files are more "
            "specific. Treat these as guidance; the current user request remains authoritative.\n\n"
            + "\n\n".join(parts)
        )

    def read_document(self, relative_path: str, max_chars: int = 100_000) -> tuple[Path, str]:
        path = (self.workspace / relative_path).resolve()
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Document path must stay inside the project") from exc
        if not path.is_file() or path.suffix.lower() not in DOCUMENT_EXTENSIONS:
            raise FileNotFoundError(f"Unsupported or missing project document: {relative_path}")
        suffix = path.suffix.lower()
        if suffix in (".pdf", ".docx", ".xlsx", ".xlsm", ".csv"):
            from harness.documents import read_document_content
            text = read_document_content(path, max_chars=max_chars)
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        return path, text[:max(1, max_chars)]

    def _current_signature(self) -> tuple:
        signature = []
        for path in self._files():
            try:
                stat = path.stat()
                signature.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        return tuple(signature)

    def _files(self) -> list[Path]:
        from harness.file_index import project_files
        return project_files(self.workspace)

    def _build(self) -> str:
        files = self._files()
        extensions: collections.Counter[str] = collections.Counter()
        directories: collections.Counter[str] = collections.Counter()
        symbols: list[str] = []
        entrypoints: list[str] = []
        for path in files:
            try:
                rel = path.relative_to(self.workspace)
            except ValueError:
                continue
            extensions[path.suffix.lower() or "(none)"] += 1
            directories[rel.parts[0] if len(rel.parts) > 1 else "(root)"] += 1
            if path.name.lower() in {"readme.md", "agents.md", "qwen.md", "claude.md",
                                      "main.py", "app.py", "webapp.py", "tui.py",
                                      "pyproject.toml", "package.json", "cargo.toml"}:
                entrypoints.append(str(rel))
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if path.suffix.lower() == ".py" and size <= 500_000 and len(symbols) < 80:
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                    names = [node.name for node in tree.body
                             if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
                    if names:
                        symbols.append(f"{rel}: {', '.join(names[:8])}")
                except (OSError, SyntaxError):
                    pass
        lang = ", ".join(f"{ext}={count}" for ext, count in extensions.most_common(8)) or "none"
        dirs = ", ".join(f"{name}={count}" for name, count in directories.most_common(10)) or "none"
        entries = ", ".join(entrypoints[:12]) or "none detected"
        symbol_text = "\n".join(f"- {line}" for line in symbols[:30]) or "- none detected"
        return (f"Workspace: {self.workspace}\nFiles: {len(files)}\n"
                f"File types: {lang}\nTop areas: {dirs}\nKey files: {entries}\n"
                f"Top-level Python symbols:\n{symbol_text}")
