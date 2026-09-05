"""Lokální fulltextové vyhledávání v projektu pomocí SQLite FTS5 a BM25."""
from __future__ import annotations

import os
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool, truncate

# Přípony textových souborů, které indexujeme
TEXT_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".css", ".scss", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".md", ".rst", ".txt", ".sql", ".sh", ".bat", ".ps1", ".cmd",
    ".rs", ".go", ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".kt",
}

IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "runtime", "sessions",
    "dist", "build", "target", "__pycache__", ".idea", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "output",
}


def _collect_files(root: Path, max_files: int = 2000) -> list[Path]:
    """Vyhledá indexovatelné textové soubory v kořenu projektu."""
    files: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Odfiltrovat ignorované adresáře v místě
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix.lower() in TEXT_EXTENSIONS:
                    try:
                        # Ignorovat obří soubory (> 1.5 MB)
                        if p.stat().st_size <= 1_500_000:
                            files.append(p)
                            if len(files) >= max_files:
                                return files
                    except OSError:
                        continue
    except OSError:
        pass
    return files


def _sanitize_fts_query(raw_query: str) -> str:
    """Očistí uživatelský dotaz pro SQLite FTS5 operátor MATCH."""
    words = re.findall(r"\w+", raw_query, re.UNICODE)
    if not words:
        return ""
    return " OR ".join(f'"{w}"' for w in words)


class SearchProjectTool(Tool):
    name = "search_project"
    parallel_safe = True
    description = (
        "Search for keywords, concepts or code snippets across all text files in the project "
        "using fast local SQLite FTS5 with BM25 ranking. Returns matching files, relevance, and snippets."
    )
    parameters = {
        "query": {"type": "string", "description": "Keywords or phrase to find across project files"},
        "max_results": {"type": "integer", "description": "Maximum number of results to return (default 15)"},
    }
    required = ["query"]
    risk = Risk.SAFE

    def run(self, ctx: AgentContext, query: str, max_results: int = 15) -> str:
        workspace = ctx.workspace
        if not workspace.is_dir():
            return f"ERROR: Workspace directory not found: {workspace}"

        clean_query = _sanitize_fts_query(query)
        if not clean_query:
            return "ERROR: Query contains no searchable words."

        try:
            from harness.file_index import search_index
            key = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:24]
            database = ctx.cfg.path("paths.runtime_dir") / "indexes" / f"{key}.sqlite3"
            hits, file_count = search_index(workspace, database, clean_query, TEXT_EXTENSIONS, max_results)
            if not hits:
                return f"No matches found for '{query}' across {file_count} project files."

            out = [f"Found {len(hits)} matching files for '{query}':\n"]
            for rel_path, snippet_text, score in hits:
                clean_snippet = snippet_text.replace("\n", " ").strip()
                out.append(f"- **`{rel_path}`** (relevance: {-score:.2f})\n  Snippet: {clean_snippet}\n")

            return truncate("".join(out), limit=30_000, label="search results")
        except Exception as e:
            return f"ERROR during project search: {e}"


def register_search_tools(registry) -> None:
    registry.register(SearchProjectTool())
