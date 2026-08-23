---
name: architecture-options
description: Guidance for greenfield architecture, major feature boundaries, migrations, or refactors where several valid designs and tradeoffs should be considered.
---

# Architecture Options

Treat architecture as support for the user's goal, not as an end in itself.

- Start from the required user workflow and constraints.
- Inspect existing conventions before proposing a new abstraction.
- Identify ownership boundaries, persistent data, external dependencies, and failure recovery.
- Compare only materially different options; state costs and operational consequences plainly.
- Prefer the smallest design that supports likely change without speculative machinery.
- Preserve explicit format or implementation constraints from the user, including a requested
  single-file result when that is the task.
- When implementing, establish a thin end-to-end path first and verify it before broadening.

Architecture recommendations are suggestions. The user's explicit decision wins.
