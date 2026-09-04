---
name: word-document-craft
description: Structured methodology for drafting, styling, and reviewing Microsoft Word (.docx) documents — typographic hierarchy, clear tables, callout blocks, and clean document exports.
---

# Word Document Craft

Produce polished, executive-ready Word documents with strong typographic hierarchy and clean visual presentation.

## Core Rules
1. **Clear Hierarchical Outline**:
   - Every formal document starts with a Title, Metadata block (Author, Date, Status/Version), and an Executive Summary.
   - Use Heading 1 for major sections, Heading 2 for subsections, and Heading 3 sparingly for granular details.
   - Never use manual bold text as fake headings; rely on true semantic heading levels.
2. **Scannable Visual Flow**:
   - Break walls of text: keep paragraphs under 5-6 lines.
   - Highlight key takeaways using bold keyphrases at the start of bullet points.
   - Use Markdown/DOCX tables for structured comparisons, metrics, and parameters.
   - Use callout boxes (quote blocks) for warnings, important notes, or recommendations.
3. **Creation & Export**:
   - Use `export_document(content, filename, format="docx", title=...)` for direct, standardized exports.
   - When custom typography, headers/footers, or complex XML styling is needed, use a Python script with `python-docx`.
4. **Validation**:
   - Inspect the generated document using `read_document(path)` to verify headings, paragraphs, and tables rendered cleanly.
   - Check that Czech or localized special characters and punctuation are rendered properly.
