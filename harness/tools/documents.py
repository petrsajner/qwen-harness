"""Dokumentové exportní, čtecí a tabulkové nástroje pro Marvin."""
from __future__ import annotations

from typing import Any

from harness.documents import (
    document_target,
    edit_spreadsheet_content,
    export_document,
    read_document_content,
)
from harness.safety import Risk
from harness.tools.base import AgentContext, Tool


class ExportDocumentTool(Tool):
    name = "export_document"
    description = ("Export final text directly as Markdown, DOCX, or PDF. With a project, save "
                   "into its exports folder; without a project, save inside this chat's session.")
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
        output_dir = ((ctx.project_workspace / "exports") if ctx.project_workspace
                      else (ctx.session.dir / "exports"))
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


class ReadDocumentTool(Tool):
    name = "read_document"
    parallel_safe = True
    description = (
        "Read and extract structured content from Word documents (.docx), PDFs (.pdf), "
        "Excel spreadsheets (.xlsx, .xlsm), or CSV files (.csv). Returns readable text, headings, "
        "and Markdown formatted tables."
    )
    parameters = {
        "path": {"type": "string", "description": "Document path (relative to workspace or absolute)"},
        "max_chars": {"type": "integer", "description": "Maximum characters to extract (default 40000)"},
        "sheet": {"type": "string", "description": "Optional sheet name for Excel spreadsheets"},
    }
    required = ["path"]
    risk = Risk.SAFE

    def run(self, ctx: AgentContext, path: str, max_chars: int = 40_000, sheet: str | None = None) -> str:
        f = ctx.resolve(path)
        if not f.exists():
            return f"ERROR: Document not found: {f}"
        try:
            return read_document_content(f, max_chars=max_chars, sheet=sheet)
        except Exception as exc:
            return f"ERROR: Failed to read document {f.name}: {exc}"


class EditSpreadsheetTool(Tool):
    name = "edit_spreadsheet"
    parallel_safe = False
    description = (
        "Create or edit Excel spreadsheets (.xlsx). Supported actions:\n"
        "- 'create': create a new workbook with optional data (list of rows)\n"
        "- 'list_sheets': list all sheet names in the workbook\n"
        "- 'create_sheet': add a new sheet with given title\n"
        "- 'append_rows': append rows to a sheet (data = list of row lists)\n"
        "- 'update_cells': update specific cells via dict (data = {'A1': 100, 'B1': '=SUM(A1:A5)'})"
    )
    parameters = {
        "path": {"type": "string", "description": "Path to the .xlsx file (relative to workspace or absolute)"},
        "action": {
            "type": "string",
            "enum": ["create", "list_sheets", "create_sheet", "append_rows", "update_cells"],
            "description": "Action to perform on the spreadsheet",
        },
        "sheet": {"type": "string", "description": "Target sheet name (optional, defaults to active sheet)"},
        "data": {"description": "Data for append_rows (list of lists) or update_cells (dict of cell coords to values)"},
        "title": {"type": "string", "description": "Optional title for new sheet or workbook"},
    }
    required = ["path", "action"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, path: str, action: str, sheet: str | None = None,
            data: Any = None, title: str | None = None) -> str:
        f = ctx.resolve(path)
        if ctx.changes and action in ("create", "create_sheet", "append_rows", "update_cells"):
            ctx.changes.record_before(f)
        try:
            result = edit_spreadsheet_content(f, action=action, sheet=sheet, data=data, title=title)
        except Exception as exc:
            return f"ERROR: Spreadsheet operation failed: {exc}"
        if ctx.changes and action in ("create", "create_sheet", "append_rows", "update_cells"):
            ctx.changes.record_after(f)
        return result


def register_document_tools(registry) -> None:
    registry.register(ExportDocumentTool())
    registry.register(ReadDocumentTool())
    registry.register(EditSpreadsheetTool())
