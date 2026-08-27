"""Lightweight multi-language symbol and reference index."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


SUPPORTED = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
             ".rs", ".go", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
             ".cs", ".java", ".kt"}
IGNORED = {".git", ".venv", "venv", "node_modules", "runtime", "sessions",
           "dist", "build", "target", "__pycache__", ".idea", ".vscode"}

PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "javascript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
        ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=")),
        ("enum", re.compile(r"^\s*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
    ],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)")),
        ("struct", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+([A-Za-z_][\w]*)")),
        ("enum", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+([A-Za-z_][\w]*)")),
        ("trait", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+([A-Za-z_][\w]*)")),
    ],
    "go": [
        ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\(")),
        ("type", re.compile(r"^\s*type\s+([A-Za-z_][\w]*)\s+(?:struct|interface)\b")),
    ],
    "c_family": [
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|sealed\s+|abstract\s+|partial\s+)*(?:class|struct|interface|record|enum)\s+([A-Za-z_][\w]*)")),
        ("function", re.compile(r"^\s*(?:[\w:<>,~*&\[\]\s]+\s+)+([A-Za-z_~][\w~]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:\{|=>)")),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+)*(?:class|interface|record|enum)\s+([A-Za-z_][\w]*)")),
        ("function", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+|suspend\s+)*(?:[\w<>,?\[\].]+\s+)+([A-Za-z_][\w]*)\s*\([^;]*\)\s*(?:\{|=)")),
    ],
}


class CodeIndex:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self._symbols: list[dict[str, Any]] | None = None

    def set_workspace(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.invalidate()

    def invalidate(self) -> None:
        self._symbols = None

    def find_symbol(self, query: str, path: str = ".", kind: str | None = None,
                    max_results: int = 100) -> list[dict[str, Any]]:
        needle = str(query or "").lower()
        root = (self.workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            root.relative_to(self.workspace)
        except ValueError:
            return []
        out = []
        for item in self._all_symbols():
            file_path = self.workspace / item["path"]
            if file_path != root and root not in file_path.parents:
                continue
            if needle not in item["name"].lower() and needle not in item["qualified"].lower():
                continue
            if kind and item["kind"] != kind:
                continue
            out.append(item)
            if len(out) >= max(1, min(int(max_results), 500)):
                break
        return out

    def document_symbols(self, path: str) -> list[dict[str, Any]]:
        target = (self.workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            rel = str(target.relative_to(self.workspace))
        except ValueError:
            return []
        return [item for item in self._all_symbols() if item["path"] == rel]

    def find_references(self, symbol: str, path: str = ".",
                        max_results: int = 200) -> list[dict[str, Any]]:
        root = (self.workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        try:
            root.relative_to(self.workspace)
        except ValueError:
            return []
        limit = max(1, min(int(max_results), 1000))
        rg = shutil.which("rg")
        if rg:
            pattern = rf"\b{re.escape(symbol)}\b"
            proc = subprocess.run(
                [rg, "--json", "--color", "never", "--", pattern, "."],
                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if proc.returncode not in (0, 1):
                return []
            out: list[dict[str, Any]] = []
            for line in proc.stdout.splitlines():
                try:
                    event = json.loads(line)
                    if event.get("type") != "match":
                        continue
                    data = event["data"]
                    absolute = root / data["path"]["text"]
                    out.append({
                        "path": str(absolute.relative_to(self.workspace)),
                        "line": int(data["line_number"]),
                        "text": str(data["lines"]["text"]).strip()[:500],
                    })
                    if len(out) >= limit:
                        break
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            return out
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        out = []
        for file in self._files():
            if file != root and root not in file.parents:
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    out.append({"path": str(file.relative_to(self.workspace)),
                                "line": line_number, "text": line.strip()[:500]})
                    if len(out) >= limit:
                        return out
        return out

    def _all_symbols(self) -> list[dict[str, Any]]:
        if self._symbols is None:
            symbols: list[dict[str, Any]] = []
            for path in self._files():
                try:
                    if path.suffix.lower() in {".py", ".pyi"}:
                        symbols.extend(self._python_symbols(path))
                    else:
                        symbols.extend(self._pattern_symbols(path))
                except (OSError, SyntaxError, UnicodeError):
                    continue
            self._symbols = symbols
        return self._symbols

    def _files(self) -> list[Path]:
        try:
            proc = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self.workspace, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if proc.returncode == 0:
                return [self.workspace / line for line in proc.stdout.splitlines()
                        if Path(line).suffix.lower() in SUPPORTED][:5000]
        except (OSError, subprocess.TimeoutExpired):
            pass
        files = []
        for path in self.workspace.rglob("*"):
            if len(files) >= 5000:
                break
            if (path.is_file() and path.suffix.lower() in SUPPORTED
                    and not any(part in IGNORED for part in path.parts)):
                files.append(path)
        return files

    def _python_symbols(self, path: Path) -> list[dict[str, Any]]:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
        rel = str(path.relative_to(self.workspace))
        lines = text.splitlines()
        out: list[dict[str, Any]] = []

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.stack: list[str] = []

            def _record(self, node, kind: str):
                name = str(node.name)
                qualified = ".".join([*self.stack, name])
                line = int(getattr(node, "lineno", 1))
                out.append({"path": rel, "line": line,
                            "end_line": int(getattr(node, "end_lineno", line)),
                            "kind": kind, "name": name, "qualified": qualified,
                            "preview": lines[line - 1].strip()[:500] if lines else ""})
                self.stack.append(name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_ClassDef(self, node):
                self._record(node, "class")

            def visit_FunctionDef(self, node):
                self._record(node, "method" if self.stack else "function")

            def visit_AsyncFunctionDef(self, node):
                self._record(node, "method" if self.stack else "function")

        Visitor().visit(tree)
        return out

    def _pattern_symbols(self, path: Path) -> list[dict[str, Any]]:
        if path.stat().st_size > 1_000_000:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        suffix = path.suffix.lower()
        family = ("javascript" if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
                  else "rust" if suffix == ".rs"
                  else "go" if suffix == ".go"
                  else "java" if suffix in {".java", ".kt"}
                  else "c_family")
        rel = str(path.relative_to(self.workspace))
        out: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            for kind, pattern in PATTERNS[family]:
                match = pattern.search(line)
                if not match:
                    continue
                name = match.group(1)
                out.append({"path": rel, "line": line_number, "end_line": line_number,
                            "kind": kind, "name": name, "qualified": name,
                            "preview": line.strip()[:500]})
                break
        return out
