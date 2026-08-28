"""Pred-implementacni testy Nemotron 3.5 Lightning na llama-serveru (standalone).

Spousti llama-server s danymi argumenty, ceka na health, meri VRAM,
generovani (tok/s) a prompt-eval; vysledky tiskne jako tabulku.
Nemeni config.yaml ani harness - ciste vnejsi mereni.

Pouziti:
    python scripts/_test_nemotron.py            # cela baterie
    python scripts/_test_nemotron.py A1 A2      # jen vybrane pripady
    python scripts/_test_nemotron.py --list
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
Q4 = MODELS / "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q4_K_XL.gguf"
Q5 = MODELS / "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-UD-Q5_K_XL.gguf"
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"

# label: (model, extra_args, poznamka)
CASES: dict[str, tuple[Path, list[str], str]] = {
    # --- 32 GB: Q4_K_XL plne na GPU ---
    "A1": (Q4, ["-c", "131072"], "Q4_XL full-GPU 128k"),
    "A2": (Q4, ["-c", "262144"], "Q4_XL full-GPU 256k"),
    "A3": (Q4, ["-c", "524288"], "Q4_XL full-GPU 512k"),
    "A4": (Q4, ["-c", "1048576"], "Q4_XL full-GPU 1M"),
    # --- 32 GB: Q5_K_XL (30.4 GB vah) - plny GPU / pretok ---
    "B1": (Q5, ["-c", "131072"], "Q5_XL full-GPU 128k (oczekavame OOM limit)"),
    "B2": (Q5, ["-c", "262144", "--n-cpu-moe", "8"], "Q5_XL ncmoe=8 256k"),
    "B3": (Q5, ["-c", "524288", "--n-cpu-moe", "16"], "Q5_XL ncmoe=16 512k"),
    # --- 24 GB simulace: Q4_XL s pretokem do RAM ---
    "C1": (Q4, ["-c", "262144", "--n-cpu-moe", "6"], "Q4_XL ncmoe=6 256k (~24 GB cil)"),
    "C2": (Q4, ["-c", "524288", "--n-cpu-moe", "10"], "Q4_XL ncmoe=10 512k (~24 GB cil)"),
    "C3": (Q4, ["-c", "262144", "--n-cpu-moe", "14"], "Q4_XL ncmoe=14 256k (24 GB ciste)"),
    "C4": (Q4, ["-c", "524288", "--n-cpu-moe", "18"], "Q4_XL ncmoe=18 512k (24 GB ciste)"),
    "B4": (Q5, ["-c", "262144"], "Q5_XL full-GPU 256k (limit test)"),
}


def vram_used_gb() -> float:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
    return int(out) / 1024


def http_json(path: str, payload: dict | None = None, timeout: float = 300) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_health(deadline_s: float = 420) -> bool:
    start = time.time()
    while time.time() - start < deadline_s:
        try:
            urllib.request.urlopen(BASE + "/health", timeout=3)
            return True
        except Exception:
            time.sleep(2)
    return False


def bench_generation(n_tokens: int = 220) -> tuple[float, float]:
    """Vrati (tok/s generovani, pocet vygenerovanych tokenu)."""
    payload = {
        "model": "x", "max_tokens": n_tokens, "temperature": 0.6, "top_p": 0.95,
        "messages": [{"role": "user",
                      "content": "Write a clear 150-word summary of how hybrid "
                                 "Mamba-Transformer models save memory."}],
    }
    t0 = time.time()
    r = http_json("/v1/chat/completions", payload)
    dt = time.time() - t0
    usage = r.get("usage", {})
    gen = int(usage.get("completion_tokens", 0) or 0)
    return (gen / dt if gen and dt else 0.0), gen


def bench_prompt_eval() -> tuple[float, int]:
    """Vrati (prompt tok/s, pocet prompt tokenu) na ~8k tokenech."""
    filler = ("The quick brown fox jumps over the lazy dog while reviewing "
              "a distributed systems design document. ") * 700
    payload = {
        "model": "x", "max_tokens": 4, "temperature": 0.2,
        "messages": [{"role": "user",
                      "content": filler + "\n\nReply with the single word: ok"}],
    }
    t0 = time.time()
    r = http_json("/v1/chat/completions", payload, timeout=600)
    dt = time.time() - t0
    usage = r.get("usage", {})
    pt = int(usage.get("prompt_tokens", 0) or 0)
    return (pt / dt if pt and dt else 0.0), pt


def run_case(label: str) -> dict | None:
    model, extra, note = CASES[label]
    if not model.exists():
        print(f"[{label}] SKIP - {model.name} neni stazeny")
        return None
    log_path = ROOT / "runtime" / f"nemotron-{label}.log"
    argv = [str(SERVER), "-m", str(model), "--port", str(PORT), "--host", "127.0.0.1",
            "-ngl", "999", "-fa", "on", "-t", "8", "--no-webui",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", *extra]
    print(f"\n[{label}] {note}")
    print(f"[{label}] args: {' '.join(extra)}")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=0x08000000)
    try:
        t0 = time.time()
        ok = wait_health()
        load_s = time.time() - t0
        if not ok:
            tail = log_path.read_text(errors="replace")[-600:]
            print(f"[{label}] FAIL - server nedosel behem {load_s:.0f}s\n{tail}")
            return {"label": label, "note": note, "ok": False}
        vram = vram_used_gb()
        tps, gen = bench_generation()
        pps, ptoks = bench_prompt_eval()
        vram2 = vram_used_gb()
        row = {"label": label, "note": note, "ok": True, "load_s": round(load_s),
               "vram_idle_gb": round(vram, 1), "vram_after_gb": round(vram2, 1),
               "gen_tps": round(tps, 1), "gen_tokens": gen,
               "prompt_tps": round(pps, 0), "prompt_tokens": ptoks}
        print(f"[{label}] load {load_s:.0f}s | VRAM {vram:.1f}->{vram2:.1f} GB | "
              f"gen {tps:.1f} tok/s | prompt {pps:.0f} tok/s")
        return row
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(4)  # uvolneni VRAM


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "--list":
        for k, (_m, extra, note) in CASES.items():
            print(f"{k}: {note}  [{' '.join(extra)}]")
        return 0
    selected = [a for a in args if not a.startswith("-")] or list(CASES)
    results = []
    for label in selected:
        if label not in CASES:
            print(f"neznamy pripad {label} (--list pro vypis)")
            continue
        row = run_case(label)
        if row:
            results.append(row)
    print("\n===== SOUHRN =====")
    for r in results:
        if r["ok"]:
            print(f"{r['label']}: {r['note']:44s} VRAM {r['vram_after_gb']:5.1f} GB | "
                  f"gen {r['gen_tps']:6.1f} tok/s | pp {r['prompt_tps']:6.0f} tok/s | "
                  f"load {r['load_s']}s")
        else:
            print(f"{r['label']}: {r['note']} FAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
