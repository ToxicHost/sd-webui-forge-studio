# Performance Task Prompt

Implement only the named performance task.

Before coding:

1. State the hypothesis.
2. Identify the exact measured span.
3. Define correctness invariants.
4. Define feature flag/fallback.
5. Define same-session A/B sequence.
6. Identify memory and interruption risks.

After coding:

- run fixed-seed correctness comparison;
- run paired repetitions in one process;
- report all runs and median;
- include environment and trace IDs;
- report VRAM and cleanup behavior;
- update performance report;
- update patch inventory if core files changed;
- state whether the hypothesis was confirmed.

Do not combine unrelated optimizations.
