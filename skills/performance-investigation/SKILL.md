---
name: performance-investigation
description: A measurement-first workflow for slow inference, startup, UI rendering, file processing, memory pressure, throughput, latency, or suspected bottlenecks.
---

# Performance Investigation

- Define the user-visible metric: latency, throughput, memory, responsiveness, or startup time.
- Capture a repeatable baseline with the real workload and environment.
- Measure major phases separately before optimizing.
- Change one meaningful variable at a time and retain comparable results.
- Distinguish faster steady-state generation from prompt evaluation, model loading, and UI delay.
- Watch for quality, stability, memory, and maintainability regressions.
- Keep an optimization only when the measured benefit matters for the user's workflow.

Prefer evidence from the actual machine over generic benchmark expectations.
