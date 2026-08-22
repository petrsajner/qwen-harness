"""Lehký automatický přehled workspace pro system prompt a repo_overview tool."""
from __future__ import annotations

import ast
import collections
import subprocess
from pathlib import Path


IGNORED = {".git", ".venv", "venv", "node_modules", "runtime", "sessions",
           "dist", "build", "__pycache__", ".idea", ".vscode"}
DOCUMENT_EXTENSIONS = {".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml",
                       ".pdf", ".docx"}


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

    def read_document(self, relative_path: str, max_chars: int = 100_000) -> tuple[Path, str]:
        path = (self.workspace / relative_path).resolve()
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Document path must stay inside the project") from exc
        if not path.is_file() or path.suffix.lower() not in DOCUMENT_EXTENSIONS:
            raise FileNotFoundError(f"Unsupported or missing project document: {relative_path}")
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            from pypdf import PdfReader
            text = "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        elif suffix == ".docx":
            from docx import Document
            document = Document(str(path))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        return path, text[:max(1, max_chars)]

    def _current_signature(self) -> tuple:
        try:
            stat = self.workspace.stat()
            git_index = self.workspace / ".git" / "index"
            git_state = ""
            if git_index.exists():
                proc = subprocess.run(
                    ["git", "status", "--porcelain"], cwd=self.workspace,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=5, creationflags=0x08000000,
                )
                git_state = proc.stdout
            else:
                latest_mtime = 0
                file_count = 0
                for path in self.workspace.rglob("*"):
                    if file_count >= 1500:
                        break
                    if not path.is_file() or any(part in IGNORED for part in path.parts):
                        continue
                    file_count += 1
                    try:
                        latest_mtime = max(latest_mtime, path.stat().st_mtime_ns)
                    except OSError:
                        continue
                git_state = f"non-git:{file_count}:{latest_mtime}"
            return (stat.st_mtime_ns, git_index.stat().st_mtime_ns if git_index.exists() else 0,
                    git_state)
        except (OSError, subprocess.TimeoutExpired):
            return (0, 0)

    def _files(self) -> list[Path]:
        try:
            proc = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self.workspace, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10, creationflags=0x08000000,
            )
            if proc.returncode == 0:
                return [self.workspace / line for line in proc.stdout.splitlines() if line][:1500]
        except (OSError, subprocess.TimeoutExpired):
            pass
        files: list[Path] = []
        for path in self.workspace.rglob("*"):
            if len(files) >= 1500:
                break
            if path.is_file() and not any(part in IGNORED for part in path.parts):
                files.append(path)
        return files

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
            if path.name.lower() in {"readme.md", "main.py", "app.py", "webapp.py", "tui.py",
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
