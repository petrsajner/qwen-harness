---
name: excel-spreadsheet-craft
description: Practical workflows for creating, reading, validating, and editing Excel spreadsheets (.xlsx) — formulas, data modeling, cell formatting, and reproducible table structures.
---

# Excel Spreadsheet Craft

Turn messy, unstructured data into clean, maintainable, and readable spreadsheets.

## Workflow
1. **Understand the Goal**: Identify the primary audience, whether the sheet is for human consumption (reports) or automated processing (data pipelines), and the required calculations.
2. **Inspect Existing Workbooks**: Use `read_document(path)` to inspect existing sheets, detect used columns, examine formula patterns, and check existing row structures before modifying.
3. **Design Schema & Layout**:
   - Put clear descriptive headers in Row 1.
   - Use one data type per column (dates, text, currency, integers).
   - Keep data tabular (avoid blank rows or merged cells inside data ranges).
   - Reserve the bottom row for totals/aggregates with a clear distinction (bold, top border).
4. **Formulas & Computations**:
   - Write standard uppercase formula names: `=SUM()`, `=AVERAGE()`, `=IF()`, `=COUNTIF()`, `=XLOOKUP()`.
   - Keep formulas readable and reference relative rows correctly.
   - For complex multi-step computations, create dedicated intermediate calculation columns rather than deeply nested unreadable formulas.
5. **Execution**:
   - For simple updates: use `edit_spreadsheet` (`create`, `append_rows`, `update_cells`).
   - For advanced formatting, conditional formatting, multiple tabs, or custom charts: write a standalone Python script utilizing `openpyxl`.
6. **Verify Results**: Read the updated file with `read_document` or verify calculations on a test row to ensure formula integrity.
