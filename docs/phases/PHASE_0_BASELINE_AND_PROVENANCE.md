# Phase 0 — Baseline and Provenance

## Purpose

Freeze what exists before any integration or optimization.

## Entry criteria

- supplied Studio and Neo source archives available;
- development machine can launch the current setup.

## Steps

1. Create the real Git repository/fork.
2. Add Forge Neo as `upstream`.
3. Checkout the exact Neo revision matching the working machine.
4. Record:
   - branch;
   - commit;
   - commit date;
   - dirty status;
   - submodule/repository revisions if applicable.
5. Inventory the current Studio source without importing implementation code
   during the documentation bootstrap.
6. Record Studio commit/version.
7. Capture launch scripts and environment.
8. Capture full console logs for the baseline sequence.
9. Create fixed-seed request fixtures.
10. Record expected output metadata and image references.
11. Inventory extensions and third-party dependencies.
12. Populate project docs.

## Baseline run sequence

- cold startup;
- first model load;
- warm base generation;
- same-checkpoint Hires;
- alternate-checkpoint Hires;
- first ADetailer run;
- second ADetailer run;
- model A → B → A;
- preview enabled/disabled;
- interrupt during Hires;
- interrupt during ADetailer.

## Deliverables

- `UPSTREAM_BASE`;
- populated `PROJECT_STATE.md`;
- environment report;
- baseline logs;
- baseline output fixtures;
- dependency/license inventory;
- Phase 0 exit report.

## Exit criteria

- exact source provenance known;
- current behavior reproducible;
- no uncommitted source ambiguity;
- performance comparisons have a baseline protocol;
- unknowns explicitly listed.
