# Backlog and Issue Map

## Epic E0 — Baseline and provenance

- FSF-001 Record exact Neo upstream commit and branch.
- FSF-002 Record exact Studio source commit and dirty status.
- FSF-003 Capture launch arguments and environment.
- FSF-004 Create baseline logs and fixed-seed fixtures.
- FSF-005 Create initial patch inventory.
- FSF-006 Confirm license/credits inventory.

## Epic E1 — Repository bootstrap

- FSF-101 Create origin fork and upstream remote.
- FSF-102 Establish `main`, `develop`, integration branch policy.
- FSF-103 Import Studio as built-in component.
- FSF-104 Add distribution version metadata.
- FSF-105 Add duplicate external-extension detection.
- FSF-106 Preserve stock UI route/launch mode.
- FSF-107 Add project documentation scaffold.

## Epic E2 — Parity and test harness

- FSF-201 Startup smoke test.
- FSF-202 txt2img fixture.
- FSF-203 img2img/inpaint fixtures.
- FSF-204 same/different checkpoint Hires fixtures.
- FSF-205 ADetailer fixture.
- FSF-206 interruption cleanup fixture.
- FSF-207 state-leak sequence.
- FSF-208 workflow/output-quality state migration test.

## Epic E3 — Timing and diagnostics

- FSF-301 Request trace IDs.
- FSF-302 Correct wall timer.
- FSF-303 Separate process-images spans.
- FSF-304 Mark preview work non-additive.
- FSF-305 Model transition spans.
- FSF-306 Diagnostics panel/report.
- FSF-307 Path/prompt scrubbing.
- FSF-308 External-wall regression assertion.

## Epic E4 — Safe performance wins

- FSF-401 ADetailer detector cache.
- FSF-402 Detector invalidation/eviction.
- FSF-403 VAE/checkpoint catalog cache.
- FSF-404 Coalesce duplicate model load requests.
- FSF-405 Preview demand gating.
- FSF-406 Preview one-in-flight/stale-drop.
- FSF-407 Full-latent Quality preview correction.
- FSF-408 Bounded CPU JPEG worker.
- FSF-409 Remove unnecessary status refresh work.

## Epic E5 — Model session and Hires

- FSF-501 Define model signatures.
- FSF-502 Active/selected/temporary state.
- FSF-503 Generation model-session lease.
- FSF-504 Hires restoration policies.
- FSF-505 Deferred restoration.
- FSF-506 Error/interrupt finalizer.
- FSF-507 UI active-model status.
- FSF-508 Same-checkpoint no-op test.
- FSF-509 A→B→A transition benchmark.
- FSF-510 Persistent versus temporary config writes.

## Epic E6 — Memory policy

- FSF-601 Instrument forced synchronization and cache-clears.
- FSF-602 Define Compatible/Balanced/Aggressive profiles.
- FSF-603 Pressure-aware cleanup experiment.
- FSF-604 OOM recovery escalation.
- FSF-605 Long-run memory soak.
- FSF-606 Optional prior-model CPU cache design.
- FSF-607 Cache budget and eviction.
- FSF-608 Model prefetch research.

## Epic E7 — Postprocessing

- FSF-701 Nested ADetailer processing context.
- FSF-702 Skip duplicate global cleanup in nested context.
- FSF-703 Per-slot structured LoRA stack.
- FSF-704 Prompt-suffix integration after inheritance/region resolution.
- FSF-705 Detector/result timing.
- FSF-706 Crop batching research.
- FSF-707 ControlNet lifecycle audit.
- FSF-708 Regional prompt state isolation.

## Epic E8 — Packaging and release

- FSF-801 Clean install.
- FSF-802 Existing-extension migration.
- FSF-803 Settings schema versioning.
- FSF-804 Update path.
- FSF-805 Rollback path.
- FSF-806 Checksums and source archive.
- FSF-807 Credits/source/license UI.
- FSF-808 Release diagnostics bundle.
- FSF-809 Public support matrix.
- FSF-810 Upstream sync rehearsal.

## Recommended first ten implementation issues

1. FSF-001
2. FSF-003
3. FSF-101
4. FSF-103
5. FSF-106
6. FSF-201
7. FSF-202
8. FSF-301
9. FSF-302
10. FSF-306
