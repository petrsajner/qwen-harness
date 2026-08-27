"""Semantic browser tools backed by an isolated headless Edge session."""
from __future__ import annotations

import json
from pathlib import Path

from harness.safety import Risk
from harness.tools.base import AgentContext, Tool


def _browser(ctx: AgentContext):
    if ctx.browser is None:
        raise RuntimeError("browser session manager unavailable")
    return ctx.browser


class BrowserOpenTool(Tool):
    name = "browser_open"
    description = ("Open a URL in the isolated headless Edge session. Use browser_snapshot after "
                   "navigation to inspect page text and obtain fresh element refs.")
    parameters = {"url": {"type": "string"}}
    required = ["url"]

    def run(self, ctx: AgentContext, url: str) -> str:
        return json.dumps(_browser(ctx).open(url), ensure_ascii=False, indent=2)


class BrowserSnapshotTool(Tool):
    name = "browser_snapshot"
    parallel_safe = False
    description = ("Return current URL/title, visible page text, and interactive elements with "
                   "stable refs such as e1. Call again after page-changing actions.")
    parameters = {
        "max_text_chars": {"type": "integer", "description": "Visible text limit (default 12000)"},
        "max_elements": {"type": "integer", "description": "Interactive element limit (default 160)"},
    }

    def run(self, ctx: AgentContext, max_text_chars: int = 12_000,
            max_elements: int = 160) -> str:
        return json.dumps(
            _browser(ctx).snapshot(max_text_chars, max_elements),
            ensure_ascii=False, indent=2)


class BrowserClickTool(Tool):
    name = "browser_click"
    description = "Click one element ref returned by the latest browser_snapshot."
    parameters = {"ref": {"type": "string"}}
    required = ["ref"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, ref: str) -> str:
        return json.dumps(_browser(ctx).click(ref), ensure_ascii=False, indent=2)


class BrowserFillTool(Tool):
    name = "browser_fill"
    description = "Replace the value of an input/textarea ref returned by browser_snapshot."
    parameters = {"ref": {"type": "string"}, "text": {"type": "string"}}
    required = ["ref", "text"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, ref: str, text: str) -> str:
        return json.dumps(_browser(ctx).fill(ref, text), ensure_ascii=False, indent=2)


class BrowserHoverTool(Tool):
    name = "browser_hover"
    description = "Hover one element ref to reveal hover states, menus, or tooltips."
    parameters = {"ref": {"type": "string"}}
    required = ["ref"]

    def run(self, ctx: AgentContext, ref: str) -> str:
        return json.dumps(_browser(ctx).hover(ref), ensure_ascii=False, indent=2)


class BrowserSelectTool(Tool):
    name = "browser_select"
    description = "Select a native dropdown option by value or visible label."
    parameters = {"ref": {"type": "string"}, "value": {"type": "string"}}
    required = ["ref", "value"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, ref: str, value: str) -> str:
        return json.dumps(_browser(ctx).select(ref, value), ensure_ascii=False, indent=2)


class BrowserScrollTool(Tool):
    name = "browser_scroll"
    description = "Scroll the page or one scrollable element vertically by a pixel delta."
    parameters = {"delta_y": {"type": "integer"},
                  "ref": {"type": "string", "description": "Optional scrollable element ref"}}
    required = ["delta_y"]

    def run(self, ctx: AgentContext, delta_y: int, ref: str | None = None) -> str:
        return json.dumps(_browser(ctx).scroll(delta_y, ref), ensure_ascii=False, indent=2)


class BrowserUploadTool(Tool):
    name = "browser_upload"
    description = "Set a local file on an <input type=file> ref. Relative paths use the project."
    parameters = {"ref": {"type": "string"}, "path": {"type": "string"}}
    required = ["ref", "path"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, ref: str, path: str) -> str:
        target = ctx.resolve(path)
        return json.dumps(_browser(ctx).upload(ref, str(target)), ensure_ascii=False, indent=2)


