# Lokální AI harness

Harness pro lokální práci s modely **Qwen3.8-27B** a **Ornith 1.5 35B-A3B** na **RTX 5090 (32 GB)** —
100% offline, žádná data neopouštějí tvůj počítač.

## Funkce

- 💬 **Obecný chat** i **coding agent** (čtení/zápis souborů, shell, vyhledávání)
- 📁 **Workspace (složka projektu)** — vyber adresář v WUI; Qwen z něj čte/zapisuje přímo
  z disku, zdrojové dokumenty nemusíš nahrávat do chatu. Pamatené naposledy použité.
- 🖼️ **Analýza obrázků** — nativní vision (mmproj), včetně screenshotů
- 🖱️ **Ovládání počítače** — screenshot → klikání, psaní, klávesy (pyautogui + mss)
- 🔀 **Přepínatelné modely** — Qwen Q4/Q5 a Ornith Abliterated Q5
- 🎚️ **Přesnost KV cache** — Qwen F16 pro přesnost, nebo Q8 pro dvojnásobný kontext; Ornith pevně Q8
- 🧠 **Thinking on/off** — režim uvažování modelu (přepínatelný za běhu)
- 🛡️ **Tři úrovně autonomie** — supervised / semi / auto (kdykoliv přepnutelné);
  čtecí příkazy (`ls`, `cat`, `grep`, `git log`…) nepotřebují potvrzení ani v supervised
- 🖥️ **Dvě UI** — terminál (TUI) + webové rozhraní (Gradio, jen 127.0.0.1)
  — kompaktní seskupený sidebar; WUI: Ctrl+Enter odesílá, chyby zobrazuje v chatu
- 💾 **Session historie** — JSONL perzistence, obrázky uložené na disk
- ↩️ **Obnovovací bod každé úlohy** — atomické patchování, přehled změn a návrat všech souborů jedním tlačítkem
- ⏱️ **Dlouhé operace na pozadí** — průběžný výstup, timeout, stdin a zastavení process tree
- 🧭 **Automatický přehled projektu a kontextu** — repo snapshot, připnuté soubory a viditelné využití kontextu
- 🌿 **Práce s chatem** — retry, úprava posledního dotazu, undo kola, větve, hledání a export/import
- 🧭 **Steering za běhu** — další zpráva přesměruje rozpracovanou odpověď po dokončení věty
- 📌 **Připnuté soubory** — vybrané instrukce nebo architektura zůstávají v kontextu daného chatu
- 🧰 **Volitelné skills** — model vidí stručný katalog a celý SKILL.md načte jen podle potřeby
- 🎛️ **Oddělené pracovní režimy** — Diskuze, Výzkum, Psaní, Vývoj a Počítač s vlastními prompty a nástroji

## Architektura

