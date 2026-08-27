"""GPU E2E: Qwen drives the isolated browser through semantic refs."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import servermgmt
from harness.agent import Agent, Status, build_registry
from harness.config import Config, load_config
from harness.llm import LLMClient
from harness.prompts import build_system_prompt
from harness.safety import SafetyPolicy
from harness.session import Session


def main() -> int:
    base_cfg = load_config()
    workspace = Path(tempfile.mkdtemp(prefix="qwen-browser-agent-e2e-"))
    agent = None
    try:
        html = workspace / "index.html"
        html.write_text(
            "<!doctype html><html><head><title>Project Creator</title></head><body>"
            "<h1>Project Creator</h1><label for='name'>Project name</label>"
            "<input id='name' placeholder='Enter project name'>"
            "<button onclick=\"document.querySelector('#result').textContent = "
            "'Created: ' + document.querySelector('#name').value; console.log('PROJECT_CREATED')\">"
            "Create project</button><p id='result'>No project yet</p></body></html>",
            encoding="utf-8",
        )
        data = base_cfg.data
        data["agent"]["workspace"] = str(workspace)
        data["agent"]["max_steps"] = 24
        data["paths"]["sessions_dir"] = str(workspace / "sessions")
        cfg = Config(data, root=ROOT)
        print("[server] start q4")
        if servermgmt.start(cfg, "q4") != 0:
            return 2
        session = Session(
            cfg, system_prompt=build_system_prompt("agent", cfg, workspace, "development"),
            workspace=str(workspace), work_mode="development")
        agent = Agent(
            cfg, LLMClient(cfg), session, build_registry("agent", "development"),
            SafetyPolicy("auto", max_steps=24), mode="agent", work_mode="development")
        task = (
            f"Use the isolated browser tools to test this local page: {html.as_uri()} . "
            "Open it, call browser_snapshot, fill Project name with Atlas, click Create project, "
            "take a fresh snapshot and verify the visible result says Created: Atlas. Then inspect "
            "browser_console for PROJECT_CREATED, capture browser_screenshot for vision, close the "
            "browser, and report the verified result. Do not edit any files."
        )
        agent.new_task(task)
        final = None
        for _ in range(40):
            result = agent.step(approve=True)
            if result.status is Status.NEEDS_CONFIRMATION:
                result = agent.step(approve=True)
            if result.status is not Status.CONTINUE:
                final = result
                break
        calls = [call["function"]["name"] for message in session.messages
                 for call in message.get("tool_calls", [])]
        required = {"browser_open", "browser_snapshot", "browser_fill", "browser_click",
                    "browser_console", "browser_screenshot", "browser_close"}
        if final is None or final.status is not Status.FINAL:
            raise RuntimeError(f"Browser agent did not finish: {getattr(final, 'status', None)}")
        if not required.issubset(calls):
            raise RuntimeError(f"Missing browser calls: required={required}, calls={calls}")
        tool_text = "\n".join(str(message.get("content") or "") for message in session.messages
                              if message.get("role") == "tool")
        if "Created: Atlas" not in tool_text or "PROJECT_CREATED" not in tool_text:
            raise RuntimeError("Qwen did not verify DOM and console results")
        print(f"[OK] tools={calls}")
        print("[OK] Qwen verified DOM, console, and vision screenshot")
        return 0
    finally:
        if agent is not None and agent.ctx.browser is not None:
            agent.ctx.browser.shutdown()
        servermgmt.stop(base_cfg, quiet=True)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
