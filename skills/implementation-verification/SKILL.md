---
name: implementation-verification
description: A proportionate final review aid for substantial code changes, bug fixes, releases, installers, and user-facing workflows that benefit from checking requirements and real behavior.
---

# Implementation Verification

Apply only checks that fit the task and available environment.

1. Re-read the user's request and list the observable acceptance criteria mentally.
2. Inspect the actual changed artifacts, not only the intended patch.
3. Run focused tests first; broaden checks when shared behavior or release packaging changed.
4. Exercise the real user-facing path when practical.
5. Check generated packages or installers when the requested result is a distributable artifact.
6. Fix confirmed issues and repeat the affected checks.
7. State clearly which validations ran and which could not run.

Do not invent extra requirements or redesign a result that deliberately follows the user's format.
