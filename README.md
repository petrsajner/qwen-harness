# Marvin — local AI harness

Your local AI companion — a brain the size of a planet that never leaves your machine.

A harness for local work with **Qwen3.8-27B** and **Ornith 1.5 35B-A3B** models on an
**RTX 5090 (32 GB)**. Model inference, projects, memories and chats stay local.
When you use web or research tools, the app makes ordinary internet requests to
the selected websites and search services.

The UI language is **English by default**; the installer offers a language choice
(English / Czech) and the app can be switched at runtime in Settings.

## User manuals

- [English user manual](output/pdf/Marvin-Manual-EN.pdf)
- [Český uživatelský manuál](output/pdf/Marvin-Manual-CS.pdf)

Both manuals are also installed with the application and can be opened from
**Settings > Help and manuals**.

## Permanent product boundaries

The following are **explicit non-goals and will not be added**. Marvin is a
personal, single-user Windows tool with a web-first interface and one local model:

- No language-server or LSP distribution layer. The built-in lightweight symbol
  index is the intended solution.
- No persistent interactive terminal as a primary workflow. Shell tools remain a backup.
- No parallel model agents, subagents, or multi-model orchestration.
- No one-million-token context profiles. Context stays within practical local GPU profiles.
- No general plugin host, MCP ecosystem, or broad integration framework.

These are product decisions, not postponed roadmap items.

## Features

- 💬 **General chat** and a **coding agent** (file read/write, shell, search)
- 📁 **Workspace (project folder)** — pick a directory in the web UI; Qwen reads and
  writes directly on disk, so you never upload source documents into the chat.
  Last used folder is remembered.
- 🖼️ **Image analysis** — native vision (mmproj), including screenshots
- 🖱️ **Computer control** — screenshot → clicking, typing, keys (pyautogui + mss)
- 🔀 **Switchable models** — Qwen Q4/Q5 and Ornith Abliterated Q5
- 🎚️ **KV cache precision** — Qwen F16 for accuracy, or Q8 for double the context;
  Ornith fixed at Q8
- 🧠 **Thinking on/off** — model reasoning mode (switchable at runtime)
- 🛡️ **Three autonomy levels** — supervised / semi / auto (switchable anytime);
  read-only commands (`ls`, `cat`, `grep`, `git log`…) need no confirmation even
  in supervised mode
- 🖥️ **Web-first workspace** with a terminal fallback (127.0.0.1 only)
  — compact grouped sidebar; web UI: Ctrl+Enter sends, errors render in the chat
- 💾 **Session history** — JSONL persistence, images stored on disk
- ↩️ **Restore point per task** — atomic patching, a change overview and one-click
  revert of all files
- ⏱️ **Long-running background operations** — streaming output, timeout, stdin and
  process-tree termination
- 🧭 **Automatic project & context overview** — repo snapshot, pinned files and live
  context usage
- 📋 **Visible persistent task plan** — goal, steps, validation results and diff-review
  state survive restart and context compression
- 🧩 **Hierarchical project guidance** — `AGENTS.md`, `QWEN.md` and `CLAUDE.md`
  are applied automatically from project root to the active file
- 🔎 **Fast repository search** — ripgrep-backed literal/regex search and file globs
- ✅ **Project validation profiles** — auto-detected test/lint/typecheck/build commands,
  optionally customized in `.qwen/project.yaml`
- 🌐 **Isolated browser session** — headless Edge with DOM refs, fill/click/keys,
  select/hover/scroll, upload/download, responsive viewports, console/network log
  and screenshots fed directly to Qwen vision
- 🧭 **Multi-language code navigation** — declarations, document symbols and fast
  whole-word references for Python, JS/TS, Rust, Go, C/C++, C#, Java and Kotlin
- 🌿 **Chat management** — retry, edit last prompt, undo round, forks, search and
  export/import
- 🧭 **Live steering** — a follow-up message redirects the in-progress answer once
  the current sentence finishes
- 📌 **Pinned files** — selected instructions or architecture stay in the context of
  that particular chat
- 🧰 **Optional skills** — the model sees a short catalog and loads a full SKILL.md
  only when needed
- 🎛️ **Separate work modes** — Discussion, Research, Writing, Development and
  Computer, each with its own prompt and tools
- 🌐 **UI language switching** — English base, Czech available (installer choice or
  the Settings dropdown; applies without losing the session)

## Interface

