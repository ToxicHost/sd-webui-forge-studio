# Phase 3 — Parity, Tests, and Truthful Metrics

## Purpose

Establish confidence before backend optimization.

## Steps

1. Add request trace IDs.
2. Add hierarchical phase spans.
3. Correct total wall timer.
4. Reset per-call Forge timer at actual `process_images()` boundary.
5. Separate preview work from additive wall phases.
6. Export diagnostics.
7. Implement smoke matrix.
8. Implement state-leak sequence.
9. Add external-wall comparison assertion.
10. Run Studio and stock UI controls in one process.
11. Document output comparison tolerances.
12. Establish release regression checklist.

## Exit criteria

- reported wall time agrees with external timing;
- core smoke matrix passes;
- trace identifies base/Hires/ADetailer/model transitions;
- next request succeeds after interrupts;
- baseline results reproducible;
- `0.2.0-alpha` candidate.
