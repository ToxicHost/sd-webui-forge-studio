# Phase 0 Status

Status date: 2026-07-23

Phase 0 remains open. Repository provenance and source inventory are recorded,
but runtime reproducibility and baseline behavior evidence have not yet been
captured.

## Completed checks

- confirmed the work branch is `docs/project-bootstrap`;
- confirmed the pre-edit working tree was clean;
- confirmed origin and upstream repository URLs;
- confirmed baseline commit, commit date, and tag;
- confirmed `neo...upstream/neo` left/right parity is `0 0`;
- confirmed no Git submodule manifest is present;
- imported the planning scaffold without nesting it under another directory;
- preserved the upstream Neo README while adding a project-foundation notice;
- reviewed the existing ignore rules and added only missing Phase 0 exclusions;
- inspected the supplied Studio source in place without copying implementation
  files into this repository;
- recorded initial dependency, integration, and license observations;
- confirmed no generation, model-loading, CUDA/VRAM, API, preview, Hires,
  ADetailer, or frontend behavior was changed.

## Confirmed source identities

### Forge Neo

- upstream: `https://github.com/Haoming02/sd-webui-forge-classic.git`
- branch: `neo`
- commit: `97ff3a4024be2f0d5316f16e868e5ef822768872`
- commit date: `2026-07-23T14:06:00+08:00`
- baseline tag: `neo-baseline-2026-07-23`
- license: AGPL-3.0

### Forge Studio reference

- version: `v4.10.0 — public beta`
- `version.json` commit:
  `b316a4d87abd69837cd723403a7a41e22f97482d`
- extracted directory: `Reference/Forge-Studio-main/Forge-Studio-main`
- license: AGPL-3.0
- implementation imported into distribution: no

## Remaining baseline evidence

- original Studio archive filename and cryptographic checksum;
- independently verifiable Studio repository mapping and commit date;
- fixed-seed request fixtures;
- expected image metadata and reference outputs;
- extension inventory and revisions;
- complete dependency and third-party license inventory;
- baseline timing and memory measurements.

## Missing runtime environment data

- operating system build;
- Python version and executable provenance;
- GPU model, VRAM, driver, CUDA, and PyTorch versions;
- attention backend and memory mode;
- CPU and system RAM;
- actual launch command, environment variables, and arguments;
- model families and extensions present on the baseline machine.

## Missing logs

- cold startup and first model load;
- warm base generation;
- same-checkpoint Hires;
- alternate-checkpoint Hires;
- first and second ADetailer runs;
- model A → B → A;
- preview enabled and disabled;
- interrupt during Hires;
- interrupt during ADetailer.

Logs must be sanitized before storage. They must not expose private paths,
credentials, private model names, prompts, or generated content.

## Unresolved questions

- What was the original Studio archive filename and checksum?
- Which public repository object and date correspond to the Studio
  `version.json` commit?
- What is the exact URL and license evidence for the recommended ADetailer
  Studio fork?
- Which model families and hardware configurations form the initial support
  matrix?
- Which bundled JavaScript and autocomplete datasets require additional
  attribution or redistribution review?

## Exact next task

Capture and review one sanitized runtime environment report for the baseline
machine. Do not run generation or begin integration as part of that task.
