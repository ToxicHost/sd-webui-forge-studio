# Upstream Sync Playbook

## Cadence

Recommended:

- check upstream weekly;
- merge routine updates every 2–4 weeks during active development;
- merge security or critical architecture fixes promptly;
- avoid syncing immediately before a release unless required.

## Preparation

Before syncing:

- finish or shelve active work;
- run the current smoke suite;
- save a baseline diagnostic report;
- record current sampler and transition timings;
- confirm the exact current `UPSTREAM_BASE`;
- create the integration branch.

## Conflict review checklist

For every conflict, answer:

1. Did upstream change an API or only nearby text?
2. Does upstream now implement a Studio patch?
3. Is Studio relying on an upstream bug?
4. Does the resolution preserve stock UI behavior?
5. Is a new adapter preferable to another core edit?
6. Does test coverage need expansion?
7. Should the patch inventory entry be removed or updated?

## Behavioral diff review

Search specifically for upstream changes to:

- `process_images`;
- Hires setup/cleanup;
- `forge_model_reload`;
- model loading parameters;
- VAE refresh and selection;
- memory management;
- CUDA streams;
- script hooks;
- progress/state;
- API startup and route registration;
- dependency versions.

## Validation tiers

### Tier 1 — Import/startup

- Python imports;
- route registration;
- stock UI startup;
- Studio startup;
- settings load.

### Tier 2 — Core generation

- txt2img;
- img2img;
- inpaint;
- Hires same checkpoint;
- Hires alternate checkpoint;
- interrupt.

### Tier 3 — Integrated features

- ADetailer;
- ControlNet;
- regional prompting;
- wildcard resolution;
- LoRA stack;
- canvas transfer;
- save/export.

### Tier 4 — Performance

- same-session stock vs Studio;
- preview open vs closed;
- first and second ADetailer run;
- base → Hires transition;
- model A → B → A;
- memory-pressure profile.

## Sync report

Use `docs/templates/UPSTREAM_SYNC_REPORT.md`.

The report must state:

- old and new upstream commits;
- conflict count and files;
- removed patches;
- new patches;
- behavior changes;
- test results;
- benchmark results;
- known follow-up.
