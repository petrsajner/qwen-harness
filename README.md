# Qwen3.8-27B lokální harness

Harness pro lokální práci s modelem **Qwen3.8-27B** (Apache 2.0) na **RTX 5090 (32 GB)** —
100% offline, žádná data neopouštějí tvůj počítač.

## Funkce

- 💬 **Obecný chat** i **coding agent** (čtení/zápis souborů, shell, vyhledávání)
- 📁 **Workspace (složka projektu)** — vyber adresář v WUI; Qwen z něj čte/zapisuje přímo
  z disku, zdrojové dokumenty nemusíš nahrávat do chatu. Pamatené naposledy použité.
- 🖼️ **Analýza obrázků** — nativní vision (mmproj), včetně screenshotů
- 🖱️ **Ovládání počítače** — screenshot → klikání, psaní, klávesy (pyautogui + mss)
- 🔀 **Přepínatelné modely** — Q4_K_M (96k kontext, ~82 tok/s) ⇄ Q5_K_M (48k, ~73 tok/s)
- 🧠 **Thinking on/off** — režim uvažování modelu (přepínatelný za běhu)
- 🛡️ **Tři úrovně autonomie** — supervised / semi / auto (kdykoliv přepnutelné);
  čtecí příkazy (`ls`, `cat`, `grep`, `git log`…) nepotřebují potvrzení ani v supervised
- 🖥️ **Dvě UI** — terminál (TUI) + webové rozhraní (Gradio, jen 127.0.0.1)
  — WUI: Ctrl+Enter odesílá, chyby zobrazuje jako zprávy v chatu
- 💾 **Session historie** — JSONL perzistence, obrázky uložené na disk

## Architektura

```
runtime/  (gitignored)                harness/  (Python)
┌──────────────────────────┐         ┌────────────────────────────────┐
│ llama-server.exe         │◄──API───│ JÁDRO                          │
│ (llama.cpp b10549        │  OpenAI │ • LLM klient (streaming+tools) │
│  CUDA 13.3, Blackwell)   │         │ • agent loop (step/resume)     │
│ Qwen3.8-27B GGUF         │         │ • tools: fs/shell/vision/      │
│  Q4_K_M (16.5 GB, 96k ctx)│        │   computer-use                 │
│  Q5_K_M (19.8 GB, 48k ctx)│        │ • safety vrstva (autonomie)    │
│ mmproj-F16 (vision, 0.9 GB)│       │ • sessions (JSONL + obrázky)   │
└──────────────────────────┘         ├────────────────────────────────┤
                                     │ UI: tui.py (terminál)          │
                                     │     webapp.py (prohlížeč)      │
                                     └────────────────────────────────┘
```

## Instalace (jednorázově)

```bash
py -3.12 -m venv .venv
.venv/Scripts/python scripts/setup_env.py --model all
```

Stáhne a připraví vše: pip závislosti, llama.cpp CUDA binárky (~540 MB),
Q4_K_M + Q5_K_M + mmproj (~37 GB) z `unsloth/Qwen3.8-27B-GGUF`.

## Spuštění

```bash
# 1) inference server (llama-server, načtení ~10 s)
.venv/Scripts/python scripts/server.py start          # výchozí model (q4)
.venv/Scripts/python scripts/server.py start q5       # nebo rovnou q5

# 2a) terminálové UI
.venv/Scripts/python tui.py

# 2b) webové UI → http://127.0.0.1:7860
.venv/Scripts/python webapp.py
```

Server se dá ovládat i z TUI (`/server start|stop|status`) a z web UI (tlačítka).

## Desktop aplikace (Windows)

**`qwen_app.py`** — nativní okno s kompletním životním cyklem:

```bash
.venv/Scripts/pythonw qwen_app.py     # bez konzole (pro zástupce)
.venv/Scripts/python qwen_app.py      # s diagnostickou konzolí
```

- **START**: automaticky nastartuje llama-server (pokud neběží) + Web UI a otevře
  nativní okno (WebView2). Chybí-li prostředí, nabídne opravu.
- **KONEC**: zavření okna zastaví Web UI **i llama-server** a uvolní VRAM
  (ověřeno: 28 GB → 3,4 GB). Fallback bez pywebview: systémový prohlížeč.

### Instalátor (Setup.exe)

```bash
installer/build_installer.bat     # vytvoří dist/QwenHarness-Setup-1.0.0.exe
```

