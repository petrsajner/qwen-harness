"""Structured symbol and reference navigation tools."""
from __future__ import annotations

import json

from harness.tools.base import AgentContext, Tool


def _index(ctx: AgentContext):
    if ctx.code_index is None:
        raise RuntimeError("code index unavailable; select a project first")
    return ctx.code_index


class FindSymbolTool(Tool):
    name = "find_symbol"
    parallel_safe = True
    description = ("Find declarations across Python, JS/TS, Rust, Go, C/C++, C#, Java, and "
                   "Kotlin. Returns file, line, kind, qualified name, and declaration preview.")
    parameters = {
        "query": {"type": "string"},
        "path": {"type": "string", "description": "Optional project subdirectory"},
        "kind": {"type": "string", "description": "Optional exact kind filter"},
        "max_results": {"type": "integer"},
    }
    required = ["query"]

    def run(self, ctx: AgentContext, query: str, path: str = ".",
            kind: str | None = None, max_results: int = 100) -> str:
        return json.dumps(
            _index(ctx).find_symbol(query, path, kind, max_results),
            ensure_ascii=False, indent=2)


class DocumentSymbolsTool(Tool):
    name = "document_symbols"
    parallel_safe = True
    description = "List declarations in one source file with line ranges and symbol kinds."
    parameters = {"path": {"type": "string"}}
    required = ["path"]

    def run(self, ctx: AgentContext, path: str) -> str:
        return json.dumps(_index(ctx).document_symbols(path), ensure_ascii=False, indent=2)


class FindReferencesTool(Tool):
    name = "find_references"
    parallel_safe = True
    description = ("Find whole-word textual references to a symbol quickly across the project. "
                   "Results include declarations; inspect context before editing.")
    parameters = {
        "symbol": {"type": "string"},
        "path": {"type": "string", "description": "Optional project subdirectory"},
        "max_results": {"type": "integer"},
    }
    required = ["symbol"]

    def run(self, ctx: AgentContext, symbol: str, path: str = ".",
            max_results: int = 200) -> str:
        return json.dumps(
            _index(ctx).find_references(symbol, path, max_results),
            ensure_ascii=False, indent=2)


def register_code_tools(registry) -> None:
    registry.register(FindSymbolTool())
    registry.register(DocumentSymbolsTool())
    registry.register(FindReferencesTool())