```
runtime/  (gitignored)                harness/  (Python)
┌──────────────────────────┐         ┌────────────────────────────────┐
│ llama-server.exe         │◄──API───│ JÁDRO                          │
│ (llama.cpp b10549        │  OpenAI │ • LLM klient (streaming+tools) │
│  CUDA 13.3, Blackwell)   │         │ • agent loop (step/resume)     │
│ Qwen3.8-27B GGUF         │         │ • tools: fs/shell/vision/      │
│ Q4: F16 128k / Q8 256k   │         │   computer-use                 │
│ Q5: F16 96k / Q8 192k    │         │ • safety vrstva (autonomie)    │
│ Ornith Abliterated Q5 (128k)│      │                                │
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
Qwen Q4/Q5, Ornith Abliterated Q5 a oba vision projektory (~59 GiB).

## Spuštění

```bash
# 1) inference server (llama-server, načtení ~10 s)
.venv/Scripts/python scripts/server.py start          # výchozí model (q5 + Q8 KV)
.venv/Scripts/python scripts/server.py start q5       # nebo rovnou q5
.venv/Scripts/python scripts/server.py start ornith_q5 # Ornith reasoning

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
installer/build_installer.bat     # vytvoří dist/QwenHarness-Setup-<verze>.exe
```

- Instaluje do `%LOCALAPPDATA%\QwenHarness` (bez admin práv), Start Menu +
  volitelně desktop ikona, čeština, odinstalace standardně přes Windows
- **První spuštění** (`run_app.bat` zástupcem) automaticky: vytvoří venv,
  stáhne závislosti, llama.cpp (~540 MB) a modely (~59 GiB) — pak už jen otvírá appku
- Při aktualizaci se změna `requirements.txt` pozná automaticky a doinstalují se jen
  potřebné Python balíčky; modely ani ostatní runtime data se nestahují znovu.
- Pozn.: odinstalace nechá stažené modely a sessions
  (`%LOCALAPPDATA%\QwenHarness\runtime`, `\sessions`) — smaž ručně, pokud nechceš.

## TUI příkazy

```
/ws [cesta]             zobraz/nastav složku projektu (workspace)
/model q4|q5|ornith_q5   přepnutí modelu (restart serveru)
/mode chat|agent|computer     režim: chat | coding nástroje | + ovládání PC
/autonomy supervised|semi|auto   úroveň autonomie
/thinking xhigh|medium|low|off   hloubka uvažování modelu
/img <cesta>            přiložit obrázek k další zprávě
/screenshot             přiložit screenshot obrazovky
/new /sessions /load <id>   správa session
/server status|start|stop    správa llama-serveru
```

## Režimy práce

| Pracovní režim | Nástroje | Využití |
|---|---|---|
| `discussion` | paměť, internet, projektové dokumenty | běžný chat, nápady a diskuze bez coding pravidel |
| `research` | internet, dokumenty, research ledger | rešerše se všemi zdroji a povinnou závěrečnou syntézou |
| `writing` | dokumenty, patch, checkpoint a rollback | scénáře, články, reporty a textové revize |
| `development` | patch, Git, shell, testy, repo snapshot | coding agent |
| `computer` | development + screenshot a GUI nástroje | ovládání počítače |

Každá konverzace si ukládá vlastní pracovní režim. Projekt může obsahovat libovolný počet
konverzací v různých režimech a pamatuje si svůj výchozí režim pro nové chaty.

### Tři vrstvy paměti

Každý chat dostává do system promptu celé tři paměťové dokumenty, které se na něj vztahují:

1. `memory/GLOBAL.md` — společné preference a fakta pro všechny druhy práce a projekty.
2. Paměť aktivního pracovního režimu — společná napříč projekty stejného typu. Původní
   `memory/MEMORY.md` je zachovaný jako paměť režimu Vývoj; ostatní jsou v `memory/modes/`.
3. `<projekt>/QWEN_MEMORY.md` — fakta a rozhodnutí platná jen pro konkrétní projekt.

Model ukládá nové informace přes `save_memory` s explicitním scope `global`, `mode` nebo
`project`. Přepnutí chatu automaticky přepne i jeho režimovou a projektovou vrstvu.

### Výzkumný režim

- `web_search`, `web_fetch` a lokální dokumenty se zapisují do persistentního `research.json`.
- Zdroje se nefiltrují ani nehodnotí podle původu či domnělé důvěryhodnosti.
- Protichůdná, negativní, nejistá a menšinová tvrzení musí zůstat v syntéze viditelná.
- Coverage kontrola ověří, že každý načtený source ID je v závěru uveden.
- Kompletní ledger lze exportovat ze sidebaru.
- Před prvním hledáním vznikne persistentní plán dílčích otázek a vyhledávacích úhlů.
- Internetové HTML, text, PDF a DOCX se extrahují a ukládají do stejného ledgeru.
- Hotovou syntézu lze exportovat do DOCX nebo PDF.
- Krátké průběžné komentáře modelu a pracovní draft před syntézou zůstávají v historii chatu.
- Požadavek na uložení hotového výsledku používá přímo `export_document` a nezakládá nový výzkum.

### Psaní

- Finální text lze exportovat do Markdownu, strukturovaného DOCX nebo PDF s podporou češtiny.
- Exportované dokumenty jsou součástí task checkpointu a lze je vrátit stejným rollbackem.
- Dokumentový export je dostupný ve všech pracovních režimech; bez projektu se ukládá k session.

### Obnovení a výkon

- Rozpracovaný agentní krok a pending potvrzení se ukládají do `task-state.json`.
- Background procesy zapisují do persistentních logů a po restartu se znovu připojí podle PID.
- Nezávislé read-only tool calls běží bounded paralelně; každá sada se zápisem zůstává sekvenční.
- Hledání v historii používá SQLite FTS5 index místo opakovaného skenování všech JSONL.
- Generování nemá aplikační limit výstupních tokenů; končí přirozeně nebo na fyzické hranici kontextu modelu.
- Stop obchází frontu, ukončí generování na nejbližší větě a zachová hotovou část odpovědi.
- Zprávy a změny kontextu sdílejí jednu frontu, takže nový dotaz ani přesun chatu nepřepíše běžící úlohu.

## Autonomie a bezpečnost

| Autonomie | Chování |
|---|---|
| `supervised` | **každá** WRITE akce (zápis, shell, klik…) vyžaduje potvrzení [y/n/a] |
| `semi` | potvrzení jen první WRITE akce v úloze, potom pokračuje bez limitu |
| `auto` | bez potvrzení a bez limitu agentních kroků |

- 🛑 **FAILSAFE vždy zapnutý**: strč myš do **levého horního rohu** obrazovky → okamžité přerušení GUI akcí
- Souřadnice kliků model zadává v pixelech obrázku, který viděl — harness je automaticky přepočítává na reálné rozlišení (i po downscale screenshotu)
- Model vidí celou obrazovku — pozor na citlivé údaje; text na obrazovce může obsahovat prompt injection (systémový prompt model varuje, ale potvrzování je hlavní ochrana)
- Pro platby, maily apod. vždy používej `supervised`

## Benchmark (RTX 5090, thinking off)

| Model | Generování | VRAM | Kontext |
|---|---|---|---|
| Q4_K_M | **~82 tok/s** | podle KV profilu | F16 128k / Q8 256k |
| Q5_K_M | **~73 tok/s** | podle KV profilu | F16 96k / Q8 192k |

TTFT ~1–2 s. Vlastní benchmark: `.venv/Scripts/python scripts/bench.py [--model q5]`

## Testy

```bash
.venv/Scripts/python tests/test_core.py     # unit testy jádra (bez GPU)
.venv/Scripts/python tests/e2e_smoke.py     # E2E: chat + tool calling + vision (GPU)
.venv/Scripts/python tests/e2e_model_switch.py      # E2E: Q4 → Q5 background switch
.venv/Scripts/python tests/e2e_coding_workflow.py   # E2E: patch → test → rollback
.venv/Scripts/python tests/e2e_research_workflow.py # E2E: protichůdné zdroje → syntéza
.venv/Scripts/python tests/e2e_document_export.py   # E2E: Research výsledek → PDF bez nového hledání
```

## Coding workflow

- Existující soubory agent mění přes `apply_patch`; před první změnou vznikne persistentní checkpoint.
- Sidebar ukazuje pouze lidský seznam vytvořených/upravených souborů a nabízí návrat celé úlohy.
- `start_project_check` automaticky najde hlavní testovací příkaz a spustí ho jako dlouhou operaci.
- Strukturované Git nástroje pracují pouze se soubory aktuální úlohy, pokud nejsou cesty zadány výslovně.
- Technický diff, procesní výstup a repo mapa jsou dostupné agentovi; hlavní uživatelské UI zůstává chatové.

## Konfigurace (`config.yaml`)

- `server.extra_args` — další ověřené vlajky llama-serveru
- `agent.max_steps`, `semi_max_steps` (`0` = bez omezení), `shell_timeout`
- `computer.screenshot_max_edge` — downscale screenshotu (úspora tokenů)
- `models.*.ctx_size` — velikost kontextu (pozor na VRAM)

## Struktura

```
harness/          jádro: config, llm, agent, safety, session, prompts, servermgmt
harness/tools/    fs, shell, vision (view_image), computer (screenshot/click/…)
skills/           distribuované volitelné SKILL.md postupy
user-skills/      vlastní trvalé skilly (instalátor je nepřepisuje)
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
