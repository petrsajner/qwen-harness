---
name: computer-automation-craft
description: Reliable procedures for desktop GUI automation and computer control — screenshot inspection, coordinate translation, keyboard shortcuts over fragile clicks, and failsafe handling.
---

# Computer Automation Craft

Drive desktop applications and system workflows reliably without endless trial-and-error clicks.

## The Golden Rules of GUI Automation
1. **Work Mode Requirement**:
   - Computer automation tools (`screenshot`, `click`, `type_text`, `press_key`, `scroll`, `move_mouse`) are active exclusively in the **Computer** (`computer`) work mode.
2. **Prefer Keyboard Shortcuts Over Mouse Clicks**:
   - Keyboard shortcuts are resolution-independent, instant, and 100% reliable.
   - Launch apps with `press_key("win+r")` followed by `type_text("notepad\n")`.
   - Switch windows with `press_key("alt+tab")`.
   - Save files with `press_key("ctrl+s")`.
   - Close dialogs/apps with `press_key("esc")` or `press_key("alt+f4")`.
3. **The Screenshot Feedback Loop**:
   - **Step 1**: ALWAYS take a `screenshot()` before performing a mouse action.
   - **Step 2**: Inspect the returned image coordinates (X, Y in image pixels).
   - **Step 3**: Execute the action (e.g. `click(x=340, y=120)`).
   - **Step 4**: Take another `screenshot()` to verify the action succeeded (e.g. menu opened, button state changed).
4. **Resilient Typing**:
   - Use `type_text(text)`. Unicode text and Czech diacritics are automatically dispatched via clipboard paste (`Ctrl+V`) for reliability.
5. **Failsafe & Safety Awareness**:
   - PyAutoGUI failsafe is permanently armed: moving the mouse into the top-left corner of the primary display `(0, 0)` immediately aborts execution.
   - Never automate sensitive screens containing credentials, passwords, or financial information without explicit confirmation.
