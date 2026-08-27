# Qwen Harness Product Invariants

This repository builds a personal, single-user Windows application. The web UI is
the primary product surface. Shell access exists only as a backup capability for
the model. One local model occupies the GPU and performs the work sequentially.

## Permanent Non-Goals

The following features do not belong in this product and must not be proposed,
planned, or implemented unless the owner explicitly reverses this decision:

- Language servers or an LSP runtime/distribution layer.
- A persistent interactive terminal as a primary workflow.
- Parallel model agents, subagents, or multi-model orchestration.
- One-million-token context profiles or context expansion beyond practical GPU profiles.
- A general plugin host, MCP ecosystem, or broad third-party integration framework.

Use the built-in lightweight symbol index instead of LSP. Keep the current bounded
background process tools as the shell fallback. Improve the single-agent web
experience, reliability, context quality, and practical local tools rather than
expanding into these non-goals.
