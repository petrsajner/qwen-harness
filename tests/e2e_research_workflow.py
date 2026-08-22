"""GPU E2E: dva protichůdné webové zdroje -> ledger -> povinná syntéza."""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness import servermgmt
from harness.agent import Agent, Status, build_registry
from harness.config import Config, load_config
from harness.llm import LLMClient
from harness.prompts import system_prompt
from harness.safety import SafetyPolicy
from harness.session import Session


PAGES = {
    "/a": "<html><title>Výklad A</title><body>Zdroj A tvrdí, že událost byla plánovaná.</body></html>",
    "/b": "<html><title>Výklad B</title><body>Zdroj B tvrdí opak: událost byla spontánní.</body></html>",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path)
        if body is None:
            self.send_error(404)
            return
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


def main() -> int:
    base_cfg = load_config()
    workspace = Path(tempfile.mkdtemp(prefix="qwen-research-e2e-"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        data = base_cfg.data
        data["work_mode"] = "research"
        data["agent"]["workspace"] = str(workspace)
        data["agent"]["max_steps"] = 12
        data["paths"]["sessions_dir"] = str(workspace / "sessions")
        cfg = Config(data, root=ROOT)
        print("[server] start q4")
        if servermgmt.start(cfg, "q4") != 0:
            return 2
        session = Session(
            cfg, system_prompt=system_prompt("chat", "research"),
            workspace=str(workspace), work_mode="research")
        registry = build_registry("chat", "research")
        if any(name in registry.names() for name in ("apply_patch", "git_commit", "run_command")):
            raise RuntimeError("Research registry obsahuje coding nástroje")
        agent = Agent(
            cfg, LLMClient(cfg), session, registry,
            SafetyPolicy("auto", max_steps=12), mode="chat", work_mode="research")
        prompt = (
            "Proveď výzkum otázky, zda byla událost plánovaná nebo spontánní. "
            f"Povinně načti přes web_fetch oba zdroje: http://127.0.0.1:{port}/a a "
            f"http://127.0.0.1:{port}/b. Zachovej oba protichůdné výklady a potom odpověz česky."
        )
        agent.new_task(prompt)
        final = None
        for _ in range(20):
            result = agent.step()
            if result.status is Status.NEEDS_CONFIRMATION:
                result = agent.step(approve=True)
            if result.status is not Status.CONTINUE:
                final = result
                break
        run = agent.ctx.research.current()
        calls = [call["function"]["name"] for message in session.messages
                 for call in message.get("tool_calls", [])]
        if final is None or final.status is not Status.FINAL:
            raise RuntimeError(f"Research workflow nedokončen: {getattr(final, 'text', None)}")
        if len(run.get("sources", [])) != 2 or calls.count("web_fetch") < 2:
            raise RuntimeError(f"Nejsou zachovány oba zdroje: calls={calls}, run={run}")
        if "[S1]" not in final.text or "[S2]" not in final.text:
            raise RuntimeError(f"Coverage syntézy neobsahuje oba zdroje: {final.text}")
        if "plánovan" not in final.text.lower() or "spontán" not in final.text.lower():
            raise RuntimeError(f"Syntéza zamlčela jeden výklad: {final.text}")
        if any("credibility" in source or "trust" in source for source in run["sources"]):
            raise RuntimeError("Ledger obsahuje nepovolené hodnocení zdrojů")
        print(f"[OK] tools={calls}, sources={[source['id'] for source in run['sources']]}")
        print("[OK] protichůdné zdroje, ledger a coverage syntéza prošly")
        return 0
    finally:
        httpd.shutdown()
        servermgmt.stop(base_cfg, quiet=True)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
