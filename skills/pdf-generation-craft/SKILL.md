---
name: pdf-generation-craft
description: Guidelines for generating high-quality printable PDF reports with ReportLab and extracting text/tables from existing PDFs using pypdf.
---

# PDF Generation & Inspection Craft

Create elegant, publication-ready PDF documents and reliably inspect external PDF files.

## Principles of PDF Design
1. **Layout & Page Geometry**:
   - Standardize on A4 format with comfortable margins (15–20 mm).
   - Reserve space for headers (Document title, date) and footers (page numbering: "Strana X z Y").
   - Prevent orphan headings: headings must keep with the following paragraph across page breaks.
2. **Typography & Encoding**:
   - Use built-in Unicode-safe fonts or registered TrueType fonts (e.g. Arial, DejaVuSans) to ensure Czech and European diacritics render without character corruption.
   - Set distinct font sizes: Document Title (22–24 pt), H1 (16–18 pt bold), H2 (13–14 pt bold), Body (10–10.5 pt), Captions/Footers (8–9 pt).
3. **Tables & Graphics**:
   - Always specify explicit column widths for ReportLab tables.
   - Alternate row colors (subtle light gray `#F8FAFC`) to improve readability of dense data tables.
4. **Tool Selection**:
   - For standard reports: use `export_document(content, filename, format="pdf", title=...)`.
   - For custom enterprise layouts, cover pages, or exact coordinates: write a dedicated Python script using ReportLab Platypus (`SimpleDocTemplate`, `Paragraph`, `Table`, `Spacer`, `PageBreak`).
5. **Inspecting Existing PDFs**:
   - Use `read_document(path)` to extract text and structure page-by-page.
   - Be mindful that scanned image-only PDFs require OCR; text-based PDFs extract immediately.
