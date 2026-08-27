"""E2E: isolated Edge DOM interaction, console, and vision screenshot."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.browser import BrowserSession
from harness.config import Config, load_config
from harness.session import Session
from harness.tools import browser as browser_tools
from harness.tools.base import AgentContext, ToolRegistry


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="qwen-browser-e2e-"))
    browser = None
    try:
        html = workspace / "index.html"
        html.write_text(
            "<!doctype html><html><head><title>Browser E2E</title></head><body>"
            "<label for='name'>Project name</label>"
            "<input id='name' placeholder='Enter project name'>"
            "<button id='run' onclick=\"document.querySelector('#result').textContent = "
            "'Created: ' + document.querySelector('#name').value; console.log('CREATE_OK')\">"
            "Create project</button><p id='result'>Waiting</p></body></html>",
            encoding="utf-8",
        )
        data = load_config().data
        data["paths"]["sessions_dir"] = str(workspace / "sessions")
        cfg = Config(data, root=ROOT)
        session = Session(cfg, session_id="browser-e2e", system_prompt="SYS")
        browser = BrowserSession(session)
        ctx = AgentContext(cfg=cfg, session=session, workspace=workspace, browser=browser)
        registry = ToolRegistry()
        browser_tools.register_browser_tools(registry)

        opened = json.loads(registry.execute("browser_open", {"url": html.as_uri()}, ctx))
        if opened.get("title") != "Browser E2E":
            raise RuntimeError(f"Page did not open: {opened}")
        snapshot = json.loads(registry.execute("browser_snapshot", {}, ctx))
        input_ref = next(item["ref"] for item in snapshot["elements"]
                         if "project name" in item["name"].lower()
                         and item["tag"] == "input")
        button_ref = next(item["ref"] for item in snapshot["elements"]
                          if "create project" in item["name"].lower())
        registry.execute("browser_fill", {"ref": input_ref, "text": "Atlas"}, ctx)
        registry.execute("browser_click", {"ref": button_ref}, ctx)
        registry.execute("browser_wait", {"milliseconds": 200}, ctx)
        after = json.loads(registry.execute("browser_snapshot", {}, ctx))
        if "Created: Atlas" not in after["text"]:
            raise RuntimeError(f"Interaction failed: {after['text']}")
        console = json.loads(registry.execute("browser_console", {}, ctx))
        if not any("CREATE_OK" in item.get("text", "") for item in console["entries"]):
            raise RuntimeError(f"Console event missing: {console}")
        screenshot = json.loads(registry.execute("browser_screenshot", {}, ctx))
        if not Path(screenshot["path"]).is_file() or not ctx.pending_images:
            raise RuntimeError(f"Screenshot not attached: {screenshot}")
        closed = json.loads(registry.execute("browser_close", {}, ctx))
        if not closed.get("closed"):
            raise RuntimeError(f"Browser did not close: {closed}")
        print("[OK] open -> snapshot -> fill -> click -> console -> screenshot -> close")
        return 0
    finally:
        if browser is not None:
            browser.shutdown()
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
