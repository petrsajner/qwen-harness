---
name: code-refactoring-patterns
description: Systematic and safe code refactoring — behavior-preserving transformations, decomposition of long functions, type hints, defensive checks, and automated verification.
---

# Code Refactoring Patterns

Improve the structure, readability, and maintainability of code without altering its external behavior.

## The Iron Law of Refactoring
> "Refactoring without tests is just changing things and hoping for the best."

## Step-by-Step Procedure
1. **Safety Baseline**:
   - Run the existing test suite (`/test` or `run_process`) to confirm the project is in a green, working state.
   - Create a checkpoint (`/checkpoint pre-refactor`) so any mistake can be reverted instantly.
2. **Identify Code Smells**:
   - Functions longer than 50 lines doing multiple unrelated tasks.
   - Deeply nested `if/else` ladders (cyclomatic complexity > 8).
   - Duplicated logic across multiple files or branches.
   - Magic numbers and strings without named constants.
   - Missing or inaccurate type annotations.
3. **Incremental Micro-steps**:
   - **Extract Function / Helper**: Isolate a distinct sub-task into a private helper function with clear inputs and outputs.
   - **Guard Clauses**: Invert conditions and return early to eliminate deep nesting.
   - **Type Annotations**: Add explicit Python `typing` or TypeScript types to function signatures.
   - **Replace Magic Constants**: Move raw numbers/strings into module-level constants or Enums.
4. **Verification After Every Step**:
   - Validate syntax before saving (`validate_syntax_pre_write` catches syntax errors automatically).
   - Re-run the test suite after each change. If tests fail, fix immediately or use `/revert`.
5. **Review**:
   - Run `/review` to inspect the clean `git diff` and ensure no unintended side effects were introduced.
