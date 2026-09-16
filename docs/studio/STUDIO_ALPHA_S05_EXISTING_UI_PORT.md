# Studio Alpha S0.5 — Existing UI Port

## Superseded milestone

S0.5 is retained as historical evidence but was superseded by S0.6. The
independent Opus review confirmed that S0.5 was technically functional while
retaining only a reduced portion of the active product surface. It also found
an invented Create/Edit/img2img switcher and concurrency, Host-validation,
contrast, unavailable-control, prompt-counter, and submit-busy defects.

S0.6 preserves the application architecture, corrects the accepted defects,
and replaces this reduced shell with the fuller Canvas workspace documented in
`docs/studio/STUDIO_ALPHA_S06_PARITY_SHELL.md`.

## Goal

S0.5 replaces the temporary S0 presentation with a faithful, bounded port of
the existing Forge Studio interface while retaining the approved standalone
application contracts and deterministic mock backend.

This is a UI-port milestone. It does not add real inference.

## Canonical Reference source

- `Reference/Forge-Studio-main/Forge-Studio-main/frontend/index.html`
- `Reference/Forge-Studio-main/Forge-Studio-main/frontend/app.css`
- `Reference/Forge-Studio-main/Forge-Studio-main/frontend/app.js`
- `Reference/Forge-Studio-main/Forge-Studio-main/frontend/brand/forge-studio-mark.svg`

The canonical UI is framework-free static HTML, CSS, and classic JavaScript.
Its complete runtime cannot be copied directly because it assumes Forge/Neo
routes, a Studio WebSocket, canvas globals, and many optional modules.
The supplied snapshot identifies itself as v4.10.0 while its frontend
cache-buster uses 4.17.0; S0.5 treats the supplied workspace files as the
canonical design source without making an external release-version claim.

## Port strategy

The standalone port directly adapts the canonical full-viewport structure and
darkroom-modern design:

```text
Existing Studio presentation
        |
        v
StudioApplication and owned contracts
        |
        v
BackendAdapter
        |
        v
MockBackend
```

The title bar, brand, Canvas navigation, left toolstrip, canvas workspace,
right Generate panel, compact controls, output area, progress strip, and
status bar are recognizable Reference structures. The previous S0 two-column
card harness is no longer the default interface.

No application, backend, or generation contract changed.

## Source and destination map

| Reference source | Standalone destination | Owned event |
|---|---|---|
| Title bar and brand mark | `frontend/index.html`, `frontend/brand/` | Presentation only |
| Toolstrip and canvas workspace | `frontend/index.html`, `studio.css` | Result and progress projection |
| Generate panel | `frontend/index.html`, `studio.js` | Submit and cancel |
| Model selector | `#paramModel` | `list_models`, `GenerationRequest.model_id` |
| Prompt controls | `#paramPrompt`, `#paramNeg` | Generation request prompts |
| Steps, CFG, dimensions, seed | Canonical parameter IDs | Generation request fields |
| Progress and status | `#progressFill`, `#statusText` | Progress events |
| Output and metadata | `#outputImage`, `#outputMetadata` | Generated result |

The pre-implementation port map is
`Evidence/studio-alpha-s05/port-map.md`.

## Launch

From `app/`:

```powershell
.\venv\Scripts\python.exe -I -S -B .\launch_studio.py
```

Studio is served at `http://127.0.0.1:7865/studio/`. It remains independently
launchable, loopback-only, dependency-free, and separate from Neo.

Socket-free demonstration:

```powershell
.\venv\Scripts\python.exe -I -S -B .\launch_studio.py --demo
```

## Connected controls

- deterministic mock model;
- positive and negative prompts;
- seed, steps, CFG, width, and height;
- Generate and Cancel;
- queued, running, completed, cancelled, and failed states;
- ordered progress and textual status;
- generated preview;
- structured failure;
- result metadata and workspace-relative output paths; and
- repeated generation and recovery after cancellation or failure.

## Intentionally unavailable controls

The canonical Settings, Extensions, canvas tools and modes, Live, Skip, VAE,
sampler, scheduler, denoise, batch, workflows, Hires Fix, ADetailer, output
destinations, image loading, and panel-collapse controls remain visible but
disabled. Each unavailable control uses native `disabled`,
`aria-disabled="true"`, and an explanatory marker. No advanced behavior is
simulated.

## Visual fidelity and deviations

The port retains the canonical neutral surfaces, periwinkle accent, compact
30-pixel controls, panel hierarchy, responsive rearrangement, visible focus
states, status treatment, and reduced-motion behavior.

Known deviations:

- Google-hosted DM Sans and JetBrains Mono were replaced by system fallbacks to
  avoid an external request.
- The mock preview occupies the canvas workspace without the Reference canvas
  engine.
- Forge/Neo-dependent optional modules and their routes were not copied.
- Unsupported product controls are disabled rather than operational.
- No screenshot was captured because no workspace-local browser engine was
  available and adding one was outside scope. Static layout and runtime route
  evidence are not represented as pixel-rendering proof.

The detailed comparison is
`Evidence/studio-alpha-s05/visual-review.md`.

## Validation

Run the complete focused suite:

```powershell
.\venv\Scripts\python.exe -I -S -B .\tests\studio_alpha\run_tests.py
```

Result: 22 tests passed.

Validation demonstrated:

- the existing-UI port is the default surface;
- all required assets resolve locally;
- no CDN or remote asset is required;
- the eight request fields map to the owned request contract;
- success, ordered progress, cancellation, controlled failure, recovery, and
  repeated generation;
- preview and metadata projection;
- an actual ephemeral loopback server and clean shutdown; and
- no Forge, Neo UI, Gradio UI, CUDA, model load, real generation, package
  operation, or external network access.

Evidence:

- `Evidence/studio-alpha-s05/demo-report.json`
- `Evidence/studio-alpha-s05/loopback-validation.json`
- `Evidence/studio-alpha-s05/results/`
- `Evidence/studio-alpha-s05/loopback-results/`
- `Evidence/studio-alpha-s05/port-map.md`
- `Evidence/studio-alpha-s05/visual-review.md`

## Current limitations

- The backend remains deterministic and mock-only.
- Browser pixel rendering awaits an owner-approved workspace-local browser
  capability.
- The preserved Gradio/Pillow dependency conflict still blocks a verified real
  Forge launch.
- Studio is not yet the repository-wide default launcher.
- Neo remains unchanged as the compatibility and rollback surface.

## Performance evidence

Not applicable. S0.5 changes presentation only and performs no inference.

## Decisions made

- Adapt the canonical static UI directly instead of introducing a framework.
- Preserve the S0 application and backend contracts without extension.
- Keep unsupported canonical controls visible and disabled.
- Use only workspace-local assets and system font fallbacks.

## Open questions

- Which dependency correction will permit the first controlled Forge launch?
- Which browser-rendering capability can be approved for future screenshot and
  pixel-fidelity evidence?

## Uncommitted changes

None are intended at handoff; see `git status --short`.

## Next step

Use the S0.6 parity shell for human visual acceptance. After acceptance,
correct the preserved dependency conflict and prove a successful controlled
Forge launch before implementing the real Studio backend adapter.

## Documents updated

- `docs/studio/STUDIO_ALPHA_S05_EXISTING_UI_PORT.md`
- `docs/studio/STUDIO_ALPHA_S0_VERTICAL_SLICE.md`
- `PROJECT_STATE.md`
