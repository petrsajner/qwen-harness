"""Dokumentové exportní nástroje pro Psaní a Výzkum."""
from __future__ import annotations

from harness.documents import document_target, export_document
from harness.safety import Risk
from harness.tools.base import AgentContext, Tool


class ExportDocumentTool(Tool):
    name = "export_document"
    description = "Export final text as Markdown, DOCX, or PDF into the project's exports folder."
    parameters = {
        "content": {"type": "string", "description": "Complete final document text"},
        "filename": {"type": "string", "description": "Output filename without extension"},
        "format": {"type": "string", "enum": ["markdown", "docx", "pdf"]},
        "title": {"type": "string", "description": "Optional document title"},
    }
    required = ["content", "filename", "format"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, content: str, filename: str,
            format: str, title: str = "") -> str:
        output_dir = ctx.workspace / "exports"
        try:
            expected = document_target(output_dir, filename, format)
        except ValueError as exc:
            return f"ERROR: document export failed: {exc}"
        if ctx.changes:
            ctx.changes.record_before(expected)
        try:
            target = export_document(content, output_dir, filename, format, title)
        except (OSError, ValueError, ImportError) as exc:
            return f"ERROR: document export failed: {exc}"
        if ctx.changes:
            ctx.changes.record_after(target)
        return f"OK: exported {target} ({target.stat().st_size:,} bytes)"


def register_document_tools(registry) -> None:
    registry.register(ExportDocumentTool())