- Instaluje do `%LOCALAPPDATA%\QwenHarness` (bez admin práv), Start Menu +
  volitelně desktop ikona, čeština, odinstalace standardně přes Windows
- **První spuštění** (`run_app.bat` zástupcem) automaticky: vytvoří venv,
  stáhne závislosti, llama.cpp (~540 MB) a modely (~37 GB) — pak už jen otvírá appku
- Pozn.: odinstalace nechá stažené modely a sessions
  (`%LOCALAPPDATA%\QwenHarness\runtime`, `\sessions`) — smaž ručně, pokud nechceš.

## TUI příkazy

```
/ws [cesta]             zobraz/nastav složku projektu (workspace)
/model q4|q5            přepnutí modelu (restart serveru, ~10 s)
/mode chat|agent|computer     režim: chat | coding nástroje | + ovládání PC
/autonomy supervised|semi|auto   úroveň autonomie
/thinking on|off        režim uvažování modelu
/img <cesta>            přiložit obrázek k další zprávě
/screenshot             přiložit screenshot obrazovky
/new /sessions /load <id>   správa session
/server status|start|stop    správa llama-serveru
```

## Režimy práce

| Režim | Nástroje | Využití |
|---|---|---|
| `chat` | žádné | obecný chat, dotazy, analýza obrázků |
| `agent` | list_dir, read/write_file, search_files, run_command, view_image | coding, správa souborů |
| `computer` | + screenshot, click, type_text, press_key, scroll, move_mouse | ovládání počítače |

## Autonomie a bezpečnost

| Autonomie | Chování |
|---|---|
| `supervised` | **každá** WRITE akce (zápis, shell, klik…) vyžaduje potvrzení [y/n/a] |
| `semi` | potvrzení jen první akce v úloze, pak běží do limitu 15 kroků |
| `auto` | bez potvrzení, tvrdý limit kroků (default 40) |

- 🛑 **FAILSAFE vždy zapnutý**: strč myš do **levého horního rohu** obrazovky → okamžité přerušení GUI akcí
- Souřadnice kliků model zadává v pixelech obrázku, který viděl — harness je automaticky přepočítává na reálné rozlišení (i po downscale screenshotu)
- Model vidí celou obrazovku — pozor na citlivé údaje; text na obrazovce může obsahovat prompt injection (systémový prompt model varuje, ale potvrzování je hlavní ochrana)
- Pro platby, maily apod. vždy používej `supervised`

## Benchmark (RTX 5090, thinking off)

| Model | Generování | VRAM | Kontext |
|---|---|---|---|
| Q4_K_M | **~82 tok/s** | ~28 / 32 GB | 128k |
| Q5_K_M | **~73 tok/s** | ~30 / 32 GB | 96k |

TTFT ~1–2 s. Vlastní benchmark: `.venv/Scripts/python scripts/bench.py [--model q5]`

## Testy

```bash
.venv/Scripts/python tests/test_core.py     # unit testy jádra (36, bez GPU)
.venv/Scripts/python tests/e2e_smoke.py     # E2E: chat + tool calling + vision (GPU)
```

## Konfigurace (`config.yaml`)

- `server.extra_args` — další vlajky llama-serveru (např. MTP draft model pro urychlení)
- `agent.max_steps`, `semi_max_steps`, `shell_timeout`
- `computer.screenshot_max_edge` — downscale screenshotu (úspora tokenů)
- `models.*.ctx_size` — velikost kontextu (pozor na VRAM)

## Struktura

```
harness/          jádro: config, llm, agent, safety, session, prompts, servermgmt
harness/tools/    fs, shell, vision (view_image), computer (screenshot/click/…)
scripts/          setup_env, download_llama, download_models, server, bench
tests/            test_core (unit), e2e_smoke (GPU)
tui.py            terminálové UI
webapp.py         webové UI (Gradio 6)
runtime/          llama.cpp + GGUF modely (gitignored)
sessions/         historie konverzací (gitignored)
```

## Poznámky

- llama-server default port se v budoucnu změní na 9931 (nyní 8080, viz notice v logu)
- Alternativa k llama.cpp je SGLang/vLLM ve WSL2 (rychlejší batch, složitější setup) —
  pro computer-use na Windows je nativní llama.cpp nejstabilnější cesta
- MTP (multi-token prediction) modul lze přidat jako draft model přes `server.extra_args`