The 1.6 workspace keeps project and chat navigation on the left, a persistent
conversation/composer in the center, and Results, Progress and Context on the
right. Settings have their own categorized dialog. Work modes retain their order:
Discussion, Research, Writing, Development, Computer.

- **Attach**, drag/drop and clipboard paste accept multiple images and documents.
  Real thumbnails remain in both the draft and sent message.
- **Clarify now / After completion** routes messages to steering or a durable queue.
- Refreshing the browser reconnects to the same run; Stop works during reasoning,
  tool preparation and transport waits. Drafts and partial answers are preserved.
- Result previews, exports, named restore points and portable project archives are
  available from the UI. Accepted decisions retain links to their source chats.
- PDF page vision, DOCX edits that preserve structure, and spreadsheet/page ranges
  extend the existing document tools.

## Architecture

```text
Desktop WebView2 / browser
       |
Static React + TypeScript workspace (ui_dist)
       | HTTP actions + replayable server events
Local FastAPI / ApplicationService
       | one model worker; immutable run configuration
Agent + existing tools + JSONL sessions / SQLite indexes
       |
llama.cpp (one selected local model)
```

The model worker belongs to the application, not to a browser connection or the
selected chat. Identified requests avoid duplicate submissions. Message events,
queue state and partial text survive reconnects. Indexes update incrementally.
Context summaries use the work mode and process every input segment.

Python remains the backend and Windows/Python 3.12 package versions are locked in
`requirements-windows-py312.lock`. Node.js is needed only by source developers to
build the frontend; the installer ships the compiled assets. The previous Gradio
surface remains available for compatibility diagnostics through
`MARVIN_LEGACY_UI=1`.

## Installation (one time)

