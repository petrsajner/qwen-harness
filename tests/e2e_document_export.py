"""GPU E2E: Research follow-up exportuje existujici odpoved bez noveho hledani."""
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
    base = load_config()
    temporary = Path(tempfile.mkdtemp(prefix="qwen-export-e2e-"))
    try:
        if not servermgmt.health(base):
            print("[SKIP] llama-server nebezi")
            return 2
        data = base.data
        data["thinking"] = False
        data["work_mode"] = "research"
        data["agent"]["workspace"] = None
        data["paths"]["sessions_dir"] = str(temporary / "sessions")
        cfg = Config(data, root=ROOT)
        session = Session(
            cfg, system_prompt=build_system_prompt("chat", cfg, None, "research"),
            workspace=None, work_mode="research")
        session.add("user", "Puvodni vyzkumna otazka")
        session.add("assistant", "# Dulezity vysledek\n\nToto je ulozena synteza o inositolu.")
        agent = Agent(
            cfg, LLMClient(cfg), session, build_registry("chat", "research"),
            SafetyPolicy("auto"), mode="chat", work_mode="research")
        agent.new_task("Uloz predchozi vysledek jako PDF soubor jmenem e2e-research")

        final = None
        for _ in range(8):
            result = agent.step()
            if result.status is not Status.CONTINUE:
                final = result
                break
        calls = [call["function"]["name"] for message in session.messages
                 for call in message.get("tool_calls", [])]
        pdf = session.dir / "exports" / "e2e-research.pdf"
        if "export_document" not in calls or any(
                name in calls for name in ("web_search", "web_fetch", "read_project_document")):
            raise RuntimeError(f"Model zvolil spatne nastroje: {calls}")
        if not pdf.is_file():
            raise RuntimeError(f"PDF nevznikl: {pdf}")
        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
        if "inositolu" not in text:
            raise RuntimeError("PDF neobsahuje puvodni vysledek")
        if final is None or final.status is not Status.FINAL:
            raise RuntimeError(f"Export workflow neskoncil: {getattr(final, 'text', None)}")
        print(f"[OK] tools={calls}, pdf={pdf}, bytes={pdf.stat().st_size}")
        return 0
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
