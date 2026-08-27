"""Lokalizace UI: angličtina je základní jazyk, čeština je volitelná.

Použití:  t("English text") vrátí text v aktivním jazyce; neznámý řetězec
spadne zpět do angličtiny. Jazyk se volí na startu (webui-state.json má
přednost před souborem z instalátoru runtime/ui-language.txt) a ve web UI
jde přepnout za běhu dropdownem v Nastavení.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_lock = threading.Lock()
_current = "en"

LANGUAGES = {"en": "English", "cs": "Čeština"}

_ALIASES = {
    "en": "en", "english": "en", "eng": "en",
    "cs": "cs", "cz": "cs", "cze": "cs", "czech": "cs",
    "česky": "cs", "cestina": "cs",
}


def normalize(value) -> str:
    key = str(value or "").strip().lower()
    return _ALIASES.get(key, "en")


def set_language(value) -> str:
    global _current
    with _lock:
        _current = normalize(value)
    return _current


def get_language() -> str:
    return _current


def language_choices() -> list[tuple[str, str]]:
    return [(label, code) for code, label in LANGUAGES.items()]


def t(text: str, **fmt) -> str:
    if _current != "en":
        text = _CS.get(text, text)
    return text.format(**fmt) if fmt else text


def detect_language(root: Path) -> str | None:
    """Uložený jazyk UI: webui-state.json (volba uživatele) > ui-language.txt (instalátor)."""
    runtime = Path(root) / "runtime"
    try:
        saved = json.loads((runtime / "webui-state.json").read_text(encoding="utf-8"))
        lang = saved.get("language")
        if lang:
            return normalize(lang)
    except (OSError, ValueError):
        pass
    try:
        raw = (runtime / "ui-language.txt").read_text(encoding="utf-8").strip()
        if raw:
            return normalize(raw)
    except OSError:
        pass
    return None


_CS = {
    # ------------------------------------------------ runtime / live zprávy
    "🧰 <i>{detail} · generated ~{amount} chars</i>": "🧰 <i>{detail} · vytvořeno ~{amount} znaků</i>",
    "tool": "nástroj",
    "⏳ {sec}s without new tokens": "⏳ {sec}s bez nových tokenů",
    "💭 <i>thinking… ({sec}s)</i>": "💭 <i>uvažování… ({sec}s)</i>",
    "Preparing content": "Připravuji obsah",
    "Saving file": "Ukládám soubor",
    "Preparing file edits…": "Připravuji úpravy souborů…",
    "Applying file edits…": "Zapisuji úpravy souborů…",
    "Preparing a command or test…": "Připravuji příkaz nebo test…",
    "Running a command or test…": "Spouštím příkaz nebo test…",
    "Checking a long-running operation…": "Kontroluji průběh dlouhé operace…",
    "Reading file{target}…": "Čtu soubor{target}…",
    "Scanning the project…": "Procházím projekt…",
    "Updating the task plan…": "Aktualizuji plán úlohy…",
    "Preparing project validation…": "Připravuji kontrolu projektu…",
    "Opening the isolated browser…": "Otevírám izolovaný prohlížeč…",
    "Inspecting the rendered page…": "Kontroluji vykreslenou stránku…",
    "Interacting with the rendered page…": "Pracuji s vykreslenou stránkou…",
    "Capturing the rendered page for vision…": "Snímám stránku pro vision…",
    "Inspecting browser diagnostics…": "Kontroluji diagnostiku prohlížeče…",
    "Browsing web sources…": "Procházím internetové zdroje…",
    "Preparing": "Připravuji",
    "Running": "Provádím",

    # ------------------------------------------------ chat view
    "📦 **Context compressed** — everything above this marker is no longer visible to the model "
    "(it works from a summary). Your history stays complete.":
        "📦 **Kontext komprimován** — vše nad tímto markerem model už nevidí "
        "(pracuje se souhrnem). Pro tebe je historie zachovaná celá.",
    "🖼️ attached image: {name}": "🖼️ přiložen obrázek: {name}",
    "🖼️ +{count} image(s)": "🖼️ +{count} obrázek(ky)",

    # ------------------------------------------------ chyby / stavy agenta
    "❌ **An error occurred** — `{error}`":
        "❌ **Došlo k chybě** — `{error}`",
    "You can try continuing with another message. If the problem persists, try **🆕 New chat** "
    "or **▶ Start server**.":
        "Můžeš zkusit pokračovat další zprávou. Pokud problém přetrvává, "
        "zkus **🆕 Novou session** nebo **▶ Start serveru**.",
    "💡 *Looks like an inference server problem — try **▶ Start server**.*":
        "💡 *Vypadá to na problém s inference serverem — zkus **▶ Start serveru**.*",
    "💡 *A tool failed — try phrasing the task differently.*":
        "💡 *Nástroj selhal — zkus zadat úkol jinak.*",
    "🔌 **Connection to the server stalled** (the server is no longer generating but the response "
    "never arrived). The response was not completed — try sending the message again.":
        "🔌 **Spojení se serverem se zaseklo** (server už negeneruje, ale odpověď nedorazila). "
        "Odpověď nebyla dokončena — zkus zprávu odeslat znovu.",
    "**Waiting for action confirmation:**": "**Čekám na potvrzení akce:**",
    "✅ Allow": "✅ Povolit",
    "❌ Deny": "❌ Zamítnout",

    # ------------------------------------------------ toasty
    "Clarification received — finishing the current sentence and redirecting the running task.":
        "Upřesnění přijato - dokončuji větu a přesměrovávám běžící úlohu.",
    "Stop received — finishing the current sentence.": "Stop přijat - dokončuji nejbližší větu.",
    "No prompt in this chat to retry.": "V chatu není žádný dotaz k opakování.",
    "No prompt in this chat to edit.": "V chatu není žádný dotaz k úpravě.",
    "No round in this chat to undo.": "V chatu není žádné kolo k vrácení.",
    "The last question and answer were removed from the chat.":
        "Poslední otázka a odpověď byly z chatu odebrány.",
    "No prompt in this chat to fork.": "V chatu není žádný dotaz pro vytvoření větve.",
    "📦 Summarizing the older conversation (this takes a while)…":
        "📦 Vytvářím souhrn starší konverzace (chvíli trvá) ...",
    "Nothing to compress (conversation too short).": "Není co komprimovat (příliš krátká konverzace).",
    "📦 **Manual compression done** — ~{before}k → ~{after}k tokens. The model works from the "
    "summary; the history stays complete.":
        "📦 **Ruční komprese dokončena** — ~{before}k → ~{after}k tokenů. "
        "Model pracuje se souhrnem, historie zůstává celá.",
    "✅ Compressed: ~{before}k → ~{after}k tokens": "✅ Komprimováno: ~{before}k → ~{after}k tokenů",
    "This chat is empty — nothing to hand off.": "Session je prázdná - není co předávat.",
    "✅ New chat with the summary is ready": "✅ Nová session se souhrnem připravena",
    "Enter a new chat name.": "Zadej nový název chatu.",
    "✅ Chat renamed: {name}": "✅ Chat přejmenován: {name}",
    "✅ Chat loaded + context switched to {target}": "✅ Session načtena + kontext přepnut na {target}",
    "✅ Chat loaded: {title}": "✅ Session načtena: {title}",
    "No matches found in history.": "V historii nebyla nalezena žádná shoda.",
    "Select a JSONL chat export first.": "Nejdřív vyber JSONL export chatu.",
    "Chat imported as a new chat.": "Chat byl importován jako nová session.",
    "Chat import failed: {error}": "Import chatu selhal: {error}",
    "A model is already loading; wait for the current operation to finish.":
        "Model se už načítá; počkej na dokončení aktuální operace.",
    "A model is already loading; KV cache cannot be switched right now.":
        "Model se už načítá; KV cache nyní nelze přepnout.",
    "A model is already loading; restart cannot start right now.":
        "Model se už načítá; restart nyní nelze spustit.",
    "∅ No project — new chats will have no project":
        "∅ Bez projektu - nové chatty budou bez příslušnosti",
    "Project folder does not exist: {path}": "Složka projektu neexistuje: {path}",
    "📁 Project: {name}": "📁 Projekt: {name}",
    "📁 Project attached: {name}": "📁 Připojen projekt: {name}",
    "Enter a project name.": "Zadej název projektu.",
    "📁 Project created {name} → {path}": "📁 Vytvořen projekt {name} → {path}",
    "Select the project you want to delete first.": "Nejdřív vyber projekt, který chceš smazat.",
    "Project is no longer in the registry.": "Projekt už není v registru.",
    "Confirm full project deletion with a second click within 8 seconds.":
        "Potvrď úplné smazání projektu druhým kliknutím do 8 sekund.",
    "⚠️ **Click again: this permanently deletes the whole project, its chats and folder**  `{path}`":
        "⚠️ **Klikni znovu: nenávratně smažu celý projekt, jeho chaty a adresář**  `{path}`",
    "Project {name} including its folder and {count} chats was deleted.":
        "Projekt {name} včetně adresáře a {count} chatů byl smazán.",
    "Confirm deletion: click the button again within 6 s.":
        "Potvrď smazání: klikni na tlačítko znovu do 6 s.",
    "The active chat is not saved (empty) — nothing to delete.":
        "Aktivní chat není uložený (prázdný) - není co mazat.",
    "🗑 Chat deleted": "🗑 Chat smazán",
    "Chat no longer exists": "Chat už neexistuje",
    "Chat is not saved — send a message first.": "Chat není uložený - pošli nejdřív zprávu.",
    "Project '{name}' not found.": "Projekt '{name}' nenalezen.",
    "📁 Chat moved → {target}": "📁 Chat přesunut → {target}",
    "Opening: {path}": "Otevírám: {path}",
    "Opening skills folder: {folder}": "Otevírám složku skills: {folder}",
    "Skills folder cannot be opened: {error}": "Složku skills nelze otevřít: {error}",
    "Click a chat row in the table first (selection).":
        "Nejdřív klikni na řádek chatu v tabulce (výběr).",
    "Chat not found (already deleted?)": "Chat nenalezen (už smazán?)",
    "✅ Memory saved — the model will see it from the next message":
        "✅ Paměť uložena - model ji uvidí od další zprávy",
    "Restore point is not available.": "Obnovovací bod není dostupný.",
    "Some files could not be restored: {errors}": "Některé soubory se nepodařilo obnovit: {errors}",
    "Restored {count} files to their pre-task state.": "Vráceno {count} souborů do stavu před úlohou.",
    "No changes to revert in this task.": "V této úloze nejsou žádné změny k vrácení.",
    "Stopped long-running operations: {count}": "Zastaveno dlouhých operací: {count}",
    "No long-running operation is running right now.": "Žádná dlouhá operace právě neběží.",
    "<small>No long-running operation is running right now.</small>":
        "<small>Žádná dlouhá operace právě neběží.</small>",
    "Pinned to this chat: {name}": "Připnuto do tohoto chatu: {name}",
    "File is already pinned: {name}": "Soubor už je připnutý: {name}",
    "Failed to pin the file: {error}": "Soubor se nepodařilo připnout: {error}",
    "Unpinned files: {count}": "Odepnuto souborů: {count}",
    "The current chat has no research ledger yet.": "Aktuální chat zatím nemá výzkumný ledger.",
    "The current research has no completed synthesis yet.":
        "Aktuální výzkum ještě nemá dokončenou syntézu.",
    "No unfinished task is available.": "Žádná rozpracovaná úloha není k dispozici.",
    "Select a project first": "Nejdřív vyber projekt",

    # ------------------------------------------------ panely / status
    "<small>No unfinished task.</small>": "<small>Žádná rozpracovaná úloha.</small>",
    "waiting for confirmation": "čeká na potvrzení",
    "ready to continue": "připravená pokračovat",
    "Unfinished task": "Rozpracovaná úloha",
    "Step: {count}": "Krok: {count}",
    "just now": "právě teď",
    "{count} min ago": "před {count} min",
    "{count} h ago": "před {count} h",
    "{count} d ago": "před {count} d",
    "— no project —": "— bez projektu —",
    "(untitled)": "(bez titulku)",
    "no project": "bez projektu",
    "**Global for everything:** `{path}`": "**Globální pro vše:** `{path}`",
    "**For {mode} mode:** `{path}`": "**Pro režim {mode}:** `{path}`",
    "**Project:** `{path}`": "**Projektová:** `{path}`",
    "**Project:** — select a project first": "**Projektová:** — nejdřív vyber projekt",
    "(set a workspace — project memory will bind to it)":
        "(nastav workspace - pak se paměť projektu váže k němu)",
    "⏳ Loading {model}…": "⏳ Načítám {model}…",
    "🖥️ GPU VRAM: — · KV cache: {kv}": "🖥️ GPU VRAM: — · KV cache: {kv}",
    "❌ {model} — switch failed": "❌ {model} — přepnutí selhalo",
    "🟢 {model}": "🟢 {model}",
    "🖥️ GPU VRAM: {vram} · KV cache: {kv}": "🖥️ GPU VRAM: {vram} · KV cache: {kv}",
    "🔴 {model} — server is down": "🔴 {model} — server stojí",
    "📊 Chat context: ~{used}k / {limit}k tokens{live}{warn}":
        "📊 Kontext chatu: ~{used}k / {limit}k tokenů{live}{warn}",
    " · generating live: ~{count} tokens": " · živě generováno: ~{count} tokenů",
    "📊 Chat context: —": "📊 Kontext chatu: —",
    "📊 Context at {pct}% — auto-compression runs at 85%":
        "📊 Kontext na {pct} % — auto-komprese proběhne při 85 %",
    "📊 Context at {pct}% — near the limit! Consider 📦 Hand off (summary into a new chat)":
        "📊 Kontext na {pct} % — blízko limitu! Zvaž 📦 Předej (souhrn do nové session)",
    "<small>No files changed in the current task yet.</small>":
        "<small>V aktuální úloze zatím nebyly změněny žádné soubory.</small>",
    "**Changes in this task: {count}**": "**Změny této úlohy: {count}**",
    "Created": "Vytvořeno",
    "Modified": "Upraveno",
    "Deleted": "Smazáno",
    "Directory": "Složka",
    "<small>The task plan appears after a substantial task starts.</small>":
        "<small>Plán se objeví po zahájení větší úlohy.</small>",
    "<small>The model is inspecting the task before creating steps.</small>":
        "<small>Model úlohu nejprve prohlíží a připravuje kroky.</small>",
    "Current task": "Aktuální úloha",
    "**Last validation:** {mark} {label}": "**Poslední kontrola:** {mark} {label}",
    "✓ Final diff reviewed": "✓ Výsledný diff zkontrolován",
    "**Running operations: {count}**": "**Probíhající operace: {count}**",
    "**Context: ~{used}k / {limit}k tokens ({pct}%)**":
        "**Kontext: ~{used}k / {limit}k tokenů ({pct}%)**",
    "- The model sees {visible} of {total} messages": "- Model vidí {visible} z {total} zpráv",
    "- Images in the active context: {count}": "- Obrázky v aktivním kontextu: {count}",
    "- Older history: compressed": "- Starší historie: komprimovaná",
    "- Older history: full": "- Starší historie: plná",
    "- Conversation and attachments: ~{count}k tokens":
        "- Konverzace a přílohy: ~{count}k tokenů",
    "- Current project context: ~{count}k tokens":
        "- Aktuální kontext projektu: ~{count}k tokenů",
    "- Tool definitions: ~{count}k tokens": "- Definice nástrojů: ~{count}k tokenů",
    "- Pinned files: {count}": "- Připnuté soubory: {count}",
    "- Pinned files: none": "- Připnuté soubory: žádné",
    "The research ledger activates in Research mode.": "Research ledger se aktivuje v režimu Výzkum.",
    "<small>The research ledger activates in Research mode.</small>":
        "<small>Research ledger se aktivuje v režimu Výzkum.</small>",
    "Research starts after you send a question.": "Výzkum začne po odeslání otázky.",
    "<small>Research starts after you send a question.</small>":
        "<small>Výzkum začne po odeslání otázky.</small>",
    "collecting sources": "sběr podkladů",
    "synthesis complete": "syntéza dokončena",
    "waiting": "čeká",
    "**Research: {phase}**": "**Výzkum: {phase}**",
    "- Search queries: {count}": "- Vyhledávací dotazy: {count}",
    "- Links found: {count}": "- Nalezené odkazy: {count}",
    "- Sources read: {count}": "- Načtené zdroje: {count}",
    "- Sources are not filtered or ranked by origin": "- Zdroje nejsou filtrovány ani hodnoceny podle původu",
    "Research synthesis": "Výzkumná syntéza",
    "<small>click a row in the table → selects the chat (nothing is loaded)</small>":
        "<small>klikni na řádek v tabulce → vybere se chat (nic se nenačte)</small>",
    "<small>selected chat no longer exists</small>": "<small>vybraný chat už neexistuje</small>",
    "<small>📄 selected: <b>{title}</b> · {project} · {count} messages</small>":
        "<small>📄 vybráno: <b>{title}</b> · {project} · {count} zpráv</small>",
    "📁 Workspace: not set": "📁 Workspace: nenastaven",
    "📁 Workspace: {path}": "📁 Workspace: {path}",
    "✅ Workspace: {path}": "✅ Workspace: {path}",
    "Mode: **{mode}**": "Režim: **{mode}**",
    "Work mode: **{mode}**": "Pracovní režim: **{mode}**",
    "Autonomy: **{level}**": "Autonomie: **{level}**",
    "Thinking: **{level}**": "Přemýšlení: **{level}**",

    # ------------------------------------------------ UI popisky
    "Work mode": "Pracovní režim",
    "WORKSPACE": "PRACOVNÍ PROSTOR",
    "CURRENT WORK": "AKTUÁLNÍ PRÁCE",
    "Chats": "Chaty",
    "PROJECT CHATS": "CHATY PROJEKTU",
    "CHATS WITHOUT PROJECT": "CHATY BEZ PROJEKTU",
    "SEARCH": "HLEDÁNÍ",
    "Context": "Kontext",
    "Runtime": "Provoz",
    "Settings & help": "Nastavení a nápověda",
    "CHANGED FILES": "ZMĚNĚNÉ SOUBORY",
    "PROCESSES": "PROCESY",
    "BROWSER": "BROWSER",
    "MEMORY": "PAMĚŤ",
    "SKILLS": "SKILLS",
    "**{count} skills available**": "**Dostupných skills: {count}**",
    "<small>No skills available.</small>": "<small>Nejsou dostupné žádné skills.</small>",
    "MANUALS": "MANUÁLY",
    "PROJECT SETUP": "NASTAVENÍ PROJEKTU",
    "CHAT DATA": "DATA CHATU",
    "MODEL & BEHAVIOR": "MODEL A CHOVÁNÍ",
    "OFFLINE BACKUP": "OFFLINE ZÁLOHA",
    "Create backup": "Vytvořit zálohu",
    "Use as fallback": "Použít jako zálohu",
    "Verify SHA-256": "Ověřit SHA-256",
    "Clear selection": "Zapomenout výběr",
    "Creating backup": "Vytvářím zálohu",
    "Verifying backup": "Ověřuji zálohu",
    "Local fallback ready": "Lokální záloha připravena",
    "Python dependencies": "Python závislosti",
    "included": "obsažen",
    "missing": "chybí",
    "<small>No offline backup selected. Python 3.12 remains the only external prerequisite.</small>":
        "<small>Není vybraná offline záloha. Jediným externím předpokladem zůstává Python 3.12.</small>",
    "⚠️ Backup manifest cannot be read: {error}": "⚠️ Manifest zálohy nelze načíst: {error}",
    "Select a parent folder for the offline backup": "Vyberte nadřazenou složku pro offline zálohu",
    "Select a Qwen Harness offline backup": "Vyberte offline zálohu Qwen Harness",
    "Offline backup creation started. Progress is visible in Runtime.":
        "Vytváření offline zálohy začalo. Průběh je vidět v Provozu.",
    "The selected folder does not contain a valid backup manifest.":
        "Vybraná složka neobsahuje platný manifest zálohy.",
    "Offline backup selected: {path}": "Vybrána offline záloha: {path}",
    "Backup manifest cannot be read: {error}": "Manifest zálohy nelze načíst: {error}",
    "Select an offline backup first.": "Nejprve vyberte offline zálohu.",
    "Full SHA-256 verification started. Progress is visible in Runtime.":
        "Úplné ověření SHA-256 začalo. Průběh je vidět v Provozu.",
    "Offline backup selection cleared.": "Výběr offline zálohy byl zapomenut.",
    "Version": "Verze",
    "⚙ FEATURES": "⚙ FUNKCE",
    "Context & handoff": "Kontext a předání",
    "Compress": "Komprimovat",
    "Hand off": "Předat chatu",
    "Changes in this task": "Změny této úlohy",
    "Task progress": "Průběh úlohy",
    "Revert task changes": "Vrátit změny této úlohy",
    "Continue task": "Pokračovat v úloze",
    "Long-running operations": "Dlouhé operace",
    "Browser session": "Browser session",
    "Close browser": "Zavřít browser",
    "<small>No isolated browser session is running.</small>":
        "<small>Neběží žádná izolovaná browser session.</small>",
    "Untitled page": "Stránka bez názvu",
    "Isolated browser session closed.": "Izolovaná browser session byla zavřena.",
    "Stop running operations": "Zastavit běžící operace",
    "What the model currently sees": "Co model právě používá",
    "Pin file": "Připnout soubor",
    "Unpin all": "Odepnout vše",
    "Available skills": "Dostupné skills",
    "Open skills folder": "Otevřít složku skills",
    "Help & manuals": "Nápověda a manuály",
    "English manual (PDF)": "Anglický manuál (PDF)",
    "Czech manual (PDF)": "Český manuál (PDF)",
    "Opening manual: {path}": "Otevírám manuál: {path}",
    "Manual not found. Reinstall or repair the application.":
        "Manuál nebyl nalezen. Přeinstalujte nebo opravte aplikaci.",
    "Manual cannot be opened: {error}": "Manuál nelze otevřít: {error}",
    "Research progress": "Průběh výzkumu",
    "Export all sources": "Exportovat všechny podklady",
    "Synthesis DOCX": "Syntéza DOCX",
    "Synthesis PDF": "Syntéza PDF",
    "⚙️ Settings": "⚙️ Nastavení",
    "Model": "Model",
    "KV cache precision": "Přesnost KV cache",
    "Autonomy": "Autonomie",
    "Thinking": "Přemýšlení",
    "Language": "Jazyk",
    "🧠 MEMORY": "🧠 PAMĚŤ",
    "The model reads memory on every task and after compression; it stores facts on request "
    "(“remember…”).":
        "Model paměti čte při každé úloze a po kompresi; fakta ukládá na požádání "
        "(„zapamatuj si…“).",
    "Global memory — open": "Globální paměť — otevřít",
    "Mode memory — open": "Paměť režimu — otevřít",
    "Project memory — open": "Projektová paměť — otevřít",
    "📁 PROJECTS": "📁 PROJEKTY",
    "Project management": "Správa projektů",
    "New": "Nový",
    "Attach": "Připojit",
    "Delete project + folder": "Smazat projekt i složku",
    "new project name…": "název nového projektu…",
    "💬 PROJECT CHATS": "💬 CHATY PROJEKTU",
    "💬 CHATS WITHOUT PROJECT": "💬 CHATY BEZ PROJEKTU",
    "Search all chats": "Hledat ve všech chatech",
    "word or phrase…": "slovo nebo část věty…",
    "Search": "Hledat",
    "New chat": "Nový chat",
    "Delete": "Smazat",
    "⚠️ **Confirm deletion — click again within 6 s**": "⚠️ **Potvrď smazání — klikni znovu do 6 s**",
    "Rename and press Enter…": "Přejmenovat a potvrdit Enterem…",
    "Rename…": "Přejmenovat…",
    "Move chat to…": "Přesunout chat…",
    "No project": "žádný projekt",
    "Export / import": "Export / import",
    "Prepared export": "Připravený export",
    "Import JSONL": "Importovat JSONL",
    "Import as new chat": "Importovat jako nový chat",
    "Type a message…  (Enter / Ctrl+Enter = send, Shift+Enter = new line)":
        "Napiš zprávu…  (Enter / Ctrl+Enter = odeslat, Shift+Enter = nový řádek)",
    "Retry": "Znovu",
    "Undo": "Vrátit",
    "Fork": "Větev",
    "Send": "Odeslat",
    "⚠️ **Agent is waiting for action confirmation**": "⚠️ **Agent čeká na potvrzení akce**",
    "Allow": "Povolit",
    "Deny": "Zamítnout",
    "🛡️ FAILSAFE: mouse to the top-left corner aborts GUI actions · read-only commands run "
    "without confirmation · everything runs locally":
        "🛡️ FAILSAFE: myš do levého horního rohu přeruší GUI akce · "
        "čtecí příkazy bez potvrzení · vše lokálně",

    # ------------------------------------------------ systémové dialogy
    "Select a project folder (workspace)": "Vyber složku projektu (workspace)",
    "Select a text file to pin into the context": "Vyber textový soubor k připnutí do kontextu",
    "Text and source files": "Textové a zdrojové soubory",
    "All files": "Všechny soubory",

    # ------------------------------------------------ jazyk / reload
    "Language changed — reloading the interface…": "Jazyk změněn — načítám rozhraní znovu…",
    "Language saved — restart the app to apply it fully.":
        "Jazyk uložen — plně se projeví po restartu aplikace.",

    # ------------------------------------------------ GPU auto-fit
    "⚡ Auto-fit for a {vram} GB GPU": "⚡ Auto-fit pro GPU {vram} GB",
    "⚠ {model} needs ~{need} GB VRAM — the GPU has {vram} GB, "
    "it will likely run out of memory":
        "⚠ {model} potřebuje ~{need} GB VRAM — GPU má {vram} GB, "
        "pravděpodobně dojde paměť",
    "GPU (VRAM)": "GPU (VRAM)",
    "Auto (detect)": "Automaticky (detekce)",
    "⚡ GPU set to {vram} GB — switching to {model} ({profile})":
        "⚡ GPU nastaveno na {vram} GB — přepínám na {model} ({profile})",
    "✅ GPU setting saved ({vram})": "✅ Nastavení GPU uloženo ({vram})",

    # ------------------------------------------------ work modes
    "Discussion": "Diskuze",
    "Research": "Výzkum",
    "Writing": "Psaní",
    "Development": "Vývoj",
    "Computer": "Počítač",

    # ------------------------------------------------ session / historie
    "New branch": "Nová větev",
    "(fork)": "(větev)",

    # ------------------------------------------------ model switch
    "Failed to save UI state: {error}": "UI stav se nepodařilo uložit: {error}",

    # ------------------------------------------------ launcher
    "starting …": "startuji …",
    "starting the interface… (first run takes a while)": "startuji rozhraní… (první spuštění chvíli trvá)",
    "still starting… (details: runtime/launcher.log)": "stále startuje… (detaily: runtime/launcher.log)",
    "No free Web UI port in range {start}-{end}": "Žádný volný Web UI port v rozsahu {start}-{end}",
    "Missing {name} — run the installation manually as described in README.":
        "Chybí {name} - spusť instalaci ručně podle README.",
    "The app is not fully installed — missing:\n\n  • {items}\n\nRun the setup now? "
    "(downloads ~37 GB to the right place)":
        "Aplikace ještě není dokončená - chybí:\n\n  • {items}\n\nSpustit instalaci nyní? "
        "(stáhne ~37 GB na správné místo)",
    "Setup failed — try again, or run “Set up environment and models” from the Start Menu.":
        "Instalace se nepodařila - zkus znovu nebo spusť "
        "'Instalace prostředí a modelů' ze Start Menu.",
    "The app crashed – details in runtime\\launcher.log": "Aplikace selhala – detaily v runtime\\launcher.log",
    "Python environment (.venv)": "Python prostředí (.venv)",
    "Python dependencies (new requirements.txt version)": "Python závislosti (nová verze requirements.txt)",
    "Qwen3.8-27B models ({detail})": "modely Qwen3.8-27B ({detail})",
    "looking in: {path}": "hledám v: {path}",

    # ------------------------------------------------ qwen_app
    "Missing required components:\n  • {items}\n\nRun the environment setup:\n  "
    ".venv\\Scripts\\python scripts\\setup_env.py":
        "Chybí potřebné součásti:\n  • {items}\n\nSpusť instalaci prostředí:\n  "
        ".venv\\Scripts\\python scripts\\setup_env.py",
    "llama-server could not be started.\nDetails: runtime\\llama-server.log":
        "llama-server se nepodařilo spustit.\nDetaily: runtime\\llama-server.log",
    "The app crashed – details in runtime\\app.log": "Aplikace selhala – detaily v runtime\\app.log",

    # ------------------------------------------------ TUI
    "  •  local harness  •  RTX 5090": "  •  lokální harness  •  RTX 5090",
    "[bold]Commands:[/bold]\n"
    "  /memory                show persistent memory (global + mode + project)\n"
    "  /model q4|q5           switch model (server restart)\n"
    "  /work discussion|research|writing|development|computer   work mode\n"
    "  /mode chat|agent|computer     compatibility shortcut\n"
    "  /autonomy supervised|semi|auto   autonomy level\n"
    "  /thinking xhigh|medium|low|off   model reasoning depth\n"
    "  /img <path>           attach an image to the next message\n"
    "  /screenshot            attach a screen capture\n"
    "  /new                   new session   /sessions  list   /load <id>\n"
    "  /server status|start|stop    inference server management\n"
    "  /help                  this help  /exit  quit\n"
    "Input: Enter send  •  Ctrl+C interrupt generation":
        "[bold]Příkazy:[/bold]\n"
        "  /memory                zobraz trvalou paměť (globální + režim + projekt)\n"
        "  /model q4|q5           přepnutí modelu (restart serveru)\n"
        "  /work discussion|research|writing|development|computer   pracovní režim\n"
        "  /mode chat|agent|computer     kompatibilní zkratka\n"
        "  /autonomy supervised|semi|auto   úroveň autonomie\n"
        "  /thinking xhigh|medium|low|off   hloubka uvažování modelu\n"
        "  /img <cesta>           přiložit obrázek k další zprávě\n"
        "  /screenshot            přiložit screenshot obrazovky\n"
        "  /new                   nová session   /sessions  seznam   /load <id>\n"
        "  /server status|start|stop    správa inference serveru\n"
        "  /help                  tato nápověda  /exit  konec\n"
        "Vstup: Enter odeslat  •  Ctrl+C přerušit generování",
    "mode": "režim",
    "autonomy": "autonomie",
    "⚠ Action confirmation": "⚠ Potvrzení akce",
    "(approved automatically - 'a' in a previous confirmation)":
        "(schváleno automaticky - 'a' v předchozím potvrzení)",
    "  Allow?  [y/n/a]  (a = all until the end of the task)":
        "  Povolit?  [y/n/a]  (a = vše do konce úlohy)",
    "ERROR:": "CHYBA:",
    "Try /server start": "Zkus /server start",
    "⛔ Interrupted (Ctrl+C)": "⛔ Přerušeno (Ctrl+C)",
    "Unknown model '{key}'. Available: {models}": "Neznámý model '{key}'. Dostupné: {models}",
    "Switching to model '{key}' (llama-server restart)...":
        "Přepínám na model '{key}' (restart llama-server)...",
    "✓ Model {key} is running.": "✓ Model {key} běží.",
    "Switch failed - see runtime/llama-server.log": "Přepnutí selhalo - viz runtime/llama-server.log",
    "⚠ llama-server is not running.": "⚠ llama-server neběží.",
    "  Start now?": "  Spustit teď?",
    "Server failed to start - try /server start later.":
        "Server se nepodařilo spustit - zkus /server start později.",
    "Goodbye!": "Nashledanou!",
    "Usage: /mode chat|agent|computer": "Použití: /mode chat|agent|computer",
    "Usage: /work discussion|research|writing|development|computer":
        "Použití: /work discussion|research|writing|development|computer",
    "Usage: /autonomy supervised|semi|auto": "Použití: /autonomy supervised|semi|auto",
    "Thinking: {level} (options: xhigh | medium | low | off)":
        "Přemýšlení: {level} (volby: xhigh | medium | low | off)",
    "Usage: /thinking xhigh|medium|low|off": "Použití: /thinking xhigh|medium|low|off",
    "✓ Workspace set: {path}": "✓ Workspace nastaven: {path}",
    "Global memory:": "Globální paměť:",
    "Mode memory ({mode}):": "Paměť režimu {mode}:",
    "Project memory:": "Projektová paměť:",
    "— (set via /ws)": "— (nastav /ws)",
    "✓ Image attached: {name}": "✓ Obrázek přiložen: {name}",
    "File not found: {path}": "Soubor nenalezen: {path}",
    "✓ New session: {id}": "✓ Nová session: {id}",
    "{count} messages": "{count} zpráv",
    "✓ Loaded: {id} ({count} messages)": "✓ Načteno: {id} ({count} zpráv)",
    "Unknown command {command} - /help": "Neznámý příkaz {command} - /help",
    "Command error: {error}": "Chyba příkazu: {error}",
}
