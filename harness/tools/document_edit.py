"""Focused Word edits and PDF page rendering through the existing vision model."""
from pathlib import Path
import re
import uuid

from harness.safety import Risk
from harness.tools.base import Tool


class EditWordTool(Tool):
    name = "edit_word_document"
    description = "Replace exact text in an existing DOCX while preserving paragraphs, tables and run styles. Specify old_text and new_text."
    risk = Risk.WRITE
    parameters = {"path": {"type": "string"}, "old_text": {"type": "string"},
                  "new_text": {"type": "string"}, "replace_all": {"type": "boolean"}}
    required = ["path", "old_text", "new_text"]

    def run(self, ctx, path, old_text, new_text, replace_all=False):
        from docx import Document
        if not old_text:
            raise ValueError("old_text must not be empty")
        target = ctx.resolve(path)
        doc = Document(target)
        from docx.text.paragraph import Paragraph
        from docx.text.run import Run
        seen = set()
        def paragraphs(container):
            for block in container.iter_inner_content():
                if isinstance(block, Paragraph):
                    if block._p not in seen:
                        seen.add(block._p)
                        yield block
                else:
                    for row in block.rows:
                        for cell in row.cells:
                            yield from paragraphs(cell)
        changes = 0
        for paragraph in paragraphs(doc):
            runs = []
            for part in paragraph.iter_inner_content():
                runs.extend([part] if isinstance(part, Run) else part.runs)
            text = "".join(run.text for run in runs)
            matches = [match.start() for match in re.finditer(re.escape(old_text), text)]
            if not replace_all:
                matches = matches[:1]
            for start in reversed(matches):
                end = start + len(old_text)
                position = 0
                inserted = False
                for run in runs:
                    length = len(run.text)
                    run_end = position + length
                    if position < end and run_end > start:
                        left = run.text[:max(0, start - position)]
                        right = run.text[max(0, end - position):] if run_end > end else ""
                        run.text = left + (new_text if not inserted else "") + right
                        inserted = True
                    position = run_end
                if not inserted:
                    break
                changes += 1
            if changes and not replace_all:
                break
        if not changes:
            return "No matching text; document unchanged."
        ctx.changes.record_before(target)
        temporary = target.with_name(f".{target.stem}-{uuid.uuid4().hex[:8]}.docx")
        try:
            doc.save(temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        ctx.changes.record_after(target)
        return f"OK: {changes} replacement(s), existing DOCX structure retained: {target}"


class ViewDocumentPageTool(Tool):
    name = "view_document_page"
    description = "Render one PDF page as an image for the vision model. Use for scanned pages, diagrams and layout. Page is 1-based."
    parameters = {"path": {"type": "string"}, "page": {"type": "integer"}}
    required = ["path"]

    def run(self, ctx, path, page=1):
        if not ctx.cfg.mmproj_file():
            return "This model has no vision capability. Switch to a vision-capable model to inspect PDF images."
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(ctx.resolve(path))
        try:
            if not 1 <= page <= len(document):
                raise ValueError(f"Page must be between 1 and {len(document)}")
            directory = ctx.session.dir / "images"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"pdf-page-{page}-{uuid.uuid4().hex[:8]}.png"
            selected = document[page - 1]
            bitmap = selected.render(scale=1.5)
            bitmap.to_pil().save(target)
            bitmap.close()
            selected.close()
            ctx.pending_images.append(target)
            return f"PDF page {page}/{len(document)} rendered for visual inspection: {target}"
        finally:
            document.close()


def register_document_edit_tools(registry):
    registry.register(EditWordTool())
    registry.register(ViewDocumentPageTool())
