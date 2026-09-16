# Headless Forge Architecture

```text
Gradio is a temporary legacy compatibility dependency.
It is not part of the target Studio architecture.
```

Phase 1 of the Gradio excision. **No model was loaded and no image was
generated.**

---

## 1. The path

```text
Forge Studio UI
    v
forge_studio/          Studio application and contracts
    v
forge_studio.backend   owned adapter boundary (BackendAdapter)
    v
forge_headless/        headless Forge runtime facade      <- new in Phase 1
    v
backend/               retained Forge/Neo inference backend
```

Gradio sits on none of it. The legacy path is untouched and remains available:

```text
Neo compatibility entry point -> legacy Gradio UI -> backend/
```

## 2. Where the boundary lives, and why

`forge_headless/` at the repository root.

**Not in `forge_studio/`** — that package is purity-locked. An AST test asserts
no module under it imports `torch`, `torchvision`, `gradio`, or `numpy`, and
none reads `sys.platform` or `platform.system`. That rule is what keeps the
Studio contracts portable and testable without a GPU. A layer allowed to import
Forge cannot live inside it, and weakening the rule to make room would trade a
verified guarantee for convenience.

**Not in `modules/` or `modules_forge/`** — those are the A1111-derived
application layers, and all 37 module-level Gradio imports in the repository
live there. Putting the headless boundary inside them would defeat its purpose.

## 3. Hard rules, enforced by test

Every module under `forge_headless/`:

- never imports `gradio` or `gradio_client`, directly or transitively;
- never imports `modules.ui`, `modules.ui_tempdir`, or `modules.gradio_extensions`;
- imports **no** Forge module at module scope, so importing the package is
  side-effect free;
- constructs no HTTP presentation;
- owns no Studio contracts;
- exposes no raw Forge globals.

## 4. The facade

```python
ForgeHeadlessRuntime.construct(repository_root=..., compatibility_mode=False)
    .probe_readiness()        -> HeadlessReadiness
    .get_runtime_identity()   -> HeadlessIdentity
    .get_runtime_capability() -> HeadlessCapability
    .shutdown()               -> None
```

Phase 1 implements those five. `list_models`, `load_model`,
`submit_generation`, `poll_generation`, and `cancel_generation` raise stable
codes — `HEADLESS_OPERATION_NOT_IMPLEMENTED` or `HEADLESS_BACKEND_NOT_READY` —
and never fake success.

## 5. Lifecycle

```text
UNINITIALIZED  INITIALIZING  READY_NO_MODEL  MODEL_LOADING  READY
BUSY  DEGRADED  FAILED  SHUTTING_DOWN  STOPPED
```

Phase 1 may reach at most `READY_NO_MODEL`, and does so only on positive
evidence: every declared safe module imported, no Gradio in `sys.modules`, no
phase-1 blocker. `PHASE1_REACHABLE_STATES` is asserted by test, so a future
change cannot quietly claim `READY`.

## 6. What readiness actually establishes

Two separate strengths of claim, deliberately not merged:

| Field | Meaning |
|---|---|
| `verified_modules` | actually imported in-process under an active blocker |
| `statically_verified_packages` | import graph parsed and found Gradio-free, **without** importing |

Phase 1 imports four retained Forge modules — `backend.args`,
`backend.shared`, `backend.misc.eps`, `backend.text_processing.parsing` — chosen
because each is free of Gradio, Torch, device probing, and third-party packages,
so the probe runs under `-I -S`.

`backend/` as a whole (91 modules) is in the *static* list. It cannot be in the
first: three of its modules probe device memory at import time, and importing
them would initialise a device.

## 7. Device facts are UNKNOWN, on purpose

`get_runtime_capability()` reports `UNKNOWN` for `device_type`,
`dtype_policy`, `attention_backend`, and `maximum_dimension`.

Establishing any of them requires `backend.memory_management`, which probes
device memory at module scope. Reporting a plausible guess would be worse than
reporting ignorance — the same reasoning behind `ModelCapability` defaulting to
`UNKNOWN` rather than `cpu`.

