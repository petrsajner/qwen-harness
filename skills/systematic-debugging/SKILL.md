---
name: systematic-debugging
description: A flexible workflow for diagnosing crashes, incorrect behavior, regressions, intermittent failures, and unclear error reports before changing code.
---

# Systematic Debugging

Use this as a diagnostic aid. Adapt the depth to the problem and follow the user's requested scope.

1. Reproduce or precisely characterize the observed failure.
2. Trace the real execution path from the visible symptom toward its inputs and state.
3. Separate confirmed evidence from hypotheses.
4. Prefer the smallest experiment that can distinguish competing explanations.
5. Fix the root cause when practical; avoid unrelated refactoring.
6. Re-run the original failing path and relevant nearby checks.
7. Report what was confirmed, what changed, and what remains uncertain.

If reproduction is expensive or unavailable, preserve uncertainty and instrument the next run rather
than presenting a guess as a diagnosis.
