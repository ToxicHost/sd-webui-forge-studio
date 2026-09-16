# Target Architecture

> **Reconciled 2026-08-11.** The architecture PRINCIPLE below held and is still
> current. The **migration stages** did not happen as written — Stage A was
> skipped entirely. What was actually built is recorded under "What was built
> instead", and the current shape is in
> `Reference/CHATGPT_PROJECT_PLANNER_HANDOFF.md`.

## Architecture principle

Keep Forge Neo as the inference foundation. Put Studio policy behind explicit services and adapters. Modify Neo core only at narrow integration points.

**This held.** `backend/`, `ldm_patched/` and `extensions-builtin/` are
untouched; the two Neo-owned patches are inventoried in `14_PATCH_INVENTORY.md`
and neither changes inference.

## What was built instead

Stage A below describes placing the Studio extension in
`extensions-builtin/forge-studio/` as a minimally-adapted first-party component.
**That directory does not exist and never did.**

What happened: the extension's **frontend** was adopted as the standalone shell
(2026-07-24), and a Studio-owned stack was written beneath it —
`forge_studio/` for HTTP, application, jobs and results, and `forge_headless/`
for the boundary onto retained Neo inference. The extension's **backend was not
ported**; its ~180 `/studio/*` routes remain in the reference archive, and the
adopted frontend calls many that have no service here.

The consequence is worth stating plainly because it is the project's most
persistent misreading: **the UI looks feature-complete because it is the
shipping extension's UI.** Source identity is not feature parity.

The retained Neo interface is a separate launch path, not a live compatibility
layer wrapped by Studio.

---

### Stage A — Embedded extension — **NOT IMPLEMENTED, retained for history**

For the first integrated release, place the current Studio code in a first-party/built-in location with minimal source changes.

```text
Forge Neo core
├── existing modules/
├── existing backend/
├── extensions-builtin/
│   └── forge-studio/        current Studio extension, minimally adapted
└── launch/bootstrap changes
```

This establishes a distributable product without coupling a broad refactor to integration.

### Stage B — Studio application layer

After parity tests exist, move backend responsibilities gradually:

```text
forge_studio/
├── api/
│   ├── routes.py
│   ├── schemas.py
│   └── errors.py
├── application/
│   ├── generation_coordinator.py
│   ├── workflow_service.py
│   └── diagnostics_service.py
├── services/
│   ├── model_session.py
│   ├── memory_policy.py
│   ├── preview_service.py
│   ├── detector_cache.py
│   ├── timing.py
│   └── file_catalog.py
├── adapters/
│   └── neo/
│       ├── processing.py
│       ├── models.py
│       ├── vae.py
│       ├── scripts.py
│       └── state.py
└── frontend/
```

Names may change. The boundary is the important part.

## Component responsibilities

### Generation coordinator

Owns one request's lifecycle:

- validate request;
- resolve prompts/wildcards;
- establish trace;
- select model session;
- execute base generation;
- execute Hires;
- execute ADetailer/postprocessors;
- encode/save result;
- guarantee cleanup;
- return diagnostics.

It must not directly own CUDA allocation policy.

### Model session manager

Owns:

- selected persistent checkpoint signature;
- currently active checkpoint signature;
- temporary phase checkpoint;
- restoration policy;
- no-op load detection;
- model transition locking;
- stale request cancellation;
- transition metrics.

It delegates actual loading/unloading to the Neo adapter.

### Memory policy

Owns policy, not inference:

- Compatible/Balanced/Aggressive profile;
- when cache cleanup is requested;
- when a previous model may remain cached;
- memory-pressure thresholds;
- user-initiated unload;
- OOM recovery escalation.

It must not call global synchronization casually. Every forced synchronization should have a reason code in diagnostics.

### Preview service

Owns:

- per-client demand state;
- hidden/disabled suppression;
- cadence;
- one-in-flight behavior;
- immutable latent snapshot consumption;
- decoder path selection;
- post-decode resize;
- encoding and broadcast;
- preview metrics.

Default quality path:

```text
full latent snapshot → full-resolution TAESD → GPU RGB resize → small CPU transfer
```

Reduced-latent decode remains experimental until validated by model family and phase.

### Detector cache

Owns:

- detector key: path, device, file modification identity, relevant precision;
- construction/loading;
- bounded cache;
- explicit invalidation;
- low-VRAM behavior;
- load/hit/miss metrics.

It does not cache detection results unless a later feature explicitly defines image-content identity.

### Timing service

Owns hierarchical spans:

```text
request
├── preflight
├── model_transition
├── base
│   ├── setup
│   ├── sample
│   └── decode
├── hires
├── adetailer
├── output_encode
└── response
```

Overlapping preview work is recorded independently and is never added to wall time.

## Core patch budget

Target: no more than approximately 8–12 Neo-owned files changed for Studio integration in the first stable release, excluding branding and packaging.

For each modified Neo file, `docs/14_PATCH_INVENTORY.md` records:

- reason;
- hook added;
- behavior changed;
- upstream conflict likelihood;
- test coverage;
- whether it could be upstreamed.

## State model

Never overload one global field to represent all of these:

- user-selected checkpoint;
- currently resident checkpoint;
- Hires checkpoint;
- ADetailer checkpoint;
- pending next-request checkpoint;
- configured checkpoint shown in settings.

Represent them explicitly.

## Concurrency rules

- One model transition at a time.
- A generation request holds a model-session lease.
- UI load requests may be coalesced before execution.
- A superseded pending load may be canceled; an active load is completed or safely unwound.
- Preview work never mutates the producer latent.
- CPU encoding queues are bounded.
- Cleanup is idempotent.
- Interrupt paths run the same model-session finalizer as normal completion.
