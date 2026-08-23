"""Export strukturovaného textu do Markdown, DOCX a PDF."""
from __future__ import annotations

import re
from pathlib import Path


def _safe_filename(name: str, suffix: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*]+', "-", Path(name or "dokument").stem).strip(" .-")
    return (stem or "dokument") + suffix


def export_document(content: str, output_dir: Path, filename: str,
                    fmt: str, title: str = "") -> Path:
    fmt = fmt.lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = document_target(output_dir, filename, fmt)
    if fmt == "markdown":
        target.write_text(content, encoding="utf-8", newline="\n")
    elif fmt == "docx":
        _write_docx(target, content, title)
    else:
        _write_pdf(target, content, title)
    return target


def document_target(output_dir: Path, filename: str, fmt: str) -> Path:
    suffix = {"markdown": ".md", "docx": ".docx", "pdf": ".pdf"}.get(fmt.lower())
    if suffix is None:
        raise ValueError("format must be markdown, docx, or pdf")
    return output_dir / _safe_filename(filename, suffix)


def _blocks(content: str):
    paragraph: list[str] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if not line:
            if paragraph:
                yield "paragraph", " ".join(paragraph)
                paragraph = []
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if heading or bullet or numbered:
            if paragraph:
                yield "paragraph", " ".join(paragraph)
                paragraph = []
            if heading:
                yield f"heading{len(heading.group(1))}", heading.group(2)
            elif bullet:
                yield "bullet", bullet.group(1)
            else:
                yield "number", numbered.group(1)
        else:
            paragraph.append(line)
    if paragraph:
        yield "paragraph", " ".join(paragraph)


def _write_docx(path: Path, content: str, title: str) -> None:
    from docx import Document
    document = Document()
    if title:
        document.add_heading(title, level=0)
    for kind, text in _blocks(content):
        if kind.startswith("heading"):
            document.add_heading(text, level=min(int(kind[-1]), 4))
        elif kind == "bullet":
            document.add_paragraph(text, style="List Bullet")
        elif kind == "number":
            document.add_paragraph(text, style="List Number")
        else:
            document.add_paragraph(text)
    document.save(path)


def _write_pdf(path: Path, content: str, title: str) -> None:
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font = "Helvetica"
    for candidate in (Path(r"C:\Windows\Fonts\arial.ttf"),
                      Path(r"C:\Windows\Fonts\segoeui.ttf")):
        if candidate.is_file():
            pdfmetrics.registerFont(TTFont("QwenUnicode", str(candidate)))
            font = "QwenUnicode"
            break
    styles = getSampleStyleSheet()
    body = ParagraphStyle("BodyUnicode", parent=styles["BodyText"], fontName=font,
                          fontSize=10.5, leading=14, alignment=TA_LEFT, spaceAfter=5)
    headings = {
        level: ParagraphStyle(f"H{level}Unicode", parent=styles[f"Heading{min(level, 4)}"],
                              fontName=font, spaceBefore=8, spaceAfter=5)
        for level in range(1, 5)
    }
    story = []
    if title:
        story.extend([Paragraph(_escape(title), headings[1]), Spacer(1, 3 * mm)])
    for kind, text in _blocks(content):
        escaped = _escape(text)
        if kind.startswith("heading"):
            story.append(Paragraph(escaped, headings[int(kind[-1])]))
        elif kind == "bullet":
            story.append(Paragraph(f"• {escaped}", body))
        elif kind == "number":
            story.append(Paragraph(escaped, body))
        else:
            story.append(Paragraph(escaped, body))
    document = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18 * mm,
                                 leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    document.build(story)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
