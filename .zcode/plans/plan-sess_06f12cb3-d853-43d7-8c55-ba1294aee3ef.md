# Qwen3.8-27B lokální harness pro RTX 5090

## Cíl
Postavit stabilní lokální aplikaci pro práci s **Qwen3.8-27B** (open weights, Apache 2.0): coding agent + obecný chat + analýza obrázků + ovládání počítače. Vše běží 100% lokálně na Windows 11 + RTX 5090 (32 GB).

## Architektura

```
runtime/ (gitignored)          harness/ (Python, v git)
┌────────────────────┐        ┌──────────────────────────────┐
│ llama-server.exe   │◄──API──│ JÁDRO                        │
│ (llama.cpp, CUDA)  │        │ • LLM klient (OpenAI API)    │
│ Qwen3.8-27B GGUF   │        │ • agent loop + tool calling  │
│ Q4_K_M + Q5_K_M    │        │ • nástroje: fs/shell/vision/ │
│ + mmproj (vision)  │        │   computer-use               │
└────────────────────┘        │ • safety vrstva (režimy)     │
                              │ • session persistence        │
                              ├──────────────────────────────┤
                              │ UI: TUI (terminál) + Web     │
                              │ (Gradio, 127.0.0.1)          │
                              └──────────────────────────────┘
```

**Backend inference:** llama.cpp `llama-server` — oficiální prebuilt Windows CUDA buildy s podporou Blackwell (sm_120); tvůj driver 591.86/CUDA 13.1 splňuje požadavek. OpenAI-kompatibilní API včetně tool callingu a vision přes mmproj.

**Proč ne SGLang/vLLM:** oficiálně doporučované, ale na Windows jen přes WSL2 — složitější a GUI ovládání počítače by muselo zasahovat do Windows hostitele. llama.cpp běží nativně a je pro tento účel nejstabilnější. (WSL2 fallback zmíním v README.)

## Struktura repozitáře `C:\Users\Petr\Documents\QWEN local`

```
.git, .gitignore, README.md (CZ), config.yaml, requirements.txt
scripts/  setup.ps1 (env+venv+deps), download_models.py (Q4+Q5+mmproj),
          server.py (start/stop/switch modelu, PID file), bench.py (tok/s)
harness/  config.py, llm.py, agent.py, safety.py, session.py, prompts.py
          tools/ (base, fs, shell, vision, computer)
tui.py    terminálové UI (rich/prompt_toolkit)
webapp.py webové UI (Gradio, chat + upload obrázků + přepínače)
runtime/  llama.cpp binárky + modely (gitignored)
```

## Klíčové vlastnosti

1. **Přepínatelné modely** — Q4_K_M (~16 GB, kontext ~96k) i Q5_K_M (~20 GB, kontext ~48k); přepnutí = restart llama-serveru s jiným GGUF přes `server.py switch`, z TUI i webu (/model příkaz). Sampling podle model cardu (thinking: t=1.0/top_p=0.95/top_k=20; non-thinking: t=0.7/top_p=0.8).
2. **Tři režimy práce** — `chat` (jen konverzace + obrázky), `agent` (coding nástroje: čtení/zápis souborů, shell, grep, git), `computer` (+ screenshot, click, type, key, scroll přes pyautogui/mss).
3. **Přepínatelná autonomie** (všechny 3 varianty, kdykoliv změnitelné):
   - `supervised` — každý zápis/shell/GUI krok vyžaduje Enter
   - `semi` — potvrzení na startu úlohy, pak max. N kroků, okamžité přerušení
   - `auto` — bez potvrzení, tvrdý limit kroků; **vždy** aktivní pyautogui FAILSAFE (myš do levého horního rohu = stop) a varování před prompt injection v systémovém promptu
4. **Vision** — obrázky v chatu (upload ve webu, `/img <cesta>` v TUI), model vidí screenshoty nativně (image_url formát přes llama-server multimodal API)
5. **Session persistence** — historie jako JSONL, `/save /load /clear`
6. **Vše česky** v UI, systémové prompty anglicky (lepší výkon modelu)

## Postup implementace (milestony + git commity)

1. **Git + scaffolding** — `git init`, .gitignore (runtime/, venv/, __pycache__, sessions/), kostra balíku, config.yaml, README
2. **Setup skript** — kontrola Pythonu 3.11+ (nabídne instalaci přes winget), venv, deps (openai, gradio, rich, prompt_toolkit, pyautogui, mss, pillow, pyyaml, huggingface_hub); download llama.cpp CUDA buildu; download GGUF Q4+Q5+mmproj z HF (vyberu nejlepší komunitní kvantizaci, ověřím mmproj; fallback: převod přes convert_hf_to_gguf.py)
3. **Server management** — start/stop/switch/health, správné flagy (`-ngl 999`, `--ctx-size`, `-fa`, `--mmproj`, port 8080), smoke test stability + VRAM monitoring
4. **Jádro harnessu** — LLM klient, tool registry, agent loop, safety vrstva, sessions
5. **TUI** — REPL se streamingem, barevný výpis tool callů, slash příkazy
6. **Web UI** — Gradio chat s obrázky, přepínače model/režim/autonomie
7. **Testy a benchmarky** — end-to-end testy (vytvoř soubor, přečti obrázek/screenshot, supervised klikací test v Poznámkovém bloku), bench.py na porovnání Q4 vs Q5, finální README

## Ověření
- Smoke test serveru hned po downloadu modelů (riziko: nová hybridní architektura v llama.cpp — existuje 716 komunitních GGUF kvantizací, předpokládám podporu; kdyby ne, řešením je build z masteru nebo LM Studio runtime)
- E2E: agent vytvoří soubor; VL popíše screenshot; computer-use napíše text v Notepadu (supervised)
- Bench tok/s Q4 vs Q5, monitoring VRAM při dlouhém kontextu

## Předpoklady
- Disk: 1,4 TB volných ✓ (modely ~40 GB dohromady)
- Internet pro download modelů (~40 GB, jednorázově)
- Vše ostatní instaluje setup skript