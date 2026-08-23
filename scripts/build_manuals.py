"""Build the English and Czech user manuals as polished PDF documents."""
from __future__ import annotations

import re
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, HRFlowable, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Preformatted, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "docs" / "manual"
OUTPUT_DIR = ROOT / "output" / "pdf"

ACCENT = colors.HexColor("#0F8F83")
ACCENT_DARK = colors.HexColor("#075E58")
INK = colors.HexColor("#18212B")
MUTED = colors.HexColor("#5E6B78")
LINE = colors.HexColor("#CBD5DE")
PALE = colors.HexColor("#EAF5F3")
PANEL = colors.HexColor("#F4F7F9")
WARN = colors.HexColor("#FFF3D6")


def register_fonts() -> tuple[str, str, str, str]:
    files = {
        "normal": Path(r"C:\Windows\Fonts\arial.ttf"),
        "bold": Path(r"C:\Windows\Fonts\arialbd.ttf"),
        "italic": Path(r"C:\Windows\Fonts\ariali.ttf"),
        "mono": Path(r"C:\Windows\Fonts\consola.ttf"),
    }
    names = {
        "normal": "ManualArial",
        "bold": "ManualArialBold",
        "italic": "ManualArialItalic",
        "mono": "ManualConsolas",
    }
    for key, source in files.items():
        if not source.is_file():
            raise FileNotFoundError(f"Required font not found: {source}")
        pdfmetrics.registerFont(TTFont(names[key], str(source)))
    pdfmetrics.registerFontFamily(
        names["normal"], normal=names["normal"], bold=names["bold"],
        italic=names["italic"], boldItalic=names["bold"],
    )
    return names["normal"], names["bold"], names["italic"], names["mono"]


FONT, FONT_BOLD, FONT_ITALIC, FONT_MONO = register_fonts()


def inline(text: str) -> str:
    value = escape(text.strip())
    value = re.sub(r"`([^`]+)`", rf'<font name="{FONT_MONO}">\1</font>', value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*", r"<i>\1</i>", value)
    value = re.sub(r"\[([^]]+)\]\((https?://[^)]+)\)", r'<link href="\2" color="#075E58">\1</link>', value)
    return value


def styles():
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "ManualBody", parent=base["BodyText"], fontName=FONT, fontSize=9.4,
            leading=13.2, textColor=INK, spaceAfter=5.5, allowWidows=0, allowOrphans=0,
        ),
        "small": ParagraphStyle(
            "ManualSmall", parent=base["BodyText"], fontName=FONT, fontSize=8,
            leading=10.5, textColor=MUTED,
        ),
        "h1": ParagraphStyle(
            "ManualH1", parent=base["Heading1"], fontName=FONT_BOLD, fontSize=19,
            leading=23, textColor=ACCENT_DARK, spaceBefore=0, spaceAfter=10,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "ManualH2", parent=base["Heading2"], fontName=FONT_BOLD, fontSize=13.2,
            leading=16, textColor=INK, spaceBefore=11, spaceAfter=6, keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "ManualH3", parent=base["Heading3"], fontName=FONT_BOLD, fontSize=10.8,
            leading=13.5, textColor=ACCENT_DARK, spaceBefore=8, spaceAfter=4,
            keepWithNext=True,
        ),
        "bullet": ParagraphStyle(
            "ManualBullet", parent=base["BodyText"], fontName=FONT, fontSize=9.2,
            leading=12.8, textColor=INK, leftIndent=13, firstLineIndent=-7,
            bulletIndent=2, spaceAfter=3,
        ),
        "number": ParagraphStyle(
            "ManualNumber", parent=base["BodyText"], fontName=FONT, fontSize=9.2,
            leading=12.8, textColor=INK, leftIndent=15, firstLineIndent=-11,
            spaceAfter=3,
        ),
        "code": ParagraphStyle(
            "ManualCode", parent=base["Code"], fontName=FONT_MONO, fontSize=7.6,
            leading=10, textColor=colors.HexColor("#DCE7EF"), backColor=INK,
            borderPadding=7, leftIndent=2, rightIndent=2, spaceBefore=4, spaceAfter=7,
        ),
        "callout": ParagraphStyle(
            "ManualCallout", parent=base["BodyText"], fontName=FONT, fontSize=9,
            leading=12.5, textColor=INK, backColor=PALE, borderColor=ACCENT,
            borderWidth=0.7, borderPadding=8, leftIndent=5, rightIndent=5,
            spaceBefore=5, spaceAfter=7,
        ),
        "warning": ParagraphStyle(
            "ManualWarning", parent=base["BodyText"], fontName=FONT, fontSize=9,
            leading=12.5, textColor=INK, backColor=WARN, borderColor=colors.HexColor("#D19A25"),
            borderWidth=0.7, borderPadding=8, leftIndent=5, rightIndent=5,
            spaceBefore=5, spaceAfter=7,
        ),
        "table": ParagraphStyle(
            "ManualTable", parent=base["BodyText"], fontName=FONT, fontSize=7.6,
            leading=9.6, textColor=INK,
        ),
        "toc1": ParagraphStyle(
            "ManualTOC1", fontName=FONT_BOLD, fontSize=10, leading=14,
            leftIndent=0, firstLineIndent=0, textColor=INK, spaceBefore=3,
        ),
        "toc2": ParagraphStyle(
            "ManualTOC2", fontName=FONT, fontSize=8.5, leading=12,
            leftIndent=12, firstLineIndent=0, textColor=MUTED,
        ),
    }


