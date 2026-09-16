# Studio Alpha S0 — Standalone Vertical Slice

## Goal

S0 proves that Studio can own and launch a complete interface workflow without
starting Forge Neo, constructing Gradio UI, loading a model, initializing
CUDA, resolving dependencies, or contacting an external network.

This is a mock interaction milestone. It is not an inference milestone.

> Superseded presentation: S0.5 replaced this milestone's temporary visual
> harness with the existing Forge Studio interface port. The application
> contracts, backend boundary, mock lifecycle, and independent launcher
> described here remain the approved foundation. See
> `docs/studio/STUDIO_ALPHA_S05_EXISTING_UI_PORT.md`.

## Branch and baseline

- Work branch: `feature/studio-alpha-vertical-slice`
- Starting baseline: `65bf780c77d9e06379a9d9d846ff85dfc5c41c8e`
- Neo remains the unchanged compatibility and rollback surface.

## Launch

From `app/`, start the interactive mock shell with:

```powershell
.\venv\Scripts\python.exe -I -S -B .\launch_studio.py
```

Studio binds only to `127.0.0.1:7865`, prints the local URL, does not open a
browser automatically, and stops cleanly with Ctrl+C. It does not use
`webui.bat`, `launch.py`, Forge, or Neo UI construction.

Run the socket-free validation demonstration with:

```powershell
.\venv\Scripts\python.exe -I -S -B .\launch_studio.py --demo
```

The demo performs success, in-progress cancellation, controlled failure, and
recovery success, then exits.

## Approach and Reference reuse

S0 uses a standard-library Python application and a vanilla HTML/CSS/JavaScript
frontend. It introduces no package, build step, CDN, external font, or copied
Neo frontend asset.

The bounded reconnaissance reviewed:

- `Reference/Forge-Studio-main/Forge-Studio-main/frontend/app.css`
- `Reference/Forge-Studio-main/Forge-Studio-main/frontend/index.html`
- the Reference brand and frontend structure; and
- the existing Studio service/adapter direction in
  `docs/02_TARGET_ARCHITECTURE.md`.

The new shell selectively reuses the Reference project's compact
"darkroom modern" visual direction and canvas/control-panel composition. It
does not reuse the Reference runtime routes, Neo imports, Gradio redirect,
DOM globals, external font request, installer, or update behavior.

## Architecture

```text
Studio presentation
  frontend/index.html + studio.css + studio.js
  presentation.py (static files and owned JSON routes)
                    |
                    v
Studio application
  contracts.py + application.py
                    |
                    v
Backend adapter boundary
  backend.py (interface) -> mock_backend.py (S0 implementation)
                            future Forge adapter (not implemented)
```

The presentation layer owns layout, form handling, progress, status, errors,
preview, and metadata display. It calls only `StudioApplication`.

The application layer owns request validation and coordination. Its contracts
cover backend status, model summaries, generation requests and identities,
progress events, cancellation, generated results, and structured errors.

`BackendAdapter` owns the replaceable backend boundary:
`get_backend_status`, `list_models`, `submit_generation`,
`poll_or_stream_progress`, `cancel_generation`, and `get_result`.
`MockBackend` is the only S0 implementation. No Studio module imports Neo,
Gradio, Torch, or a model runtime.

## Source layout

```text
forge_studio/
|-- __init__.py
|-- contracts.py
|-- backend.py
|-- application.py
|-- mock_backend.py
|-- presentation.py
`-- frontend/
    |-- index.html
    |-- studio.css
    `-- studio.js
launch_studio.py
tests/studio_alpha/
|-- run_tests.py
|-- test_import_boundaries.py
`-- test_studio_application.py
```

## Implemented interface

The Alpha S0 shell displays:

- backend status and mock designation;
- deterministic model selection;
- positive and negative prompts;
- seed, steps, CFG, width, and height;
- Generate and Cancel actions;
- queued, running, completed, cancelled, and failed states;
- percentage progress and current operation;
- a deterministic SVG preview;
- workspace-relative result and metadata paths; and
- a clear structured-error area that resets for the next request.

Entering `__mock_fail__` as the exact positive prompt triggers the controlled
failure scenario. The shell remains usable afterward.

## Mock behavior and output

Each process starts with two deterministic mock models and job IDs beginning
at `mock-job-0001`. Successful jobs advance through fixed progress events and
produce a programmatically generated SVG derived from the validated request.
No generative AI or external asset is used.

Cancellation after the running event is terminal and cannot later become
completed. Controlled failure returns `MOCK_CONTROLLED_FAILURE`. Subsequent
jobs work after completion, cancellation, or failure.

Interactive and demo completions write the SVG and deterministic metadata only
under:

```text
../Evidence/studio-alpha-s0/results/
```

The socket-free demo report is:

```text
../Evidence/studio-alpha-s0/demo-report.json
```

No screenshot was captured during automated validation because the approved
run did not open a browser or create a listener.

## Tests

Run:

```powershell
.\venv\Scripts\python.exe -I -S -B .\tests\studio_alpha\run_tests.py
```

The focused suite covers the 15 required product behaviors plus the
presentation adapter and strict JSON input boundary. It patches socket and
URL-opening APIs and fails if a test invokes them.

## Results

- Focused tests: 17 passed.
- Socket-free demo: PASS.
- Demonstrated states: queued, running, completed, cancelled, and failed.
- Demonstrated recovery: a later job completes after cancellation and failure.
- Result preview: deterministic programmatic SVG.
- Result metadata: deterministic and workspace-relative.
- Forge/Neo launch: not invoked.
- Gradio UI construction: not imported.
- Model loading and CUDA initialization: not invoked.
- External network and pip: not invoked.

## Performance evidence

Not applicable. S0 contains no inference or performance change.

## Current limitations and risks

- The backend is a mock and provides no real inference.
- The real Forge adapter is blocked until the preserved dependency conflict is
  resolved and Forge launches successfully.
- The interactive shell uses an IPv4 loopback listener; S0 implements no
  remote-listen or multi-user mode.
- Automated validation exercised the socket-free presentation adapter and demo,
  not browser rendering.
- The existing workspace venv is used as the launcher and is not yet a
  self-contained production runtime.
- Studio is not the repository default UI, and Neo remains unchanged.

## Decisions made

- Keep S0 dependency-free and use the existing workspace Python launcher.
- Keep presentation, application coordination, and backend behavior separate.
- Generate the placeholder locally instead of adding an external fixture.
- Do not implement the real Forge adapter during S0.

## Open questions

- Which dependency resolution will allow the preserved Forge baseline to
  launch?
- Which first real adapter operation should be validated after launch?
- What parity evidence is required before Studio can become the default UI?

## Uncommitted changes

None are intended at handoff; see `git status --short`.

## Next single task

After owner review, resolve the existing dependency blocker and prove a
successful Forge launch before implementing the real Studio backend adapter.

## Documents updated

- `docs/studio/STUDIO_ALPHA_S0_VERTICAL_SLICE.md`
- `PROJECT_STATE.md`
