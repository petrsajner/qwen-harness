# Qwen3.8-27B lokální harness

Harness pro lokální práci s modelem **Qwen3.8-27B** na **RTX 5090 (32 GB)** — 100% offline,
žádná data neopouštějí tvůj počítač.

## Funkce

- 💬 **Obecný chat** i **coding agent** (čtení/zápis souborů, shell, vyhledávání)
- 🖼️ **Analýza obrázků** — nativní vision model (mmproj)
- 🖱️ **Ovládání počítače** — screenshot → klikání, psaní, klávesy (pyautogui)
- 🔀 **Přepínatelné modely** — Q4_K_M (rychlá, 96k kontext) ⇄ Q5_K_M (kvalitní, 48k kontext)
- 🛡️ **Tři úrovně autonomie** — supervised (potvrzování každé akce) / semi / auto
- 🖥️ **Dvě UI** — terminál (TUI) + webové rozhraní (Gradio, jen localhost)
- 💾 **Session historie** — JSONL perzistence, obrázky uložené na disk

## Architektura

```
llama-server (llama.cpp, CUDA, RTX 5090)
   └─ OpenAI-kompatibilní API (localhost:8080)
        └─ harness/ (Python)
             ├─ agent loop + tool calling
             ├─ safety vrstva (potvrzování akcí)
             └─ UI: tui.py (terminál) + webapp.py (prohlížeč)
```

## Instalace

```bash
py -3.12 -m venv .venv
.venv/Scripts/python scripts/setup_env.py   # venv deps + llama.cpp + modely (~37 GB)
```

## Spuštění

```bash
# 1. inference server (llama-server + Qwen3.8-27B)
.venv/Scripts/python scripts/server.py start

# 2a. terminálové UI
.venv/Scripts/python tui.py

# 2b. nebo webové UI (http://127.0.0.1:7860)
.venv/Scripts/python webapp.py
```

## Rychlé příkazy (TUI)

```
/model q5            přepnutí na Q5_K_M (restart serveru)
/mode computer       režim ovládání počítače
/autonomy auto       úroveň autonomie (supervised|semi|auto)
/img <cesta>         přiložit obrázek
/save, /load, /clear session historie
```

## Bezpečnost

- **FAILSAFE je vždy aktivní**: strč myš do levého horního rohu obrazovky → okamžité přerušení GUI akcí
- V režimu `supervised` se každý zápis/shell/GUI krok potvrzuje Entrem
- Model vidí celou obrazovku — pozor na citlivé údaje a vizuální prompt injection
- Doporučení: pro platby/maily apod. vždy `supervised`

*(Detailní README dokončím po implementaci a testování.)*
