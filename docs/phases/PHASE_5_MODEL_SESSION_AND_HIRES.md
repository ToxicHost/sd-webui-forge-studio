# Phase 5 — Model Session and Hires Lifecycle

## Purpose

Reduce unnecessary model transitions and make active model state truthful.

## Steps

1. Define canonical model signature.
2. Represent selected, active, and temporary phase signatures separately.
3. Add generation model-session lease.
4. Serialize transitions.
5. Add Hires restoration policy:
   - immediate;
   - after response;
   - when required.
6. Default conservatively, then benchmark.
7. Ensure ADetailer model selection is explicit:
   - base;
   - Hires;
   - custom.
8. Separate temporary swaps from persistent settings writes.
9. Add model-status UI.
10. Add finalizer for success/error/interrupt.
11. Test base → Hires → ADetailer → next request.
12. Benchmark A → B → A.
13. Review whether native Neo Hires path can replace custom path in more cases.

## Exit criteria

- no ambiguous active model;
- same-checkpoint Hires avoids reload;
- alternate Hires model transitions match policy;
- result is not delayed by unnecessary restoration where policy permits;
- next request always loads correct model;
- interruption safe;
- `0.4.0-alpha` candidate.
