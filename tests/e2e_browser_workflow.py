"""E2E: isolated Edge DOM interaction, console, and vision screenshot."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
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
        upload = workspace / "sample.txt"
        upload.write_text("UPLOAD_OK", encoding="utf-8")
        html.write_text(
            "<!doctype html><html><head><title>Browser E2E</title></head><body>"
            "<label for='name'>Project name</label>"
            "<input id='name' placeholder='Enter project name'>"
            "<label for='mode'>Mode</label><select id='mode' "
            "onchange=\"document.querySelector('#mode-result').textContent='Mode: '+this.value\">"
            "<option value='fast'>Fast</option><option value='quality'>Quality</option></select>"
            "<p id='mode-result'>Mode: fast</p>"
            "<label for='upload'>Upload file</label><input id='upload' type='file' "
            "onchange=\"document.querySelector('#upload-result').textContent=this.files[0].name\">"
            "<p id='upload-result'>No file</p>"
            "<button id='hover' onmouseenter=\"this.textContent='Hovered'\">Hover me</button>"
            "<button id='run' onclick=\"document.querySelector('#result').textContent = "
            "'Created: ' + document.querySelector('#name').value; console.log('CREATE_OK')\">"
            "Create project</button><p id='result'>Waiting</p>"
            "<div style='height:1200px'></div>"
            "<a id='download' download='result.txt' href='data:text/plain,DOWNLOAD_OK'>Download report</a>"
            "</body></html>",
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
        select_ref = next(item["ref"] for item in snapshot["elements"]
                          if item["tag"] == "select")
        upload_ref = next(item["ref"] for item in snapshot["elements"]
                          if item["type"] == "file")
        hover_ref = next(item["ref"] for item in snapshot["elements"]
                         if "hover me" in item["name"].lower())
        download_ref = next(item["ref"] for item in snapshot["elements"]
                            if "download report" in item["name"].lower())
        registry.execute("browser_fill", {"ref": input_ref, "text": "Atlas"}, ctx)
        registry.execute("browser_select", {"ref": select_ref, "value": "Quality"}, ctx)
        registry.execute("browser_upload", {"ref": upload_ref, "path": "sample.txt"}, ctx)
        registry.execute("browser_hover", {"ref": hover_ref}, ctx)
        registry.execute("browser_click", {"ref": button_ref}, ctx)
        registry.execute("browser_scroll", {"delta_y": 900}, ctx)
        viewport = json.loads(registry.execute(
            "browser_viewport", {"width": 820, "height": 700}, ctx))
        registry.execute("browser_wait", {"milliseconds": 200}, ctx)
        after = json.loads(registry.execute("browser_snapshot", {}, ctx))
        if not all(text in after["text"] for text in
                   ("Created: Atlas", "Mode: quality", "sample.txt", "Hovered")):
            raise RuntimeError(f"Interaction failed: {after['text']}")
        console = json.loads(registry.execute("browser_console", {}, ctx))
        if not any("CREATE_OK" in item.get("text", "") for item in console["entries"]):
            raise RuntimeError(f"Console event missing: {console}")
        screenshot = json.loads(registry.execute("browser_screenshot", {}, ctx))
        if (not Path(screenshot["path"]).is_file() or not ctx.pending_images
                or viewport["viewport"] != {"width": 820, "height": 700}
                or screenshot["width"] != 820):
            raise RuntimeError(f"Screenshot not attached: {screenshot}")
        downloaded = json.loads(registry.execute(
            "browser_download", {"ref": download_ref}, ctx))
        if Path(downloaded["path"]).read_text(encoding="utf-8") != "DOWNLOAD_OK":
            raise RuntimeError(f"Download failed: {downloaded}")
        wait_result = {}

        def long_wait():
            wait_result["text"] = registry.execute(
                "browser_wait", {"milliseconds": 20_000}, ctx)

        thread = threading.Thread(target=long_wait)
        started = time.monotonic()
        thread.start()
        time.sleep(0.3)
        browser.cancel_active()
        thread.join(timeout=4)
        if thread.is_alive() or time.monotonic() - started >= 5:
            raise RuntimeError("Active browser operation was not cancelled promptly")
        closed = json.loads(registry.execute("browser_close", {}, ctx))
        if not closed.get("closed"):
            raise RuntimeError(f"Browser did not close: {closed}")
        print("[OK] DOM + select + hover + scroll + upload + download + viewport + vision + cancel")
        return 0
    finally:
        if browser is not None:
            browser.shutdown()
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
