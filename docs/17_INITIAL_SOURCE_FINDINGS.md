# Initial Source Findings from Supplied Snapshots

These are source-review observations, not completed performance proof.

## Verified identities

- Forge Neo upstream is `https://github.com/Haoming02/sd-webui-forge-classic.git`.
- The baseline is branch `neo` at commit
  `97ff3a4024be2f0d5316f16e868e5ef822768872`, dated
  `2026-07-23T14:06:00+08:00`.
- The `neo` branch and `upstream/neo` were at `0 0` left/right parity.
- The baseline tag is `neo-baseline-2026-07-23`.
- Studio `README.md` identifies the snapshot as `v4.10.0 — public beta`.
- Studio `version.json` contains commit
  `b316a4d87abd69837cd723403a7a41e22f97482d`.
- Both source trees contain AGPL-3.0 license text.

The Studio snapshot has no `.git` directory and no archive file was supplied in
the current reference area. Its original archive name, archive checksum, commit
date, and independent mapping from `version.json` to a Git repository remain
unverified.

## Existing Studio structure

The extracted snapshot contains 101 files:

- 63 files under `frontend/`, including the canvas, workflow, Gallery,
  Workshop, Lexicon, settings, documentation, localization, and brand assets;
- 24 Python files under `scripts/`;
- supporting `blueprints/`, `css/`, `javascript/`, `docs/`, `watermarks/`, and
  font/data assets;
- `install.py`, two Windows launchers, `README.md`, `version.json`, and license
  files.

The primary backend integration is split across
`scripts/studio_generation.py`, `scripts/studio_api.py`, and
`scripts/studio_bridge_init.py`. Dedicated modules cover ADetailer, ControlNet,
regional prompting, attention coupling, Gallery, Workshop, Lexicon, live
painting, Civitai metadata, dynamic prompts, tokens, watermarks, and custom
samplers/schedulers.

Studio registers a Forge callback through `scripts/studio.py`, attaches routes
to Forge's FastAPI application, serves its frontend at `/studio`, and redirects
`/` to `/studio` in `--nowebui` mode. The bridge initializes extension
ScriptRunner inputs for standalone mode without a Neo core edit.

## Timing observation

`studio_generation.py` contains generation-state timer handling and multiple
`process_images()` call paths. Existing Studio performance documentation says
the Forge timer is reset immediately before `process_images()` and requires
same-session A/B comparisons. This behavior still needs runtime verification
against the supplied snapshot.

## Hires and model-lifecycle observation

`studio_generation.py` defines `run_hires_fix()` and contains checkpoint
reload/restore fallbacks. This is a future candidate for an explicit
model-session policy, but no lifecycle changes belong in Phase 0.

## File-scan observation

`studio_api.py` and `studio_workshop.py` call `sd_vae.refresh_vae_list()` in
multiple paths. The project should distinguish explicit refresh from ordinary
generation/model-status requests and measure filesystem scan cost before
changing behavior.

## Neo model loading

The verified Neo baseline contains:

- `modules/sd_models.py::forge_model_reload`;
- `modules/devices.py::torch_gc`;
- `backend/memory_management.py::soft_empty_cache`;
- `backend/memory_management.py::unload_all_models`.

The exact call frequency and cost must be instrumented before changing cleanup
policy.

## Dependency and license observations

- `install.py` installs `imageio-ffmpeg` and `imagehash` for Gallery features.
- `studio_gallery.py` may attempt to install `watchdog` at runtime if missing;
  this behavior needs an explicit packaging and offline-install policy.
- Studio also imports dependencies already expected from Neo, including
  FastAPI, Pydantic, Pillow, NumPy, Gradio, PyTorch, safetensors, and psutil.
- Playfair Display font files include SIL Open Font License 1.1 text.
- The bundled `ag-psd.js` contains multiple third-party license notices.
- Gallery identifies itself as based on TrackImage v6.8 and integrated with
  permission, but the snapshot does not include a separate TrackImage license
  record.

## Current unknowns

- original Studio archive name and checksum;
- Studio commit date and independently verifiable repository mapping;
- actual user launch arguments and runtime environment on test machines;
- support matrix across model families;
- measured seconds for each model/Hires/ADetailer transition;
- whether the reported timer discrepancy is entirely timer scope or also
  overlap accounting;
- exact cause of the reported reduced-latent preview regression;
- complete third-party dependency and license inventory.

## Required baseline evidence

Capture complete sanitized evidence for:

1. runtime environment, startup, and first model load;
2. warm base generation;
3. same-checkpoint Hires;
4. alternate-checkpoint Hires;
5. ADetailer first and second run;
6. model A → B → A;
7. preview enabled and disabled;
8. interrupt during Hires and ADetailer;
9. fixed-seed requests, expected metadata, and reference outputs.
