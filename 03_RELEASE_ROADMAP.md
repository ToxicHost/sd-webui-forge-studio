# Release Roadmap

## `0.1.0-alpha` — Integrated shell

Purpose: prove that the fork can continuously track Neo without immediately redesigning it.

Required:

- exact Neo base recorded in `UPSTREAM_BASE`;
- Studio imported as a built-in or first-party component;
- Studio opens at `/studio`;
- stock Neo UI remains available;
- launchers identify the distribution version and upstream base;
- duplicate external Studio extension is detected and blocked with instructions;
- no backend performance policy changes yet;
- startup and basic generation smoke tests pass.

## `0.2.0-alpha` — Truth and diagnostics

Required:

- request trace IDs;
- accurate `total_wall` using `time.perf_counter()`;
- separate phase spans;
- preview work explicitly labeled as overlapping/non-additive;
- diagnostics export with private-path scrubbing;
- fixed-seed base, Hires, img2img, inpaint, interrupt, and ADetailer smoke tests;
- same-process Studio-versus-stock comparison workflow.

## `0.3.0-alpha` — Safe latency improvements

Candidates:

- ADetailer detector cache;
- cached VAE/checkpoint metadata scans;
- coalesced duplicate load requests;
- preview demand gating and one-in-flight scheduling;
- compatible full-latent TAESD preview as default;
- cheap temporary preview encoding;
- no-op route/status refresh optimizations.

Every item must be independently feature-gated until validated.

## `0.4.0-alpha` — Model session and Hires

Required:

- explicit distinction between active, selected, and temporary phase checkpoint;
- Hires checkpoint restoration policy;
- guaranteed cleanup on interruption/error;
- no stale model identity in UI;
- model transition timing;
- tests covering base → Hires → ADetailer → next request;
- same-checkpoint Hires does not cause a real reload;
- different-checkpoint Hires does not restore more often than policy requires.

## `0.5.0-beta` — Install, update, migrate

Required:

- clean install instructions;
- versioned settings schema;
- import from existing Studio extension;
- source and release checksums;
- update notification;
- rollback to prior distribution version;
- models remain outside destructive installer ownership;
- Windows first-party launcher; Linux launch path documented;
- dependency and license inventory.

## `0.9.0-beta` — Public beta candidate

Required:

- at least two successful upstream merge rehearsals;
- supported hardware/model matrix published;
- known limitations published;
- crash and diagnostic collection instructions;
- no open blocker-class correctness defects;
- performance numbers reproduce on at least two machines;
- release candidate soak test.

## `1.0.0` — Stable

Required:

- one-click or clearly documented migration;
- stable update policy;
- stable API compatibility policy;
- tested rollback;
- extension compatibility statement;
- upstream base and patch inventory published;
- support/documentation process established;
- no experimental feature enabled by default unless proven across the support matrix.