class BrowserDownloadTool(Tool):
    name = "browser_download"
    description = "Click a ref that starts a download and save it in the active chat browser folder."
    parameters = {"ref": {"type": "string"}}
    required = ["ref"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, ref: str) -> str:
        return json.dumps(_browser(ctx).download(ref), ensure_ascii=False, indent=2)


class BrowserViewportTool(Tool):
    name = "browser_viewport"
    description = "Set the isolated browser viewport for desktop, tablet, or mobile layout checks."
    parameters = {"width": {"type": "integer"}, "height": {"type": "integer"}}
    required = ["width", "height"]

    def run(self, ctx: AgentContext, width: int, height: int) -> str:
        return json.dumps(_browser(ctx).viewport(width, height), ensure_ascii=False, indent=2)


class BrowserPressTool(Tool):
    name = "browser_press"
    description = "Press a browser key/combo such as Enter, Escape, Control+L, or Control+Enter."
    parameters = {"key": {"type": "string"}}
    required = ["key"]
    risk = Risk.WRITE

    def run(self, ctx: AgentContext, key: str) -> str:
        return json.dumps(_browser(ctx).press(key), ensure_ascii=False, indent=2)


class BrowserWaitTool(Tool):
    name = "browser_wait"
    description = "Wait briefly for UI/network changes, then return the current page status."
    parameters = {"milliseconds": {"type": "integer"}}
    required = ["milliseconds"]

    def run(self, ctx: AgentContext, milliseconds: int) -> str:
        return json.dumps(_browser(ctx).wait(milliseconds), ensure_ascii=False, indent=2)


class BrowserScreenshotTool(Tool):
    name = "browser_screenshot"
    description = ("Capture the current page and attach it to the next model request for native "
                   "vision analysis. Use after implementing or changing visible UI.")
    parameters = {"full_page": {"type": "boolean", "description": "Capture full page"}}

    def run(self, ctx: AgentContext, full_page: bool = False) -> str:
        result = _browser(ctx).screenshot(full_page)
        path = Path(result["path"])
        ctx.pending_images.append(path)
        return json.dumps(result, ensure_ascii=False, indent=2)


class BrowserConsoleTool(Tool):
    name = "browser_console"
    description = "Read browser console messages incrementally. Reuse the returned cursor."
    parameters = {"cursor": {"type": "integer"}, "max_entries": {"type": "integer"}}

    def run(self, ctx: AgentContext, cursor: int = 0, max_entries: int = 100) -> str:
        return json.dumps(
            _browser(ctx).console(cursor, max_entries), ensure_ascii=False, indent=2)


class BrowserNetworkTool(Tool):
    name = "browser_network"
    description = "Read HTTP responses and failed requests incrementally. Reuse the cursor."
    parameters = {"cursor": {"type": "integer"}, "max_entries": {"type": "integer"}}

    def run(self, ctx: AgentContext, cursor: int = 0, max_entries: int = 100) -> str:
        return json.dumps(
            _browser(ctx).network(cursor, max_entries), ensure_ascii=False, indent=2)


class BrowserCloseTool(Tool):
    name = "browser_close"
    description = "Close the isolated browser session and release its processes."
    parameters = {}

    def run(self, ctx: AgentContext) -> str:
        return json.dumps(_browser(ctx).close(), ensure_ascii=False, indent=2)


def register_browser_tools(registry) -> None:
    registry.register(BrowserOpenTool())
    registry.register(BrowserSnapshotTool())
    registry.register(BrowserClickTool())
    registry.register(BrowserFillTool())
    registry.register(BrowserHoverTool())
    registry.register(BrowserSelectTool())
    registry.register(BrowserScrollTool())
    registry.register(BrowserUploadTool())
    registry.register(BrowserDownloadTool())
    registry.register(BrowserViewportTool())
    registry.register(BrowserPressTool())
    registry.register(BrowserWaitTool())
    registry.register(BrowserScreenshotTool())
    registry.register(BrowserConsoleTool())
    registry.register(BrowserNetworkTool())
    registry.register(BrowserCloseTool())
