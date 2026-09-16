# Project Charter

## Mission

Deliver a local-first, canvas-first AI image-generation application that preserves Forge Neo's inference compatibility while making Forge Studio the primary product experience and improving multi-phase workflow latency responsibly.

## In scope

- Fork and continuous upstream tracking of Forge Neo.
- Integration of existing Forge Studio frontend and backend routes.
- Stock Neo UI compatibility mode.
- Model, VAE, text-encoder, Hires, ADetailer, ControlNet, preview, timing, and diagnostics lifecycle improvements.
- Installation, migration, release, update, rollback, and support documentation.
- AGPL source and attribution compliance.
- Windows-first release; Linux support as a documented secondary target.

## Out of scope before `1.0`

- Replacing Forge's inference engine.
- Reimplementing all third-party extensions.
- Bundling checkpoint, LoRA, VAE, text-encoder, or detector weights.
- Hosted multi-user service.
- Cloud accounts or required telemetry.
- A node-graph workflow engine.
- Support promises for every architecture Neo can theoretically load.
- A new model-merging backend beyond the current Workshop scope.
- Optimizations that depend on undocumented CUDA races or global monkey patches.

## Success criteria

### Product

- Studio is the default experience.
- Existing users can retain model directories and Studio settings.
- The original Neo UI remains accessible for comparison and recovery.
- A failed optimization can be disabled without reinstalling.

### Correctness

- Fixed-seed generation remains behaviorally equivalent for supported workflows.
- Temporary models, LoRAs, scripts, and prompts do not leak between requests.
- Interrupt and error paths restore a valid application state.
- Timings describe actual wall time and clearly distinguish overlapping work.

### Performance

- Preview-off/hidden mode has no measurable Studio-specific sampler penalty beyond paired-run noise.
- ADetailer detector reuse eliminates repeated construction on warm runs.
- Hires transitions avoid redundant restoration/reload operations.
- Cold loads and warm swaps are measured separately.
- Claims include raw logs and environment details.

### Maintenance

- Every release records an upstream commit.
- Core changes are listed in a patch inventory.
- Upstream merges are rehearsed before public releases.
- Studio-owned logic is concentrated in Studio-owned modules.

## Constraints

- AGPL-3.0 distribution obligations.
- PyTorch/CUDA behavior varies by GPU, driver, model, dtype, and memory mode.
- Forge Neo global state and extension hooks can make nested processing fragile.
- The existing Studio code is large and feature-rich; migration must preserve behavior before refactoring.
- The Phase 0 Neo baseline is commit `97ff3a4024be2f0d5316f16e868e5ef822768872`
  on branch `neo`, tagged `neo-baseline-2026-07-23`.

## Decision authority

The project owner decides product scope, branding, release timing, default performance profiles, and support matrix.

A coding agent must request or clearly surface a decision when:

- output semantics would change;
- a default memory/model policy changes;
- an extension loses compatibility;
- a new network request or telemetry path is introduced;
- a migration could delete or overwrite user data;
- an optimization cannot be made reversible.