`cuda_initialized` follows the same rule: readable via
`torch.cuda.is_initialized()`, but only if Torch is imported, which this phase
does not do. So it reports `UNKNOWN`, not `false`.

## 8. Studio integration

```text
STUDIO_BACKEND        mock | forge-headless        default: mock
NEO_UI_COMPATIBILITY  enabled | disabled           default: disabled
```

Independent by design. Choosing `forge-headless` does not enable the legacy UI;
disabling the legacy UI does not change the backend. `backend_selection.py`
imports only `os` and `typing`, so no selector can itself pull in Torch, Gradio,
or a socket — asserted by test.

An unrecognised backend name falls back to `mock` and the status route reports
that the request was not honoured, so a typo is visible rather than silent.

`StudioApplication` takes an optional `headless_runtime` as an **opaque object**
and reads only its declared reporting surface. Never a Forge global.

## 9. Owned status route

```text
GET /studio/runtime_status
```

Reports selected backend, whether the selection was honoured, headless state,
legacy compatibility state, `gradio_imported`, `model_loaded`,
`generation_available`, and a `blocking_reason`.

Inherits Host validation through the existing owned route table with no bypass
and no CORS header. Pure read — repeated calls leave residency unchanged. Emits
no path and no traceback. The canonical frontend does not consume it and is
unchanged.

For the mock backend, `generation_available` is **true** — the mock genuinely
generates. Reporting false there would be a lie. The headless selection is what
reports generation unavailable, with the blocker named.

`gradio_imported` is `"UNKNOWN"` when no headless runtime is attached: Studio can
say it did not import Gradio itself, not that nothing in the process did.

## 10. What Phase 1 did not do

- deleted no Gradio file;
- modified nothing under `modules/`, `modules_forge/`, or `backend/`;
- loaded no model, generated no image, initialised no device;
- changed no package and used no network;
- left the legacy Neo shell working exactly as before.

The boundary is additive. Nothing was removed to make room for it.

---

## Phase 2A additions

```text
Phase 2A validates model selection and loader handoff.
It does not open or load a checkpoint.
```

Four modules joined the package:

| Module | Role |
|---|---|
| `catalogue.py` | explicit-root enumeration, fail-closed containment, read-only case probe |
| `loader_port.py` | `LoaderPort` protocol, frozen `LoadRequest`, `PolicyGatedLoader` |
| `studio_adapter.py` | `BackendAdapter` implementation projecting candidates onto `ModelSummary` |
| *(extended)* `import_graph.py` | `paths_to_forbidden()` enumerates **every** module-level chain, not the first |

`INTEGRATION_REVISION` is now `headless-forge/phase2a`.

### Why the adapter is here and not in `forge_studio`

`forge_studio` is purity-locked by AST assertion. An adapter that imports the
headless runtime cannot live inside it, so the dependency points
`forge_headless -> forge_studio` and never back. `StudioApplication` still holds
the runtime as an opaque `object` and reads only its declared reporting surface.

### Lifecycle

`CATALOGUE_READY_NO_MODEL` and the transient `MODEL_LOAD_VALIDATING` were added.
`PHASE2A_REACHABLE_STATES` still excludes `MODEL_LOADING`, `READY`, and `BUSY`,
and a test enforces it.

### The Phase 1 count that was wrong

Phase 1 reported four blocking edges from a walk that recorded the first path per
module. `modules.shared` actually had fifteen paths through nine importers, so
`paths_to_forbidden()` now enumerates all of them and the blocker inventory in
`facade.py` is asserted still-true by test rather than trusted.

### The device boundary, named exactly

`modules/shared.py:7` `from backend import memory_management`, used only for
`xformers_available` at line 30. `backend/memory_management.py` calls
`torch.device` (L47), `torch.xpu.device_count` (L114),
`get_total_memory(get_torch_device())` (L187), and
`get_torch_device_name(...)` (L397) at module scope. That is why the
Gradio-freedom proof for `modules.shared` is static, and why importing it is
Phase 2B work.

