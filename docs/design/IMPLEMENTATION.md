# Marvin 1.6 implementation

Approved scope: the full 2026-09-05 proposal, preserving all 1.5.3 features, ordered modes, Attach, drag/drop, clipboard, prompt/chat thumbnails. This is active implementation, not another proposal. Do not mark complete until shipped and verified.

## Baseline

- HEAD 0c7841f, version 1.5.3.
- Pre-existing edits in webapp.py (216 insertions / 53 deletions) belong to the user. Preserve them. QWEN_MEMORY.md was already untracked.
- UI reference: C:/Users/Petr/.codex/visualizations/2026/08/22/01a02980-14b9-7d22-8d58-9327b6d33ff7/marvin-workspace.html.
- No subagents; no LSP, plugin host, parallel model agents or impractical contexts.

## Implemented in 1.6.0

- [x] LLM transport cancellation including no incoming bytes, reasoning/tool arguments; usage collection and regression tests.
- [x] Stop between tools; replace five-call forced FINAL with advisory repetition guidance.
- [x] Stable message IDs, atomic metadata, truncated-tail recovery, reasoning token estimates.
- [x] Incremental history FTS; persistent project FTS and shared file discovery module.
- [x] Mode-aware full-input chunked summaries and original chat retrieval tools.
- [x] Document ranges for PDF/DOCX/XLSX/CSV with continuation and formula modes.
- [x] Validate all above, extend regression tests and fix old assertions that encode removed behavior.
- [x] Document vision pages and DOCX preservation, research checkpoints/citations.
- [x] Checkpoints with conflict-aware restoration and project transfer.
- [x] ApplicationService with durable events, immutable run identity/config, queue/steering/STOP/reconnect/drafts.
- [x] FastAPI local routes and functional React/TypeScript UI matching approved concept; no mock handlers in production.
- [x] UI actions, slash commands, settings, memory, skills, processes, browser, exports, backups and language connected to existing services.
- [x] Entry point and installer integration, dependency lock, new unique release number and manuals.
- [x] Core and application integration tests, browser workflow checks, desktop/scale screenshots, attachment payload checks and installer build.

## Release verification

- Full release pipeline: 366 core tests, 14 workspace integration tests, TypeScript/Vite production build, both PDF manuals, PyInstaller launcher, Inno Setup installer.
- Edge browser checks: ordered work modes, Attach multi-image picker, image-only submit and thumbnails after reload, STOP retaining partial text, chat deletion and new selection; no console errors.
- Screenshot checks: 1440x960, 1024x768, 720x450 under output/playwright/release-1.6-*.
- Final manuals rendered and visually checked: English 24 pages, Czech 18 pages.
- Real Qwen Q5 end-to-end run passed: write_file/read_file task completed in 30.91 seconds including model load; image request correctly answered Green. Measured usage and saved context snapshot verified (runtime/workspace-model-e2e.json).
- Installer: dist/Marvin-Setup-1.6.0.exe, 15,620,510 bytes. SHA256 CC45C05E76148DCE6FF7050AE1991323E6FC8AA3F17ACD2AA2BA608C4817DCE3.
- Last release fixes: generic fork attachments survive source deletion, resume uses distinct live step IDs, deletion avoids reloading a deleted chat, title follows loaded chat, process stop can rebind saved manager, checkpoint conflict override requires explicit confirmation.
- Installer compilation is verified; the installer has not been run over the owner's installed application. Do not describe that as a clean-machine installation test.

## Implementation choices

Keep existing Python agent/tools/model runtime. Add a local ApplicationService/API independent of Gradio. Existing webapp.py remains available as a compatibility surface for tests/legacy while its __main__ dispatches to the new server. Serve prebuilt frontend assets; end users need no Node. One worker owns all model calls, including compression, and each run keeps its own session/config. Browsing another chat never rebinds the running agent.

Do not commit/push until requested. Do not recreate or modify the large model backup in this task. Close temporary validation processes; start the finished application on a free local port for the user after verification.
