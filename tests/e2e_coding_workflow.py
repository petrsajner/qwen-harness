"""GPU E2E: patch -> background test -> task rollback."""
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
from harness.prompts import system_prompt
from harness.safety import SafetyPolicy
from harness.session import Session


def main() -> int:
    base_cfg = load_config()
    workspace = Path(tempfile.mkdtemp(prefix="qwen-coding-e2e-"))
    try:
        (workspace / "target.py").write_text('VALUE = "before"\n', encoding="utf-8")
        (workspace / "tests").mkdir()
        (workspace / "tests" / "test_core.py").write_text(
            "from pathlib import Path\n"
            "text = Path('target.py').read_text(encoding='utf-8')\n"
            "assert 'VALUE = \"after\"' in text, text\n"
            "print('CODING-WORKFLOW-TEST-OK')\n",
            encoding="utf-8",
        )
        data = base_cfg.data
        data["agent"]["workspace"] = str(workspace)
        data["agent"]["max_steps"] = 20
        data["paths"]["sessions_dir"] = str(workspace / "sessions")
        cfg = Config(data, root=ROOT)

        print("[server] start q4")
        if servermgmt.start(cfg, "q4") != 0:
            return 2
        session = Session(cfg, system_prompt=system_prompt("agent"), workspace=str(workspace))
        agent = Agent(cfg, LLMClient(cfg), session, build_registry("agent"),
                      SafetyPolicy("auto", max_steps=20), mode="agent")
        prompt = (
            "V target.py změň přesně VALUE = \"before\" na VALUE = \"after\". "
            "Povinně použij nástroj apply_patch, nepoužívej write_file ani run_command. "
            "Potom povinně spusť start_project_check a poll_command opakuj, dokud test neskončí. "
            "Nakonec stručně potvrď výsledek."
        )
        agent.new_task(prompt)
        final = None
        for _ in range(30):
            result = agent.step()
            if result.status is Status.NEEDS_CONFIRMATION:
                result = agent.step(approve=True)
            if result.status is not Status.CONTINUE:
                final = result
                break
        calls = [call["function"]["name"] for message in session.messages
                 for call in message.get("tool_calls", [])]
        target = (workspace / "target.py").read_text(encoding="utf-8")
        required = {"apply_patch", "start_project_check", "poll_command"}
        if final is None or final.status is not Status.FINAL or not required.issubset(calls):
            raise RuntimeError(f"Agent workflow nedokončen: status={getattr(final, 'status', None)}, calls={calls}")
        if 'VALUE = "after"' not in target:
            raise RuntimeError(f"Patch se neaplikoval: {target!r}")
        tool_results = "\n".join(str(message.get("content", "")) for message in session.messages
                                 if message.get("role") == "tool")
        if "CODING-WORKFLOW-TEST-OK" not in tool_results:
            raise RuntimeError("Agent nedopolloval úspěšný výsledek testu")
        summary = agent.ctx.changes.summary()
        if not any(item["changed"] for item in summary["files"]):
            raise RuntimeError("Task journal neeviduje změnu")
        undo = agent.ctx.changes.undo()
        restored = (workspace / "target.py").read_text(encoding="utf-8")
        if undo["errors"] or 'VALUE = "before"' not in restored:
            raise RuntimeError(f"Rollback selhal: {undo}, content={restored!r}")
        print(f"[OK] tools={calls}")
        print("[OK] patch, background test a rollback prošly")
        return 0
    finally:
        servermgmt.stop(base_cfg, quiet=True)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
