"""Unit testy jádra harnesu (bez GPU / serveru).

Spuštění:  .venv/Scripts/python tests/test_core.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.agent import build_registry
from harness.config import Config, load_config
from harness.llm import parse_tool_arguments
from harness.safety import Risk, SafetyPolicy
from harness.session import Session
from harness.tools.base import AgentContext, ToolRegistry

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {label}")


def test_config() -> None:
    print("[config]")
    cfg = load_config()
    check(cfg.model_key() in cfg.data["models"], "default model existuje v 'models'")
    check(cfg.base_url.startswith("http://127.0.0.1"), "base_url je localhost")
    check(cfg.model_file().name.endswith(".gguf"), "model_file ukazuje na GGUF")
    s = cfg.sampling(thinking=True)
    check(abs(s["temperature"] - 1.0) < 1e-9, "thinking sampling t=1.0")
    s2 = cfg.sampling(thinking=False)
    check(abs(s2["temperature"] - 0.7) < 1e-9, "non-thinking sampling t=0.7")


def test_safety() -> None:
    print("[safety]")
    sup = SafetyPolicy("supervised", max_steps=40, semi_max_steps=15)
    check(sup.needs_confirmation(Risk.WRITE), "supervised: WRITE potvrzení")
    check(not sup.needs_confirmation(Risk.SAFE), "supervised: SAFE bez potvrzení")
    sup.new_task()
    check(sup.step_limit() == 40, "supervised limit = max_steps")

    semi = SafetyPolicy("semi", max_steps=40, semi_max_steps=15)
    check(semi.needs_confirmation(Risk.WRITE), "semi: první WRITE potvrzení")
    semi.mark_confirmed()
    check(not semi.needs_confirmation(Risk.WRITE), "semi: další WRITE už bez potvrzení")
    check(semi.step_limit() == 15, "semi limit = semi_max_steps")

    auto = SafetyPolicy("auto", max_steps=40)
    check(not auto.needs_confirmation(Risk.WRITE), "auto: bez potvrzení")
    check(auto.step_limit() == 40, "auto limit = max_steps")

    try:
        SafetyPolicy("režimNaval")
        check(False, "invalid autonomy vyhodí výjimku")
    except ValueError:
        check(True, "invalid autonomy vyhodí výjimku")


def test_session() -> None:
    print("[session]")
    tmp = Path(tempfile.mkdtemp())
    try:
        data = load_config().data
        data["paths"]["sessions_dir"] = str(tmp / "sessions")
        cfg = Config(data, root=ROOT)
        s = Session(cfg, session_id="test-session", system_prompt="SYS")
        s.add("user", "ahoj")
        img = tmp / "obrazek.png"
        img.write_bytes(b"\x89PNG fake")
        s.add("user", "mrkni na to", images=[img])
        s.add("assistant", "", tool_calls=[{"id": "call_1", "type": "function",
                                            "function": {"name": "list_dir", "arguments": "{}"}}])
        s.add("tool", "result text", tool_call_id="call_1", name="list_dir")
        check((tmp / "sessions/test-session/messages.jsonl").exists(), "JSONL uložen")
        n_img = len(list((tmp / "sessions/test-session/images").glob("*.png")))
        check(n_img == 1, f"obrázek zkopírován do session ({n_img})")

        api = s.to_api_messages()
        check(api[0]["role"] == "system", "system prompt na začátku")
        img_msg = [m for m in api if isinstance(m.get("content"), list)]
        check(len(img_msg) == 1 and any(p["type"] == "image_url" for p in img_msg[0]["content"]),
              "obrázek renderován jako image_url data URL")

        loaded = Session.load(cfg, "test-session")
        check(len(loaded.messages) == 5, f"roundtrip zpráv (={len(loaded.messages)})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tools_fs_shell() -> None:
    print("[tools]")
    tmp = Path(tempfile.mkdtemp())
    try:
        data = load_config().data
        data["paths"]["sessions_dir"] = str(tmp / "sessions")
        cfg = Config(data, root=ROOT)
        session = Session(cfg, session_id="tool-test")
        ctx = AgentContext(cfg=cfg, session=session, workspace=tmp)

        reg = ToolRegistry()
        from harness.tools import fs, shell
        fs.register_fs_tools(reg)
        shell.register_shell_tools(reg)

        (tmp / "sub").mkdir()
        (tmp / "sub" / "a.txt").write_text("ahoj\nsvěte\nQWEN", encoding="utf-8")
        (tmp / "sub" / "data.py").write_text("x = 1\nQWEN_MARKER = 'zde'\n", encoding="utf-8")

        r = reg.execute("list_dir", {"path": "."}, ctx)
        check("sub" in r and "[DIR]" in r, "list_dir vidí adresář")

        r = reg.execute("read_file", {"path": "sub/a.txt"}, ctx)
        check("ahoj" in r and "3|" in r, "read_file vrací obsah s čísly řádků")

        r = reg.execute("write_file", {"path": "sub/new.md", "content": "# test"}, ctx)
        check((tmp / "sub" / "new.md").exists(), "write_file vytvořil soubor")

        r = reg.execute("search_files", {"query": "qwen_marker", "path": "."}, ctx)
        check("data.py:2" in r, "search_files case-insensitive nalezení")

        r = reg.execute("run_command", {"command": "echo hello-$((40+2))", "shell": "bash"}, ctx)
        check("hello-42" in r and "exit code: 0" in r, f"run_command bash funguje: {r[:60]}")

        r = reg.execute("run_command", {"command": "Write-Output 'ps-works'"}, ctx)
        check("ps-works" in r, f"run_command powershell funguje")

        r = reg.execute("run_command", {"command": "format c: /x"}, ctx)
        check("blocked" in r.lower(), "nebezpečný příkaz zablokován")

        r = reg.execute("read_file", {"path": "neexistuje.txt"}, ctx)
        check(r.startswith("ERROR"), "chyba čtení vrací ERROR text")

        schemas = reg.schemas()
        check(all(s["function"]["name"] for s in schemas) and len(schemas) == 5,
              f"schemas pro 5 nástrojů ({len(schemas)})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_registry_modes() -> None:
    print("[registry]")
    chat = build_registry("chat")
    agent = build_registry("agent")
    computer = build_registry("computer")
    check(len(chat.names()) == 0, "chat režim bez nástrojů")
    check({"list_dir", "run_command", "view_image"} <= set(agent.names()),
          f"agent režim: fs+shell+vision ({len(agent.names())})")
    check({"screenshot", "click", "type_text", "press_key"} <= set(computer.names()),
          f"computer režim: + GUI nástroje ({len(computer.names())})")
    click = computer.get("click")
    check(click.risk == Risk.WRITE, "click je WRITE risk")
    shot = computer.get("screenshot")
    check(shot.risk == Risk.SAFE, "screenshot je SAFE risk")


def test_parse_args() -> None:
    print("[llm helpers]")
    check(parse_tool_arguments('{"x": 1}') == {"x": 1}, "platný JSON")
    check(parse_tool_arguments("") == {}, "prázdné argumenty")
    check(parse_tool_arguments('blabla {"x": [1,2]} blabla') == {"x": [1, 2]},
          "JSON zasypaný v textu")


if __name__ == "__main__":
    test_config()
    test_safety()
    test_session()
    test_tools_fs_shell()
    test_registry_modes()
    test_parse_args()
    print(f"\n{'=' * 40}\nVÝSLEDEK: {PASS} ✓ / {FAIL} ✗")
    sys.exit(1 if FAIL else 0)
