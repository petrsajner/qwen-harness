---
name: web-scraping-extraction
description: Resilient workflow for fetching, parsing, and distilling structured information from web pages, documentation, and online APIs into clean Markdown or JSON.
---

# Web Scraping & Data Extraction Craft

Extract high-signal facts, tables, and documentation from the internet into clean, structured formats.

## Strategy
1. **Search First, Fetch Targeted Pages**:
   - Use `web_search(query)` to locate official documentation, release notes, or data sources.
   - Pick the most authoritative URLs (prefer official docs, GitHub repositories, reputable tech portals).
2. **Fetch and Strip Boilerplate**:
   - Use `web_fetch(url)` to retrieve content.
   - Ignore cookie banners, navigation menus, ads, and footers. Focus strictly on article body, API tables, and code snippets.
3. **Structured Transformation**:
   - When extracting data records: transform them into Markdown tables or JSON arrays with clear keys (`name`, `version`, `date`, `description`, `url`).
   - Standardize date formats (ISO `YYYY-MM-DD`) and currency/number notations.
4. **Attribution & Provenance**:
   - Always state the source URL and retrieval context.
   - Distinguish facts confirmed in the source text from assumptions or extrapolations.
5. **Handling Limitations**:
   - If a page blocks automated access, requires JavaScript SPAs, or is behind a login wall, report the limitation clearly and explore alternative public sources or archives.
