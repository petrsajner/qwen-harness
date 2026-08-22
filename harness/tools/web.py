"""Nástroje pro přístup k internetu - web_search (Bing, fallback DDG) a web_fetch.

Oba nástroje jsou read-only (pouze GET), bez API klíčů. Slouží modelu k
dohledávání aktuálních informací, dokumentace apod. Limity (timeout, délka
výstupu, počet výsledků) se dají nastavit v config.yaml sekce `web:`.
"""
from __future__ import annotations

import html as _htmlmod
import re
import urllib.parse

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool, truncate

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _web_cfg(ctx: AgentContext) -> dict:
    return ctx.cfg.data.get("web", {}) or {}


def _strip_tags(html: str) -> str:
    """Odstraň tagy a srovnej mezery/bílé znaky."""
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return _htmlmod.unescape(text).strip()


def _ddg_unwrap(href: str) -> str:
    """DDG html vrací odkazy přes redirect //duckduckgo.com/l/?uddg=<urlenc>."""
    if "uddg=" in href:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if "uddg" in qs:
            return qs["uddg"][0]
    if href.startswith("//"):
        href = "https:" + href
    return href


def _bing_unwrap(url: str) -> str:
    """Bing zabalí odkazy do bing.com/ck/a?...&u=a1<base64url> - rozbal je."""
    if "bing.com/ck/" not in url:
        return url
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        u = (qs.get("u") or [""])[0]
        if u.startswith("a1"):
            u = u[2:]
        u += "=" * (-len(u) % 4)  # doplň padding
        import base64
        dec = base64.urlsafe_b64decode(u.encode()).decode("utf-8", "replace")
        return dec if dec.startswith("http") else url
    except Exception:
        return url


_ddgs_state = {"tried": False, "ok": False}


def _ensure_ddgs() -> bool:
    """Je k dispozici knihovna ddgs? Když ne, jednou zkus tichou doinstalaci."""
    if _ddgs_state["tried"]:
        return _ddgs_state["ok"]
    _ddgs_state["tried"] = True
    try:
        from ddgs import DDGS  # noqa: F401
        _ddgs_state["ok"] = True
        return True
    except ImportError:
        pass
    import subprocess
    import sys
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ddgs"],
                       check=True, timeout=180, capture_output=True, creationflags=flags)
        from ddgs import DDGS  # noqa: F401
        _ddgs_state["ok"] = True
    except Exception:
        _ddgs_state["ok"] = False
    return _ddgs_state["ok"]


def _ddgs_search(query: str, want: int, timeout: int, backend: str = "google"
                 ) -> list[tuple[str, str, str]]:
    from ddgs import DDGS
    out: list[tuple[str, str, str]] = []
    try:
        ddgs = DDGS(timeout=timeout)
    except TypeError:
        ddgs = DDGS()
    with ddgs:
        for r in ddgs.text(query, max_results=want, backend=backend):
            out.append((r.get("title", ""), r.get("href") or r.get("url") or "",
                        r.get("body", "")))
    return out


