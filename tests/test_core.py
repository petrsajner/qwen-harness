"""Unit testy jádra harnesu (bez GPU / serveru).

Spuštění:  .venv/Scripts/python tests/test_core.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import uuid
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
    check(set(chat.names()) == {"read_memory", "save_memory"},
          f"chat režim: jen memory nástroje ({chat.names()})")
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


def test_workspace() -> None:
    print("[workspace]")
    from harness.agent import Agent
    data = load_config().data
    data["paths"]["sessions_dir"] = str(Path(tempfile.mkdtemp()) / "sessions")
    cfg = Config(data, root=ROOT)
    tmp = Path(tempfile.mkdtemp())
    try:
        session = Session(cfg, session_id="ws-test")
        from harness.safety import SafetyPolicy
        agent = Agent(cfg, LLMStub(), session, build_registry("agent"),
                      SafetyPolicy("supervised"), mode="agent")
        # None -> cwd (výchozí)
        check(agent.workspace == Path.cwd().resolve(), "výchozí workspace = cwd")
        # nastavení adresáře
        p = agent.set_workspace(str(tmp))
        check(p == tmp.resolve() and agent.workspace == tmp.resolve(), "set_workspace adresář")
        # soubor -> nadřazený adresář
        f = tmp / "soubor.txt"
        f.write_text("x", encoding="utf-8")
        p2 = agent.set_workspace(str(f))
        check(p2 == tmp.resolve(), "soubor -> nadřazený adresář")
        # uvozovky kolem cesty
        p3 = agent.set_workspace(f'"{tmp}"')
        check(p3 == tmp.resolve(), "cesta v uvozovkách")
        # neexistující
        try:
            agent.set_workspace(tmp / "neexistuje")
            check(False, "neexistující cesta vyhodí ValueError")
        except ValueError:
            check(True, "neexistující cesta vyhodí ValueError")
        # nástroje řeší relativní cesty od workspace
        from harness.tools.base import AgentContext
        r = build_registry("agent").execute("read_file", {"path": "soubor.txt"}, agent.ctx)
        check("soubor.txt" in r and "1| x" in r, "read_file řeší cestu od workspace")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_shell_readonly() -> None:
    print("[shell read-only klasifikace]")
    from harness.tools.shell import RunCommandTool, is_read_only_command
    tool = RunCommandTool()
    safe = [
        "ls -la", "cat soubor.txt", "grep -r foo .", "git status", "git log --oneline",
        "git diff HEAD~1", "find . -name '*.py'", "echo ahoj", "ls | grep test",
        "cat a.txt; cat b.txt", "stat main.py", "wc -l *.py",
    ]
    unsafe = [
        "rm -rf x", "echo ahoj > soubor.txt", "cat x | tee y", "mkdir novy",
        "git push", "git commit -m x", "curl http://x", "ls; rm x",
        "echo $(rm x)", "npm install x", "grep x . > out", "git branch nova",
        "cp a b", "cat < vstup.txt", "python skript.py", "",
    ]
    for cmd in safe:
        check(is_read_only_command(cmd), f"SAFE: {cmd!r}")
    for cmd in unsafe:
        check(not is_read_only_command(cmd), f"WRITE: {cmd!r}")
    check(tool.risk_for({"command": "ls"}) == Risk.SAFE, "risk_for ls = SAFE")
    check(tool.risk_for({"command": "rm x"}) == Risk.WRITE, "risk_for rm = WRITE")


def test_context_compression() -> None:
    print("[ctx komprese - ne-destruktivní]")
    tmp = Path(tempfile.mkdtemp())
    try:
        data = load_config().data
        data["paths"]["sessions_dir"] = str(tmp / "sessions")
        cfg = Config(data, root=ROOT)
        s = Session(cfg, session_id="ctx-test", system_prompt="SYS")
        # napodob delsi konverzaci: 6x user/assistant/tool trojice
        for i in range(6):
            s.add("user", f"otazka {i} " + "x" * 500)
            s.add("assistant", "", tool_calls=[{"id": f"c{i}", "type": "function",
                                                "function": {"name": "read_file", "arguments": "{}"}}])
            s.add("tool", f"odpoved {i} " + "y" * 300, tool_call_id=f"c{i}", name="read_file")
        n_before = len(s.messages)
        est = s.estimate_context_tokens()
        check(est > 1000, f"odhad tokenů rozumný ({est})")

        ok = s.compress_to_summary("SOUHRN konverzace.", min_keep=6)
        check(ok, "komprese proběhla")
        check(len(s.messages) == n_before, f"historie NEDOTČENÁ ({len(s.messages)} == {n_before})")
        check(s.compression is not None and s.compression["cut"] > 1, "záznam komprese (cut)")

        # model vidí méně, uživatel vše
        api_view = s._view_messages()
        check(len(api_view) < n_before, f"modelův view menší ({len(api_view)} < {n_before})")
        check("SOUHRN konverzace." in api_view[1]["content"], "souhrn v modelově view")
        est2 = s.estimate_context_tokens()
        check(est2 < est, f"tokeny pro model klesly ({est} → {est2})")

        # view nezačíná osiřelým tool voláním
        first_role = api_view[2]["role"] if len(api_view) > 2 else None
        check(first_role in ("user", None), f"cut na user hranici (role={first_role})")

        # persist + roundtrip: historie i komprese
        loaded = Session.load(cfg, "ctx-test")
        check(len(loaded.messages) == n_before, "JSONL kompletní (roundtrip)")
        check(loaded.compression is not None and loaded.compression["cut"] == s.compression["cut"],
              "compression.json persistován")

        # druhá komprese posune cut dál
        s.add("user", "nova otazka " + "a" * 100)
        s.add("assistant", "nova odpoved " + "b" * 100)
        ok2 = s.compress_to_summary("SOUHRN 2.", min_keep=2)
        check(ok2 and s.compression["cut"] > loaded.compression["cut"],
              "druhá komprese posunula cut vpřed")

        # trim fallback posouvá cut, nemaže historii
        s2 = Session(cfg, session_id="trim-test", system_prompt="SYS")
        for i in range(10):
            s2.add("user", f"u{i} " + "z" * 2000)
            s2.add("assistant", f"a{i} " + "w" * 2000)
        big = s2.estimate_context_tokens()
        n2 = len(s2.messages)
        ok = s2.trim_to_budget(big // 2)
        check(ok and s2.estimate_context_tokens() <= big // 2,
              f"trim do rozpočtu ({big} → {s2.estimate_context_tokens()})")
        check(len(s2.messages) == n2, "trim nemaže historii (jen posouvá cut)")

        # tokenový rozpočet: obří tool výstupy v ocasu nespotřebují půlku kontextu
        s3 = Session(cfg, session_id="bigtail-test", system_prompt="SYS")
        for i in range(10):
            s3.add("user", f"q{i}")
            s3.add("assistant", "", tool_calls=[{"id": f"c{i}", "type": "function",
                                                 "function": {"name": "read_file", "arguments": "{}"}}])
            s3.add("tool", "T" * 6000, tool_call_id=f"c{i}", name="read_file")  # ~1.6k toků
        # 10 trojic ≈ 16k+ tokenů; rozpočet 6k → cut musí ořezat hluboko
        est_before = s3.estimate_context_tokens()
        ok = s3.compress_to_summary("SOUHRN", keep_tokens=6000)
        est_after = s3.estimate_context_tokens()
        check(ok and est_after <= 8000,
              f"tokenový rozpočet drží ocas ({est_before} → {est_after}, cíl ≤ 8000)")
        view = s3._view_messages()
        check(view[2]["role"] == "user", "token-based cut také na user hranici")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reasoning_effort_kwargs() -> None:
    print("[reasoning effort]")
    from harness.llm import _template_kwargs
    data = load_config().data
    data["thinking"] = True
    data["reasoning_effort"] = "low"
    check(_template_kwargs(Config(data, ROOT)) == {"chat_template_kwargs": {"reasoning_effort": "low"}},
          "effort low → template kwarg")
    data["reasoning_effort"] = "xhigh"
    check(_template_kwargs(Config(data, ROOT))["chat_template_kwargs"]["reasoning_effort"] == "xhigh",
          "effort xhigh")
    data["thinking"] = False
    check(_template_kwargs(Config(data, ROOT)) == {"chat_template_kwargs": {"thinking": False}},
          "thinking off má prioritu před effort")
    data["thinking"] = True
    data["reasoning_effort"] = "blbost"
    check(_template_kwargs(Config(data, ROOT)) == {}, "neplatný effort → bez kwarg (default šablony)")


def test_session_meta() -> None:
    print("[session meta + historie]")
    tmp = Path(tempfile.mkdtemp())
    try:
        data = load_config().data
        data["paths"]["sessions_dir"] = str(tmp / "sessions")
        cfg = Config(data, root=ROOT)
        # session s workspace + titulkem z prvního dotazu
        s = Session(cfg, session_id="meta-a", system_prompt="SYS", workspace=r"C:\projekty\Alfa")
        s.add("user", "Oprav bug v parseru")
        s.add("assistant", "hotovo")
        check(s.meta["title"] == "Oprav bug v parseru", "titulek z prvního dotazu")
        check((tmp / "sessions/meta-a/meta.json").exists(), "meta.json uložen")
        # protokolová poznámka se titulkem stát nesmí
        s2 = Session(cfg, session_id="meta-b", system_prompt="SYS")
        s2.add("user", "[TASK PROTOCOL - follow] abc")
        s2.add("user", "Skutečný dotaz")
        check(s2.meta["title"] == "Skutečný dotaz", "poznámka [..] se titulkem nestává")
        # výpis s metadaty, setříděný podle updated
        lst = Session.list_sessions(cfg)
        check({x["id"] for x in lst} == {"meta-a", "meta-b"}, "list_sessions vidí obě")
        a = next(x for x in lst if x["id"] == "meta-a")
        check(a["workspace"] == r"C:\projekty\Alfa" and a["title"] == "Oprav bug v parseru",
              "meta ve výpisu (workspace + titulek)")
        check(lst[0]["id"] == "meta-b", "novější session první")
        # stará session bez meta → titulek dohoní z první user zprávy
        s3 = Session(cfg, session_id="meta-old", system_prompt="SYS")
        s3.add("user", "Starý dotaz bez mety")
        (tmp / "sessions/meta-old/meta.json").unlink()
        loaded = Session.load(cfg, "meta-old")
        check(loaded.meta["title"] == "Starý dotaz bez mety", "zpětná kompatibilita titulku")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class LLMStub:
    """Fake LLM se scénářem - vrací předpřipravené odpovědi v pořadí."""
    def __init__(self, script=None):
        from harness.llm import AssistantResult
        self.script = list(script or [])
        self.calls = 0

    def stream(self, messages, tools=None, max_tokens=None, on_text=None, on_reasoning=None, **kw):
        self.calls += 1
        self.last_messages = messages
        return self.script.pop(0)


def _tc(name, args="{}"):
    return {"id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": args}}


def test_communication_protocol() -> None:
    print("[komunikační protokol]")
    from harness.agent import Agent, Status
    from harness.llm import AssistantResult
    from harness.safety import SafetyPolicy
    data = load_config().data
    data["paths"]["sessions_dir"] = str(Path(tempfile.mkdtemp()) / "sessions")
    cfg = Config(data, root=ROOT)
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg.agent["workspace"] = str(tmp)

        def make_agent(script):
            session = Session(cfg, session_id=f"proto-{uuid.uuid4().hex[:6]}")
            llm = LLMStub(script)
            agent = Agent(cfg, llm, session, build_registry("agent"),
                          SafetyPolicy("auto"), mode="agent")
            return agent, session

        # 1) protokolová poznámka se přidá k úloze (a staré se odstraní)
        agent, session = make_agent([])
        agent.new_task("udelej neco")
        notes = [m for m in session.messages if "[TASK PROTOCOL" in str(m.get("content"))]
        check(len(notes) == 1, "TASK PROTOCOL poznámka přidána (user role)")
        agent.new_task("dalsi ukol")
        notes = [m for m in session.messages if "[TASK PROTOCOL" in str(m.get("content"))]
        check(len(notes) == 1, "stará poznámka nahrazena (ne hromadí se)")

        # 2) progress nudge po 4 tool-krocích bez slov
        script = [AssistantResult(tool_calls=[_tc("list_dir", '{"path": "."}')]) for _ in range(4)]
        script.append(AssistantResult(content="hotovo"))
        agent, session = make_agent(script)
        agent.new_task("prohledat adresar")
        statuses = [agent.step(approve=True).status for _ in range(5)]
        prog = [m for m in session.messages if "[PROGRESS UPDATE" in str(m.get("content"))]
        check(len(prog) >= 1, f"PROGRESS nudge po 4 krocích (počet: {len(prog)})")

        # 3) vynucení strukturovaného souhrnu po úloze s nástroji
        script = [
            AssistantResult(tool_calls=[_tc("list_dir")]),
            AssistantResult(tool_calls=[_tc("list_dir"), _tc("list_dir")]),
            AssistantResult(content="jen kratka odpoved"),   # nezaklad vyzaduje souhrn
            AssistantResult(content="✅ Hotovo: nic\n- **x**: y"),  # strukturovany
        ]
        agent, session = make_agent(script)
        agent.new_task("test souhrnu")
        r1 = agent.step(approve=True)
        r2 = agent.step(approve=True)
        r3 = agent.step(approve=True)
        check(r3.status is Status.CONTINUE, "krátká odpověď po nástrojích → vynucen souhrn (CONTINUE)")
        notes = [m for m in session.messages if "[FINAL SUMMARY" in str(m.get("content"))]
        check(len(notes) == 1, "SUMMARY poznámka vložena")
        r4 = agent.step(approve=True)
        check(r4.status is Status.FINAL and "✅" in r4.text, "druhý průchod → FINAL se souhrnem")
        check(agent.llm.calls == 4, "žádné zbytečné navíc volání")

        # 4) chat režim: žádný protokol (bez nástrojů netřeba)
        session = Session(cfg, session_id="chat-proto")
        agent = Agent(cfg, LLMStub([]), session, build_registry("chat"), SafetyPolicy("auto"), mode="chat")
        agent.new_task("ahoj")
        notes = [m for m in session.messages if "[TASK PROTOCOL" in str(m.get("content"))]
        check(not notes, "chat režim bez protokolu")

        # 5) přetečení kontextu → komprese + retry (1×), pak FINAL
        class OverflowLLM(LLMStub):
            def stream(self, messages, **kw):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("request exceeds the available context size")
                from harness.llm import AssistantResult
                return AssistantResult(content="✅ po kompresi OK")

        from harness.agent import Status as St
        for i in range(8):
            (tmp / f"f{i}.txt").write_text("x" * 400, encoding="utf-8")
        session = Session(cfg, session_id="ovf-test", system_prompt="SYS")
        ovf_llm = OverflowLLM()
        agent = Agent(cfg, ovf_llm, session, build_registry("agent"), SafetyPolicy("auto"), mode="agent")
        agent.llm = ovf_llm
        # naplnění session, aby komprese měla co zahodit
        for i in range(8):
            session.add("user", f"q{i} " + "y" * 900)
            session.add("assistant", f"a{i} " + "z" * 900)
        agent.new_task("dalsi dotaz")
        agent._steps = 0
        r = agent.step(approve=True)
        check(r.status is St.CONTINUE, "overflow → CONTINUE (komprese + retry)")
        r2 = agent.step(approve=True)
        check(r2.status is St.FINAL and ovf_llm.calls == 2,
              "retry po kompresi uspěl (2 volání)")
        # druhé přetečení už retry nedostane → ERROR
        class AlwaysOverflow(LLMStub):
            def stream(self, messages, **kw):
                self.calls += 1
                raise RuntimeError("prompt is too long: 999999 > 98304")
        ao = AlwaysOverflow()
        agent.llm = ao
        agent._overflow_retried = False
        session.add("user", "znovu preteceni")
        agent.safety.new_task()
        r3 = agent.step(approve=True)
        r4 = agent.step(approve=True)
        check(r4.status is St.ERROR and ao.calls == 2, "druhé přetečení → ERROR (žádná smyčka)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_config()
    test_safety()
    test_session()
    test_tools_fs_shell()
    test_registry_modes()
    test_parse_args()
    test_reasoning_effort_kwargs()
    test_shell_readonly()
    test_workspace()
    test_session_meta()
    test_context_compression()
    test_communication_protocol()
    print(f"\n{'=' * 40}\nVÝSLEDEK: {PASS} ✓ / {FAIL} ✗")
    sys.exit(1 if FAIL else 0)