**Required prerequisite:** install 64-bit **Python 3.12** from
[python.org](https://www.python.org/downloads/release/python-31210/) and enable
**Add Python to PATH** in its installer. Marvin creates its own virtual
environment, but it does not bundle the Python interpreter itself.

```bash
py -3.12 -m venv .venv
.venv/Scripts/python scripts/setup_env.py --model all
npm --prefix frontend ci
npm --prefix frontend run build
```

Downloads and prepares everything: pip dependencies, llama.cpp CUDA binaries
(~540 MB), Qwen Q4/Q5, Ornith Abliterated Q5 and both vision projectors (~59 GiB).

## Running

```bash
# 1) inference server (llama-server, ~10 s to load)
.venv/Scripts/python scripts/server.py start          # default model (q5 + Q8 KV)
.venv/Scripts/python scripts/server.py start q5       # or q5 directly
.venv/Scripts/python scripts/server.py start ornith_q5 # Ornith reasoning

# 2a) terminal UI
.venv/Scripts/python tui.py

# 2b) web UI → http://127.0.0.1:7860
.venv/Scripts/python webapp.py
```

The server can also be controlled from the TUI (`/server start|stop|status`) and
from the web UI (buttons).

## Desktop app (Windows)

**`qwen_app.py`** — a native window with the full lifecycle:

```bash
.venv/Scripts/pythonw qwen_app.py     # no console (for shortcuts)
.venv/Scripts/python qwen_app.py      # with a diagnostic console
```

- **START**: automatically starts llama-server (if not running) + the web UI and
  opens a native window (WebView2). If the environment is missing it offers a repair.
- **END**: closing the window stops the web UI **and llama-server** and frees VRAM
  (verified: 28 GB → 3.4 GB). Fallback without pywebview: system browser.

### Installer (Setup.exe)

```bash
installer/build_installer.bat     # builds dist/Marvin-Setup-<version>.exe
```

- Installs into `%LOCALAPPDATA%\QwenHarness` (no admin rights), Start Menu +
  optional desktop icon, standard Windows uninstall
- **Language choice during setup** — English is the default; the wizard also offers
  Czech. The selected language is written to `runtime\ui-language.txt` and the app
  starts in it. You can switch later in the web UI (Settings → Appearance and language).
- **First launch** (via the `run_app.bat` shortcut) automatically: creates the venv,
  downloads dependencies, llama.cpp (~540 MB) and models (~59 GiB) — afterwards it
  just opens the app. Python 3.12 must already be installed; Setup.exe does not
  contain Python.
- **Offline backup** in Settings & help copies the already downloaded files from
  `runtime\models` and `runtime\llama` plus the installed packages from `.venv`,
  without downloading them again, and writes a SHA-256 manifest. On another
  computer, install Python 3.12 and run the Setup.exe stored inside the backup.
  The installer detects `manifest.json` beside itself and keeps it as a local
  fallback. You can also place
  the backup beside Setup.exe as `QwenHarness-Offline-Backup` or run **Set up from
  offline backup** from the Start Menu. Normal setup uses the standard internet
  sources first and restores only a component that cannot be obtained online.
  The explicit offline command restores the backup immediately.
- On updates, a changed `requirements.txt` is detected automatically and only the
  needed Python packages are installed; models and other runtime data are not
  re-downloaded.
- Note: uninstall leaves downloaded models and sessions
  (`%LOCALAPPDATA%\QwenHarness\runtime`, `\sessions`) — delete manually if you wish.

## TUI commands

```
/ws [path]              show/set the project folder (workspace)
/model q4|q5|ornith_q5  switch model (server restart)
/mode chat|agent|computer     mode: chat | coding tools | + PC control
/autonomy supervised|semi|auto   autonomy level
/thinking xhigh|medium|low|off   model reasoning depth
/img <path>             attach an image to the next message
/screenshot             attach a screen capture
/new /sessions /load <id>   session management
/server status|start|stop    llama-server management
```

## Work modes

| Work mode | Tools | Use for |
|---|---|---|
| `discussion` | memory, web, project documents | everyday chat, ideas and discussion without coding rules |
| `research` | web, documents, research ledger | research with all sources and a mandatory final synthesis |
| `writing` | documents, patch, checkpoint and rollback | scripts, articles, reports and text revisions |
| `development` | patch, Git, shell, tests, repo snapshot | coding agent |
| `computer` | development + screenshot and GUI tools | computer control |

Every conversation remembers its own work mode. A project can hold any number of
conversations in different modes and remembers its default mode for new chats.

### Three layers of memory

Every chat receives three memory documents relevant to it in its system prompt:

1. `memory/GLOBAL.md` — shared preferences and facts for all kinds of work and projects.
2. Memory of the active work mode — shared across projects of the same type. The
   original `memory/MEMORY.md` is kept as the Development mode memory; the others
   live in `memory/modes/`.
3. `<project>/QWEN_MEMORY.md` — facts and decisions valid only for that project.

The model stores new information via `save_memory` with an explicit scope:
`global`, `mode` or `project`. Switching chats automatically switches the mode and
project layers too.

### Research mode

- `web_search`, `web_fetch` and local documents are written to a persistent `research.json`.
- Sources are not filtered or ranked by origin or assumed trustworthiness.
- Contradictory, negative, uncertain and minority claims must stay visible in the synthesis.
- A coverage check verifies that every loaded source ID appears in the conclusion.
- The complete ledger can be exported from the sidebar.
- Before the first search, a persistent plan of sub-questions and search angles is created.
- Web HTML, text, PDF and DOCX are extracted and stored in the same ledger.
- A finished synthesis can be exported to DOCX or PDF.
- Short progress comments and the working draft before synthesis stay in chat history.
- Saving a finished result uses `export_document` directly and starts no new research.

### Writing

- The final text can be exported to Markdown, structured DOCX or PDF with Czech diacritics support.
- Exported documents are part of the task checkpoint and can be reverted with the same rollback.
- Document export is available in all work modes; without a project it is saved next to the session.

### Resume and performance

- An in-progress agent step and pending confirmation are stored in `task-state.json`.
- The operational goal, step states, validations and active project paths are stored in
  `task-plan.json` and shown live in **Task progress**.
- Background processes write to persistent logs and re-attach by PID after a restart.
- Independent read-only tool calls run with bounded parallelism; every set containing
  a write stays sequential.
- History search uses an SQLite FTS5 index instead of repeatedly scanning all JSONL.
- Generation has no application-level output-token limit; it ends naturally or at the
  model's physical context boundary.
- Stop bypasses the queue, terminates generation at the nearest sentence, cancels an
  active browser operation or synchronous command, and keeps finished answer text.
- Synchronous commands save complete stdout/stderr under the chat's `command-logs`;
  the model receives a useful head+tail preview instead of losing errors at the end.
- Messages and context changes share one queue, so a new prompt or a chat switch
  never overwrites a running task.

## Autonomy and safety

| Autonomy | Behavior |
|---|---|
| `supervised` | **every** WRITE action (file write, shell, click…) requires confirmation [y/n/a] |
| `semi` | confirmation only for the first WRITE action in a task, then unlimited |
| `auto` | no confirmation and no agent step limit |

- 🛑 **FAILSAFE always on**: push the mouse into the **top-left corner** of the screen
  → GUI actions abort immediately
- The model specifies click coordinates in the pixels of the image it saw — the
  harness automatically remaps them to the real resolution (even after screenshot downscaling)
- The model sees the whole screen — beware of sensitive data; on-screen text may
  contain prompt injection (the system prompt warns the model, but confirmation is
  the main protection)
- Always use `supervised` for payments, email and similar

## Benchmark (RTX 5090, thinking off)

| Model | Generation | VRAM | Context |
|---|---|---|---|
| Q4_K_M | **~82 tok/s** | depends on KV profile | F16 128k / Q8 256k |
| Q5_K_M | **~73 tok/s** | depends on KV profile | F16 96k / Q8 192k |

TTFT ~1–2 s. Custom benchmark: `.venv/Scripts/python scripts/bench.py [--model q5]`

## Tests

```bash
.venv/Scripts/python tests/test_core.py     # core unit tests (no GPU)
.venv/Scripts/python tests/e2e_smoke.py     # E2E: chat + tool calling + vision (GPU)
.venv/Scripts/python tests/e2e_model_switch.py      # E2E: Q4 → Q5 background switch
.venv/Scripts/python tests/e2e_coding_workflow.py   # E2E: patch → test → rollback
.venv/Scripts/python tests/e2e_research_workflow.py # E2E: contradictory sources → synthesis
.venv/Scripts/python tests/e2e_document_export.py   # E2E: research result → PDF without new searching
.venv/Scripts/python tests/e2e_browser_workflow.py  # E2E: Edge DOM + console + screenshot
.venv/Scripts/python tests/e2e_browser_agent.py     # GPU E2E: Qwen autonomously tests a page
```

## Coding workflow

- Dynamic project context is assembled only for the current request; stale repo maps,
  pins and helper catalogs do not accumulate in chat history.
- Project guidance is discovered hierarchically from `AGENTS.md`, `QWEN.md` and
  `CLAUDE.md` files relevant to the files being inspected or changed.
- `search_files` uses ripgrep when available and supports literal/regex search;
  `find_files` provides project globs.
- `find_symbol`, `document_symbols` and `find_references` provide structured
  multi-language code navigation without loading whole files into context.
- Web applications can be opened in a separate headless Edge session. The agent uses
  semantic element refs, DOM text, console/network diagnostics and vision screenshots;
  it never needs to control the user's normal browser window.
- The agent edits existing files via `apply_patch`; a persistent checkpoint is taken
  before the first change.
- The sidebar shows a human-friendly list of created/modified files and offers a
  one-click revert of the whole task.
- `start_project_check` automatically finds the project's main test command and runs
  it as a long operation.
- A project can define named checks in `.qwen/project.yaml`:

```yaml
checks:
  - id: tests
    label: Full test suite
    command: npm test
    shell: powershell
    kind: test
    timeout: 900
    primary: true
```
- Structured Git tools operate only on the current task's files unless paths are
  given explicitly.
- Technical diff, process output and a repo map are available to the agent; the main
  user interface stays chat-based.

## Configuration (`config.yaml`)

- `server.extra_args` — additional verified llama-server flags
- `agent.max_steps`, `semi_max_steps` (`0` = unlimited), `shell_timeout`
- `computer.screenshot_max_edge` — screenshot downscale (token savings)
- `models.*.ctx_size` — context size (watch VRAM)

## Structure

```
harness/          core: config, llm, agent, safety, session, prompts, servermgmt, i18n
harness/tools/    fs, shell, vision (view_image), computer (screenshot/click/…)
skills/           bundled optional SKILL.md procedures
user-skills/      your own persistent skills (the installer never overwrites them)
scripts/          setup_env, download_llama, download_models, server, bench
tests/            test_core (unit), e2e_smoke (GPU)
tui.py            terminal UI
webapp.py         web UI (Gradio 6)
runtime/          llama.cpp + GGUF models (gitignored)
sessions/         conversation history (gitignored)
```

## Notes

- The llama-server default port will change to 9931 in the future (currently 8080,
  see the notice in the log)
- An alternative to llama.cpp is SGLang/vLLM under WSL2 (faster batching, more
  complex setup) — for computer-use on Windows, native llama.cpp is the most stable path
- The MTP (multi-token prediction) module can be added as a draft model via
  `server.extra_args`
