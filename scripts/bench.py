"""Benchmark: rychlost generování na běžícím llama-serveru.

Použití:
    python scripts/bench.py              # aktuální model
    python scripts/bench.py --model q5   # přepne na q5 a změří
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.config import load_config  # noqa: E402
from harness.llm import LLMClient  # noqa: E402
from harness import servermgmt  # noqa: E402

PROMPTS = [
    ("krátká odpověď", "Reply with exactly: OK", 32),
    ("generování", "Write numbers from 1 to 300, one per line.", 700),
    ("prompt eval", "Summarize the following: " + ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 60)
     + "\nOne sentence summary:", 200),
]


def bench(llm: LLMClient, thinking: bool) -> None:
    sampling = dict(llm.cfg.sampling(thinking))
    extra_body = {}
    if "top_k" in sampling:
        extra_body["top_k"] = sampling.pop("top_k")
    for label, prompt, max_tokens in PROMPTS:
        # TTFT (streaming)
        t0 = time.time()
        first = None
        text = ""
        usage = None
        try:
            stream = llm.client.chat.completions.create(
                model=llm.model_name, stream=True, max_tokens=max_tokens,
                stream_options={"include_usage": True},
                messages=[{"role": "user", "content": prompt}],
                extra_body=extra_body, **sampling,
            )
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                d = chunk.choices[0].delta
                if d and d.content:
                    if first is None:
                        first = time.time()
                    text += d.content
        except Exception as e:
            print(f"  [{label}] CHYBA: {e}")
            continue
        t_end = time.time()
        if first is None:
            print(f"  [{label}] žádná odpověď (model odmítl?)")
            continue
        # skutečné tokeny z usage (fallback: odhad ~4 znaky/token)
        if usage and getattr(usage, "completion_tokens", None):
            n_tok = usage.completion_tokens
            tok_src = "usage"
        else:
            n_tok = len(text) / 4
            tok_src = "odhad"
        ttft = first - t0
        gen_time = t_end - first
        tps = n_tok / gen_time if gen_time > 0 else 0
        print(f"  {label:<14} TTFT {ttft:5.2f}s | gen {gen_time:5.2f}s | {tps:6.1f} tok/s ({n_tok:.0f} tok, {tok_src})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", help="přepnout na model (q4/q5) před benchmarket")
    ap.add_argument("--no-thinking", action="store_true", help="vypnout thinking režim")
    args = ap.parse_args()
    cfg = load_config()

    if args.model:
        if servermgmt.start(cfg, args.model) != 0:
            return 1
    elif not servermgmt.health(cfg):
        print("[CHYBA] Server neběží. Start: python scripts/server.py start")
        return 1

    print(f"\n=== BENCHMARK  model={servermgmt.running_model(cfg)}  "
          f"thinking={'off' if args.no_thinking else 'on'}  {servermgmt.vram_str()} ===\n")
    llm = LLMClient(cfg)
    bench(llm, thinking=not args.no_thinking)
    print(f"\n{servermgmt.vram_str()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