STYLES = styles()


class ManualDocTemplate(BaseDocTemplate):
    def __init__(self, filename: Path, title: str, language: str):
        super().__init__(
            str(filename), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
            topMargin=18 * mm, bottomMargin=17 * mm, title=title,
            author="Qwen Harness Project",
        )
        self.manual_title = title
        self.language = language
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
        self.addPageTemplates(PageTemplate(id="manual", frames=[frame], onPage=self._page))

    def _page(self, canvas, doc):
        page = canvas.getPageNumber()
        if page == 1:
            return
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(self.leftMargin, A4[1] - 12 * mm, A4[0] - self.rightMargin, A4[1] - 12 * mm)
        canvas.setFont(FONT, 7.4)
        canvas.setFillColor(MUTED)
        canvas.drawString(self.leftMargin, A4[1] - 9.5 * mm, self.manual_title)
        label = "Strana" if self.language == "cs" else "Page"
        canvas.drawRightString(A4[0] - self.rightMargin, 9 * mm, f"{label} {page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            level = getattr(flowable, "_toc_level", None)
            if level is not None:
                text = flowable.getPlainText()
                key = f"h-{level}-{self.seq.nextf('heading')}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=False)
                self.notify("TOCEntry", (level, text, self.page, key))


def cover(title: str, subtitle: str, version: str, language: str):
    version_label = "Verze aplikace" if language == "cs" else "Application version"
    date_label = "Uživatelský manuál" if language == "cs" else "User manual"
    return [
        Spacer(1, 24 * mm),
        HRFlowable(width="100%", thickness=5, color=ACCENT, spaceAfter=14 * mm),
        Paragraph(title, ParagraphStyle(
            "CoverTitle", fontName=FONT_BOLD, fontSize=29, leading=34,
            textColor=INK, alignment=TA_LEFT, spaceAfter=8,
        )),
        Paragraph(subtitle, ParagraphStyle(
            "CoverSubtitle", fontName=FONT, fontSize=14, leading=19,
            textColor=ACCENT_DARK, alignment=TA_LEFT, spaceAfter=18 * mm,
        )),
        Table(
            [[Paragraph(f"<b>{version_label}</b>", STYLES["body"]), version],
             [Paragraph(f"<b>{date_label}</b>", STYLES["body"]), "2026"]],
            colWidths=[48 * mm, 75 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
                ("FONTNAME", (0, 0), (-1, -1), FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]),
        ),
        Spacer(1, 55 * mm),
        Paragraph(
            "Local AI · Qwen 3.8 · Ornith 1.5 · Windows 11 · RTX 5090",
            ParagraphStyle("CoverFoot", fontName=FONT, fontSize=9, textColor=MUTED),
        ),
        PageBreak(),
    ]


def markdown_story(text: str, toc_title: str):
    story = []
    toc = TableOfContents()
    toc.levelStyles = [STYLES["toc1"], STYLES["toc2"]]
    story.extend([
        Paragraph(toc_title, STYLES["h1"]),
        Spacer(1, 2 * mm), toc, PageBreak(),
    ])
    lines = text.splitlines()
    i = 0
    paragraph: list[str] = []

    def flush():
        nonlocal paragraph
        if paragraph:
            story.append(Paragraph(inline(" ".join(part.strip() for part in paragraph)), STYLES["body"]))
            paragraph = []

    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if not stripped:
            flush()
            i += 1
            continue
        if stripped.startswith("```"):
            flush()
            language = stripped[3:].strip()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i].rstrip("\n"))
                i += 1
            i += 1
            code_block = Preformatted("\n".join(code), STYLES["code"])
            code_panel = Table([[code_block]], colWidths=[A4[0] - 40 * mm], hAlign="LEFT")
            code_panel.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), INK),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#344556")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.extend([code_panel, Spacer(1, 3 * mm)])
            continue
        if stripped == "<!-- pagebreak -->":
            flush()
            story.append(PageBreak())
            i += 1
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            if level == 1:
                story.append(CondPageBreak(48 * mm))
            p = Paragraph(inline(heading.group(2)), STYLES[f"h{level}"])
            p._toc_level = 0 if level == 1 else None
            story.append(p)
            i += 1
            continue
        if stripped.startswith("> "):
            flush()
            value = stripped[2:].strip()
            style = STYLES["warning"] if value.upper().startswith(("WARNING", "VAROVÁNÍ")) else STYLES["callout"]
            story.append(Paragraph(inline(value), style))
            i += 1
            continue
        if re.match(r"^[-*]\s+", stripped):
            flush()
            value = re.sub(r"^[-*]\s+", "", stripped)
            story.append(Paragraph(inline(value), STYLES["bullet"], bulletText="•"))
            i += 1
            continue
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            flush()
            story.append(Paragraph(inline(numbered.group(2)), STYLES["number"],
                                   bulletText=f"{numbered.group(1)}."))
            i += 1
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush()
            rows = []
            while i < len(lines):
                candidate = lines[i].strip()
                if not (candidate.startswith("|") and candidate.endswith("|")):
                    break
                cells = [cell.replace(r"\|", "|").strip()
                         for cell in re.split(r"(?<!\\)\|", candidate[1:-1])]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    rows.append(cells)
                i += 1
            if rows:
                columns = max(len(row) for row in rows)
                normalized = [row + [""] * (columns - len(row)) for row in rows]
                data = [[Paragraph(inline(cell), STYLES["table"]) for cell in row]
                        for row in normalized]
                weights = [1] * columns
                if columns == 2:
                    weights = [0.32, 0.68]
                elif columns == 3:
                    weights = [0.23, 0.35, 0.42]
                elif columns >= 4:
                    weights = [1 / columns] * columns
                usable = A4[0] - 36 * mm
                widths = [usable * weight / sum(weights) for weight in weights]
                table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), ACCENT_DARK),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
                    ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.extend([table, Spacer(1, 3 * mm)])
            continue
        if stripped == "---":
            flush()
            story.append(HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=4, spaceAfter=6))
            i += 1
            continue
        paragraph.append(stripped)
        i += 1
    flush()
    return story


def build(source: Path, target: Path, title: str, subtitle: str, language: str, version: str):
    target.parent.mkdir(parents=True, exist_ok=True)
    toc_title = "Obsah" if language == "cs" else "Contents"
    doc = ManualDocTemplate(target, title, language)
    story = cover(title, subtitle, version, language)
    story.extend(markdown_story(source.read_text(encoding="utf-8"), toc_title))
    doc.multiBuild(story)


def main() -> int:
    version = (ROOT / "installer" / "version.txt").read_text(encoding="utf-8").strip()
    manuals = [
        ("manual_en.md", "Qwen Harness User Manual", "Complete guide to the local chat and coding workstation", "en", "QwenHarness-Manual-EN.pdf"),
        ("manual_cs.md", "Uživatelský manuál Qwen Harness", "Kompletní průvodce lokální chatovací a vývojovou stanicí", "cs", "QwenHarness-Manual-CS.pdf"),
    ]
    for source_name, title, subtitle, language, target_name in manuals:
        build(SOURCE_DIR / source_name, OUTPUT_DIR / target_name,
              title, subtitle, language, version)
        print(f"[OK] {OUTPUT_DIR / target_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
