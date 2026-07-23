# Project State

## Current phase

Phase 0 — Baseline and Provenance (in progress).

## Source baseline

- Distribution repository: `https://github.com/ToxicHost/sd-webui-forge-studio`
- Working branch: `docs/project-bootstrap`
- Forge Neo upstream: `https://github.com/Haoming02/sd-webui-forge-classic.git`
- Forge Neo branch: `neo`
- Forge Neo commit: `97ff3a4024be2f0d5316f16e868e5ef822768872`
- Forge Neo commit date: `2026-07-23T14:06:00+08:00`
- Baseline tag: `neo-baseline-2026-07-23`
- `neo...upstream/neo` at Phase 0 inspection: `0 0`
- Forge Studio snapshot: `v4.10.0 — public beta`
- Studio `version.json` commit: `b316a4d87abd69837cd723403a7a41e22f97482d`
- License baseline: AGPL-3.0 text is present in both source trees

## Current status

- The working tree was clean before the Phase 0 scaffold import.
- The planning scaffold has been imported at the repository root.
- Neo provenance and upstream parity have been verified.
- The supplied Studio source has been inventoried but not copied into this repository.
- No distribution integration or product-code change has begun.
- Runtime environment evidence, launch logs, fixtures, and behavior baselines are still required.

## Known issues relevant to the fork

- console generation time reportedly overstates observed time by about six seconds;
- reduced-latent preview path produced severely garbled previews for one user;
- Studio-specific preview scheduling previously caused measurable overhead;
- model/Hires/ADetailer transition latency needs phase-level measurement;
- JPEG/WebP quality state must remain separately persisted;
- ADetailer per-slot LoRA behavior must preserve blank prompt inheritance.

## Phase 0 evidence still required

- sanitized runtime environment and actual launch arguments;
- cold startup and first-model-load log;
- warm base, same-checkpoint Hires, and alternate-checkpoint Hires logs;
- first and second ADetailer logs;
- model A → B → A log;
- preview enabled/disabled logs;
- Hires and ADetailer interrupt-recovery logs;
- fixed-seed request fixtures and expected output metadata;
- original Studio archive name/checksum and independently verifiable Studio commit date.

## Next single task

Capture and review a sanitized runtime environment report for the baseline machine.

## Unresolved decisions

- final public repository name and URL;
- exact default stock UI path/launch mode;
- initial supported hardware/model matrix;
- whether the standalone extension remains supported after beta;
- default Hires restoration policy after benchmarking.

## Last updated

2026-07-23 — provenance and source inventory bootstrap.