class WebSearchTool(Tool):
    name = "web_search"
    description = ("Search the web (Google) and return top results "
                   "(title, URL, snippet). Use for current information, documentation, "
                   "error messages, library APIs - anything you are unsure about "
                   "or that may have changed after your training data. "
                   "Then use web_fetch on a promising URL for details.")
    parameters = {
        "query": {"type": "string", "description": "Search query (be specific)"},
        "max_results": {"type": "integer", "description": "Number of results (default from config, max 8)"},
    }
    required = ["query"]
    risk = Risk.SAFE  # read-only GET vyhledávání

    def run(self, ctx: AgentContext, query: str = "", max_results: int = 0) -> str:
        if not (query or "").strip():
            return "ERROR: empty query"
        if not _web_cfg(ctx).get("enabled", True):
            return "ERROR: web access is disabled in config (web.enabled: false)"
        import requests
        wcfg = _web_cfg(ctx)
        want = max(1, min(int(max_results or wcfg.get("search_results", 5)), 8))
        timeout = wcfg.get("timeout", 10)
        try:
            items = []
            if _ensure_ddgs():
                be = wcfg.get("backend", "google")
                try:
                    items = _ddgs_search(query.strip(), want, timeout, backend=be)
                except Exception:
                    items = _ddgs_search(query.strip(), want, timeout, backend="auto")
            items = items \
                or self._bing(query.strip(), want, timeout) \
                or self._ddg(query.strip(), want, timeout)
        except Exception as e:
            return f"ERROR: web search failed: {type(e).__name__}: {e}"
        if not items:
            return "(žádné výsledky - zkus jiný dotaz)"
        if getattr(ctx, "research", None):
            ctx.research.record_query(query.strip(), items[:want])
        out = []
        for i, (title, url, snip) in enumerate(items[:want]):
            out.append(f"{i + 1}. {title or '(bez titulku)'}\n   {url}\n   {snip[:300]}")
        return "\n\n".join(out)

    @staticmethod
    def _bing(query: str, want: int, timeout: int) -> list[tuple[str, str, str]]:
        import requests
        r = requests.get("https://www.bing.com/search",
                         params={"q": query, "count": want + 2, "mkt": "en-US"},
                         headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
                         timeout=timeout)
        r.raise_for_status()
        blocks = re.findall(r'<li class="b_algo".*?</li>', r.text, re.S)
        out: list[tuple[str, str, str]] = []
        for b in blocks:
            m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
            if not m:
                continue
            url = _bing_unwrap(_htmlmod.unescape(m.group(1)))
            title = _strip_tags(m.group(2))
            sm = re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
            snippet = _strip_tags(sm.group(1)) if sm else ""
            out.append((title, url, snippet))
        return out

    @staticmethod
    def _ddg(query: str, want: int, timeout: int) -> list[tuple[str, str, str]]:
        import requests
        r = requests.get("https://html.duckduckgo.com/html/", params={"q": query},
                         headers={"User-Agent": _UA, "Accept-Language": "cs,en"},
                         timeout=timeout)
        items = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
        return [(_strip_tags(t), _ddg_unwrap(h),
                 _strip_tags(snips[i]) if i < len(snips) else "")
                for i, (h, t) in enumerate(items)]


class WebFetchTool(Tool):
    name = "web_fetch"
    description = ("Download a web page (http/https) and return its readable text "
                   "(scripts/styles removed). Use after web_search to read details, "
                   "or for any public documentation page. Not for local files.")
    parameters = {
        "url": {"type": "string", "description": "Full URL including http(s)://"},
        "max_chars": {"type": "integer", "description": "Max characters to return (default from config)"},
    }
    required = ["url"]
    risk = Risk.SAFE  # read-only GET

    def run(self, ctx: AgentContext, url: str = "", max_chars: int = 0) -> str:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return "ERROR: url must start with http:// or https://"
        if not _web_cfg(ctx).get("enabled", True):
            return "ERROR: web access is disabled in config (web.enabled: false)"
        import requests
        try:
            r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "cs,en"},
                             timeout=_web_cfg(ctx).get("timeout", 10))
            r.raise_for_status()
        except Exception as e:
            return f"ERROR: fetch failed: {type(e).__name__}: {e}"
        ctype = r.headers.get("content-type", "")
        if "html" in ctype or "xml" in ctype or ctype.startswith("text/"):
            text = _strip_tags(r.text)
            title_match = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
            title = _strip_tags(title_match.group(1)) if title_match else r.url
        else:
            text = f"(binary/unsupported content-type: {ctype or 'neznámý'})"
            title = r.url
        if getattr(ctx, "research", None):
            max_source = int(_web_cfg(ctx).get("research_source_max_chars", 200_000))
            ctx.research.record_source(
                r.url, title, text, ctype or "unknown", requested_url=url,
                max_chars=max_source,
            )
        limit = int(max_chars or _web_cfg(ctx).get("max_chars", 12000))
        return truncate(text, limit, "web_fetch")


def register_web_tools(reg) -> None:
    reg.register(WebSearchTool())
    reg.register(WebFetchTool())
