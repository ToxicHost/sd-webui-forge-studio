# First 30 Days Plan

This is an execution sequence, not a calendar promise.

## Days 1–3 — Freeze and record

- create the real Git fork;
- add upstream remote;
- identify exact Neo branch/commit;
- import current Studio snapshot;
- record environment and launch flags;
- capture startup and generation logs;
- create baseline fixed-seed fixtures;
- populate `UPSTREAM_BASE` and `PROJECT_STATE.md`.

Deliverable: Phase 0 exit report.

## Days 4–7 — Integrated shell

- move Studio into built-in/first-party location;
- preserve `/studio`;
- preserve stock UI;
- add distribution version;
- detect duplicate external Studio;
- add source/license/credits links;
- confirm startup from a clean clone.

Deliverable: `0.1.0-alpha` candidate.

## Days 8–12 — Tests and trace

- add trace IDs;
- instrument request phases;
- correct total wall timer;
- add diagnostics export;
- create core smoke scripts/checklists;
- add state-leak and interrupt tests.

Deliverable: `0.2.0-alpha` candidate.

## Days 13–18 — Safe wins

- detector cache;
- VAE/catalog cache;
- model-load request coalescing;
- preview Quality correction;
- preview demand/cadence/stale-drop validation.

Deliverable: measured before/after reports.

## Days 19–24 — Model-session skeleton

- model signature type;
- active/selected/temporary state;
- serialized transition API;
- Hires restoration policy behind flag;
- robust finalizer;
- UI model-status differentiation.

Deliverable: model-session integration test suite.

## Days 25–30 — Stabilize and hand off

- run mixed-workflow soak;
- update risk register;
- produce upstream diff/patch inventory;
- close documentation gaps;
- prepare Fable read-only audit prompt;
- tag a private alpha;
- choose the next one subsystem, not a broad rewrite.

Deliverable: clean agent handoff and private alpha report.
