"""Funkcni testy Nemotron 3.5 Lightning proti llama-serveru (pred implementaci).

1) tool-calling pres OpenAI API (klicove pro naseho agenta)
2) reasoning stream (reasoning_content vs <think> v contentu)
3) "detailed thinking off" v system promptu vypina uvafovani
4) kvalita cestiny (ocni test)

Pouziti: python scripts/_test_nemotron_func.py [q4|q5]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = next(iter(sorted((ROOT / "runtime" / "llama").rglob("llama-server.exe"))))
MODELS = ROOT / "runtime" / "models"
PORT = 8098
BASE = f"http://127.0.0.1:{PORT}"

MODEL_FILE = {
    "q4": "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q4_K_XL.gguf",
    "q5": "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q5_K_XL.gguf",
}


def chat(payload: dict, timeout: float = 240) -> dict:
    req = urllib.request.Request(
        BASE + "/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chat_stream(payload: dict, timeout: float = 120) -> dict:
    payload = {**payload, "stream": True}
    req = urllib.request.Request(
        BASE + "/v1/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    saw_reasoning = False
    content_parts: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if delta.get("reasoning_content"):
                saw_reasoning = True
            if delta.get("content"):
                content_parts.append(delta["content"])
    return {"saw_reasoning": saw_reasoning, "content": "".join(content_parts)}


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "q4"
    model = MODELS / MODEL_FILE[which]
    assert model.exists(), model
    extra = ["--n-cpu-moe", "8"] if which == "q5" else []
    log = open(ROOT / "runtime" / "nemotron-func.log", "w", encoding="utf-8")
    argv = [str(SERVER), "-m", str(model), "--port", str(PORT), "--host", "127.0.0.1",
            "-ngl", "999", "-fa", "on", "-t", "8", "--no-webui", "-c", "131072",
            *extra]
    proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=0x08000000)
    try:
        t0 = time.time()
        while time.time() - t0 < 420:
            try:
                urllib.request.urlopen(BASE + "/health", timeout=3)
                break
            except Exception:
                time.sleep(2)
        else:
            print("FAIL: server nenastartoval")
            return 1
        print(f"[setup] {which} loaded in {time.time()-t0:.0f}s\n")

        # 1) tool-calling
        r = chat({
            "max_tokens": 300, "temperature": 0.6,
            "messages": [{"role": "user",
                          "content": "What is the weather in Prague right now? "
                                     "Use the get_weather tool."}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }],
            "tool_choice": "auto",
        })
        msg = r["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        args_ok = False
        if tool_calls:
            try:
                args_ok = "prague" in json.loads(
                    tool_calls[0]["function"]["arguments"].lower() and
                    tool_calls[0]["function"]["arguments"]).get("city", "").lower()
            except Exception:
                args_ok = False
        print("1) TOOL-CALLING:", "PASS" if tool_calls and args_ok else "FAIL",
              "| calls:", json.dumps(tool_calls)[:200])

        # 2) reasoning stream
        r2 = chat_stream({
            "max_tokens": 300, "temperature": 0.6,
            "messages": [{"role": "user",
                          "content": "How many letter r are in 'strawberry'? "
                                     "Think it through."}],
        })
        print("2) REASONING-STREAM:",
              "PASS (reasoning_content oddelene)" if r2["saw_reasoning"]
              else "INFO (bez reasoning_content; content zaci)" if "<think>" in r2["content"]
              else "FAIL (zadne uvafovani)",
              f"| content[:120]: {r2['content'][:120]!r}")

        # 3) detailed thinking off
        r3 = chat_stream({
            "max_tokens": 200, "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "detailed thinking off"},
                {"role": "user", "content": "Name three primary colors. Be brief."},
            ],
        })
        print("3) THINKING-OFF:",
              "PASS (bez reasoning, bez <think>)"
              if not r3["saw_reasoning"] and "<think>" not in r3["content"]
              else "FAIL",
              f"| content[:120]: {r3['content'][:120]!r}")

        # 4) cestina
        r4 = chat({
            "max_tokens": 250, "temperature": 0.6,
            "messages": [{"role": "system", "content": "detailed thinking on"},
                         {"role": "user", "content":
                          "Odpověz česky dvěma větami: jaký je hlavní rozdíl "
                          "mezi Mamba a Transformer architekturou?"}],
        })
        text = r4["choices"][0]["message"].get("content", "")
        print("4) CESTINA (ocni test):\n---\n" + text.strip()[:500] + "\n---")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
