# Forge Backend Adapter — Readiness Review

Review of the owned Studio boundary after the pre-adapter corrections, ahead of
implementing `ForgeBackendAdapter`.

`ForgeBackendAdapter` is still **not** implemented. This document classifies
what must change before it can be, what belongs inside it, and what can wait.

Classifications:

- **MUST FIX BEFORE ADAPTER** — the owned contract cannot express a real
  backend correctly until this changes.
- **IMPLEMENT IN ADAPTER** — the contract is adequate; the work is the
  adapter's own.
- **DEFER UNTIL AFTER FIRST REAL IMAGE** — real, but not on the critical path.

---

## 0. Status — all six MUST FIX items resolved

The six items were implemented on `feature/studio-real-adapter-contracts`.
Outcome states per the required vocabulary:

| Item | Outcome |
|---|---|
| 2.2 Progress contract | **FIXED AND TESTED** |
| 2.4 Model residency | **FIXED AND TESTED** |
| 2.5 Adapter-declared supported parameters | **FIXED AND TESTED** |
| 2.6 Model capability reporting | **OWNED CONTRACT FIXED — RUNTIME VERIFICATION DEFERRED** |
| 2.8 Browser-safe result delivery | **FIXED AND TESTED** |
| 2.13 Backend shutdown | **OWNED CONTRACT FIXED — RUNTIME VERIFICATION DEFERRED** |

**No remaining unfixed owned-contract blocker.** The two deferred items have
complete, tested owned contracts; what cannot be verified without launching
Forge is whether a real backend's *behaviour* matches what it declares — see
§0.1 and §0.2. Neither is documented-only: both have executable
implementations and regression tests.

Validation: 146 Studio tests OK (63 → 146), 179 preflight tests OK, 25/25
loopback checks OK (21 → 25), socket-free demo PASS, canonical frontend
manifests unchanged.

### 0.1 Why 2.6 is runtime-deferred

`get_capability` exists, is abstract on `BackendAdapter`, and `MockBackend`
reports its real behaviour: alignment 1, minimum 1, no maximum, no pixel
ceiling, no normalization. A test asserts the declaration matches observed
behaviour by generating at 521×777 — deliberately prime-ish and 64-hostile —
and confirming exact preservation.

What cannot be verified here is whether a *real* model's declared alignment,
safe axis limits, and pixel ceiling match what Forge actually does. That needs
a launched backend and a loaded checkpoint, both prohibited. The adapter's
responsibility is stated in §2.6.

### 0.2 Why 2.13 is runtime-deferred

`shutdown` exists, is abstract, and is idempotent, safe with a job in flight,
and wired through `StudioPresentation.shutdown` → `StudioApplication.shutdown`
→ `BackendAdapter.shutdown`, additionally clearing the result registry. Tested
for idempotency, residency release, in-flight safety, post-shutdown refusal,
and handle invalidation.

`MockBackend` holds no CUDA context, no model weights, and no VRAM, so "VRAM is
actually released" is unverifiable without a real backend. That is Acceptance
Matrix G12, and it stays gated.

---

## 1. The contract as it stands

`forge_studio/backend.py` — `BackendAdapter` (ABC), six abstract methods:

```text
get_backend_status()               -> BackendStatus
list_models()                      -> tuple[ModelSummary, ...]
submit_generation(request)         -> GenerationJobIdentity
poll_or_stream_progress(job_id)    -> ProgressEvent
cancel_generation(job_id)          -> CancellationResult
get_result(job_id)                 -> GeneratedResult
```

Composition is clean: `StudioApplication(backend: BackendAdapter)` takes the
adapter by constructor injection, and the only concrete wiring to `MockBackend`
lives in `presentation.py`'s demo/serve entry points. Substituting a real
adapter requires no change to `StudioApplication`.

The docstring already states the two invariants that matter most: every public
method must be concurrency-safe, and progress reads must be non-consuming
observations.

---

## 2. Item-by-item review

### 2.1 Resolved seed contract — **IMPLEMENT IN ADAPTER**

`GeneratedResult.metadata["seed"]` is the sole source of a resolved seed.
`_resolved_seed()` rejects bools, non-ints, and negatives; infotext omits
`Seed:` when unresolved. Correct and sufficient.

The adapter must place Forge's *actual* resolved seed there — the value after
`-1` randomization — not echo the request. Note the top-level `seed` field in
the retained-API response still emits `-1` as a sentinel when unresolved
(Correction Branch Review O-2); the adapter must not treat that as a value.

### 2.2 Progress contract — **MUST FIX BEFORE ADAPTER**

`ProgressEvent` carries `progress: int` (percent 0-100), `sequence`, `state`,
`message`, `error`. Scale selection is now explicit, not magnitude-inferred.

The gap: **there is no step/total-steps field on the contract.**
`SourceFrontendAdapter._ActiveGeneration` tracks `steps` out-of-band, next to
the job, because the contract cannot carry it. For the mock that is harmless.
For a real backend it is not: integer percent cannot express "step 7 of 20"
without rounding, and Forge reports step counts natively. Sampler-internal
substeps and Hires passes make a single percent actively misleading.

Add `step: int | None` and `total_steps: int | None` to `ProgressEvent` before
the adapter, and let the frontend adapter read them from the contract rather
than from its own bookkeeping.

### 2.3 Non-consuming observation — **IMPLEMENT IN ADAPTER**

Verified correct at both layers: observers call
`poll(job_id, include_result=False)`, and `presentation.poll` attaches a result
only in the `completed` state, so pre-terminal polls never materialize an image
regardless of the flag.

The adapter must preserve this against Forge's shared progress state, which is
process-global rather than per-job. Two observers of the same job, and an
observer of job A while job B runs, must both be safe.

### 2.4 Model residency semantics — **MUST FIX BEFORE ADAPTER**

`BackendAdapter` has **no `load_model`, no `unload_model`, and no
`get_current_model`.** Those live only on `SourceFrontendAdapter`, which holds
`_selected_model_id` as frontend state.

That is wrong for a real backend. Loading a checkpoint costs seconds and VRAM,
can fail, and is a backend fact — not a frontend selection. Today a real
adapter would have no contract method through which to be told to load
anything, and generation would have to load implicitly, which A5 exists to
prevent.

Add explicit model residency to the contract before the adapter:

```text
load_model(model_id)   -> ModelResidency   (idempotent; errors structured)
unload_model()         -> ModelResidency
get_current_model()    -> ModelResidency   (pure read)
```

`BackendStatus.model_loaded` already exists but is a bare bool with no identity
— insufficient to answer "which model".

### 2.5 Ignored-setting notices — **MUST FIX BEFORE ADAPTER**

`_UNSUPPORTED_GENERATION_KEYS` is a hardcoded 12-key tuple in
`source_api_adapter.py`, filtered by truthiness. It correctly reports what the
*mock* ignores.

A real adapter supports a different, larger set — sampler and scheduler in
particular become supported. A static list in the frontend adapter will then
lie in the other direction: claiming Studio ignores a setting the backend
honours. The supported-parameter set must come *from the adapter*, not from a
constant beside the HTTP shim.

Also note the truthiness filter (Correction Branch Review O-3): a real backend
where `0` is meaningful needs per-key semantics.

### 2.6 Exact dimensions — **MUST FIX BEFORE ADAPTER**

Validation is now `_positive_integer` / `_positive_int` at both layers, with no
arbitrary maximum, no divisibility rule, and no silent rounding. The mock
preserves `520x776` exactly. This matches the A7 audit conclusion.

The audit also concluded `FORGE_ADAPTER_CAPABILITY_REQUIRED = YES` — and
**there is no capability method on the contract.** Without one, either Studio
re-imposes a guessed universal rule (rejected by the owner) or it forwards
dimensions the selected model cannot honour and inherits the retained API's
silent latent-floor mismatch (VAE factor 8, Flux2 16).

Add capability reporting before the adapter:

```text
get_capability(model_id, operation) -> ModelCapability
```

reporting exact alignment, safe axis and pixel limits, and explicit
reject-versus-normalize behaviour. It must also let Studio report *requested
versus effective* dimensions when they differ, rather than silently
substituting.

### 2.7 Optional result transport — **IMPLEMENT IN ADAPTER**

`GeneratedResult.__init__` requires `image_data_url` or `output_path`, and
`to_dict()` drops `image_data_url` when `None`. Sound.

For real images, inline base64 data URLs are the wrong default — a 1024x1024
PNG is megabytes per poll. The adapter should return `output_path` and let a
contained delivery boundary serve bytes. That boundary does not exist yet
(§2.8).

### 2.8 Browser-safe result handling — **MUST FIX BEFORE ADAPTER**

`presentation.poll` strips `output_path` and `metadata_path`, and this is the
only browser-facing result path. Verified: no other emitter, and no
file-serving or streaming route exists.

That is exactly why it blocks. With inline data URLs impractical for real
images and internal paths correctly withheld, **a real adapter returning
`output_path` produces a result the browser cannot display at all.** The
contained delivery boundary must exist before or with the adapter: it must
translate an owned internal reference into bytes or an opaque same-origin
identifier, and must never accept a filesystem path from the browser or serve
outside the owned output root.

### 2.9 Model classification — **IMPLEMENT IN ADAPTER**

`ModelSummary.is_mock` defaults `False`; `MockBackend` sets `True` explicitly.
A real adapter inherits the correct default and needs no change.

One inconsistency to fix in passing: `SourceFrontendAdapter.model_status()` and
`load_model()` return hardcoded `"is_mock": True`, and `_canonical_result` sets
`"is_mock": True` in `settings`. These must derive from the backend.

### 2.10 Cancellation — **IMPLEMENT IN ADAPTER**

`CancellationResult` plus terminal-state handling is adequate, and the loopback
validator covers concurrent interruption, terminality, and recovery.

Real cancellation is harder: Forge interruption is cooperative and checked
between steps, so cancellation is not immediate and may land after completion.
The adapter must keep terminal states terminal, never resurrect a cancelled
job, and not leave the sampler in a state that corrupts the next request
(AGENTS.md: temporary model/LoRA/script state must not survive a request).

### 2.11 Structured errors — **IMPLEMENT IN ADAPTER**

`StructuredError(code, message, field)` and `StudioError` are adequate.
`ProgressEvent.error` carries failures.

The adapter must map real failures — OOM, missing checkpoint, corrupt
safetensors, CUDA fault — to stable codes without leaking filesystem paths,
stack traces, or model directory layout into `message`.

### 2.12 Thread safety — **IMPLEMENT IN ADAPTER**

The contract states the requirement and `MockBackend` honours it with a single
`Lock` plus a `_jobs` dict.

A real adapter cannot: Forge's model state, sampler, and progress reporting are
process-global mutable singletons. Concurrent `submit_generation` calls are not
safe. The adapter must serialize generation on its own gate — the retained
`_generation_gate` in `SourceFrontendAdapter` is the existing precedent — while
keeping status, progress, result, and cancel calls non-blocking. A slow
submission must not block an unrelated status read.

### 2.13 Shutdown — **MUST FIX BEFORE ADAPTER**

**`BackendAdapter` has no `shutdown()`.** The 21-check loopback validator's
"clean shutdown and port release" tests the HTTP *server*, not the backend.

`MockBackend` holds nothing, so nothing is missing today. A real adapter holds
a loaded checkpoint, CUDA context, and VRAM. Without contract-level teardown
there is no defined point to free them, no way to guarantee an in-flight
generation is interrupted before exit, and Acceptance Matrix gates G12 and G13
(clean shutdown, repeat launch) cannot be satisfied.

Add `shutdown()` — idempotent, safe to call with a job in flight, and required
to leave no CUDA allocation or orphaned thread.

### 2.14 Output ownership — **IMPLEMENT IN ADAPTER**

`MockBackend._persist_result` writes `{job_id}.svg` and `{job_id}.json` under
an injected `result_directory`, returning paths joined to
`result_public_root` (default `Evidence/studio-alpha-s0/results`).

Two things the real adapter must change. First, `result_public_root` is a
*string prefix* joined to filenames — it looks like a URL root but is not
served by anything; the real adapter must not inherit the confusion between an
ownership record and a servable route. Second, real outputs belong under the
application's own output root with owner-visible organization, not under
`Evidence/`.

### 2.15 Mock/real composition selection — **IMPLEMENT IN ADAPTER**

Constructor injection is already correct. What is missing is only the selection
point: `presentation.py`'s entry points hardcode `MockBackend(...)`.

Selection must be explicit and default to mock. A missing, failed, or
unauthorized real backend must **not** silently fall back to mock — the mock
produces plausible images and metadata, so a silent fallback would be
indistinguishable from a real generation. `BackendStatus.is_mock` and
`ModelSummary.is_mock` must always reflect what actually served the request.

---

## 3. Summary

### MUST FIX BEFORE ADAPTER (6) — all resolved

| # | Item | Change | Outcome |
|---|---|---|---|
| 2.2 | Progress contract | `step` / `total_steps` added to `ProgressEvent`; the bridge prefers contract values over percent-derived arithmetic | FIXED AND TESTED |
| 2.4 | Model residency | `ModelResidency` contract plus `load_model` / `unload_model` / `get_current_model`; the bridge's private `_selected_model_id` is deleted | FIXED AND TESTED |
| 2.5 | Ignored-setting notices | `supported_generation_parameters()` on `BackendAdapter`; the notice is `recognized − supported` | FIXED AND TESTED |
| 2.6 | Dimension capability | `ModelCapability` contract plus `get_capability(model_id, operation)` | OWNED CONTRACT FIXED — RUNTIME VERIFICATION DEFERRED |
| 2.8 | Browser-safe delivery | `result_delivery.py` registry, `/studio/file` route, opaque handles, sandboxed response CSP | FIXED AND TESTED |
| 2.13 | Shutdown | idempotent `shutdown()` on `BackendAdapter`, wired through presentation and application | OWNED CONTRACT FIXED — RUNTIME VERIFICATION DEFERRED |

`BackendAdapter` now declares 11 abstract methods, up from 6. A test asserts
the exact set, that an incomplete subclass cannot be instantiated, that
`MockBackend` satisfies all of them, and that every method added here carries a
contract docstring.

Item 2.8 was the one that blocked a *visible* first real image; it is now
implemented and exercised over real HTTP. See
[`STUDIO_RESULT_DELIVERY_CONTRACT.md`](STUDIO_RESULT_DELIVERY_CONTRACT.md).

### IMPLEMENT IN ADAPTER (9)

2.1 resolved seed · 2.3 non-consuming observation · 2.7 optional transport ·
2.9 model classification (plus the three hardcoded `is_mock: True` sites) ·
2.10 cancellation · 2.11 structured errors · 2.12 thread safety ·
2.14 output ownership · 2.15 composition selection

### DEFER UNTIL AFTER FIRST REAL IMAGE

- per-key semantics for ignored parameters (O-3);
- moving stale-selection invalidation off the `_models()` read path (O-1);
- retiring the top-level `seed: -1` sentinel (O-2);
- sampler and scheduler as first-class `GenerationRequest` fields — for the
  first image, record the backend's actual choice as provenance rather than
  accepting a requested value;
- `mime_type` negotiation beyond a single real format;
- progress streaming in place of polling;
- LoRA, img2img, Hires, ADetailer, ControlNet, Gallery, Workshop.

---

## 3.5 Mac-readiness boundaries — landed

Three platform boundaries identified by the static portability audit are now
implemented, tested, and integrated. Status:
`MAC ARCHITECTURE READY — RUNTIME UNVERIFIED`.

| ID | Boundary | State |
|---|---|---|
| B1 | Platform-aware Torch install selection | IMPLEMENTED AND TESTED |
| B2 | Device capability in `ModelCapability` | IMPLEMENTED AND TESTED |
| B3 | Explicit result-root case policy | IMPLEMENTED AND TESTED |

B2 directly extends this document's item 2.6. `ModelCapability` now carries
`device_type`, `dtype_policy`, and `attention_backend`, each defaulting to
`UNKNOWN`. A real adapter must translate Forge runtime facts into them:
`memory_management.get_torch_device()` for the device,
`should_use_fp16()`/`should_use_bf16()` for the dtype policy, and
`xformers_enabled()`/`sage_enabled()` for the attention backend. That
translation lives in the adapter -- no Torch import and no platform branch may
enter `forge_studio/`, and an AST test enforces it.

See `STUDIO_PLATFORM_CAPABILITY_CONTRACT.md`, `MAC_INSTALL_SELECTION_POLICY.md`,
and `MAC_RESULT_ROOT_CASE_POLICY.md`.

No macOS runtime claim is made. Nothing has run on a Mac.

---

## 3.10 First-image readiness — the adapter's generation half is specified

```text
Studio is ready for one separately authorized controlled test image.
No real generation occurred in this readiness phase.
```

`ForgeBackendAdapter` now has a concrete contract for every generation
responsibility listed in §3:

- **request translation** -- `FirstImageRequest`, frozen, with every default
  traced to Forge source;
- **progress mapping** -- `HeadlessProgress` reports real `step`/`total_steps`
  and refuses to invent a percentage, which is exactly the A1 discipline;
- **cancellation** -- cooperative, observed at a step boundary, and terminal
  states cannot regress;
- **result ownership** -- `GenerationOutcome` carries a root-relative location
  that `ResultRegistry` turns into an opaque handle;
- **structured failure** -- fourteen distinct validation codes plus
  `HEADLESS_GENERATION_NOT_AUTHORIZED`, none carrying a path or a traceback.

The adapter still must not construct Gradio UI or import from `modules/ui*`, and
nothing in this milestone does.

---

## 3.9 Phase 2B retry — a real model session exists

```text
The Anima triplet loaded. Gradio-free, on CUDA, inside 14 GiB, then unloaded.
```

`ForgeBackendAdapter` now has a proven construction path to build on:

- a Gradio-free options boundary supplies what the retained engine reads
  (one option, `emphasis`), so the adapter does not need Forge's UI-coupled
  option bootstrap;
- device facts are established -- RTX 5060 Ti, cc 12.0, bf16, 15.93 GiB -- so
  `get_capability` can stop answering `UNKNOWN` once a model is resident;
- residency is real: unet, clip, and vae all present on an `Anima` engine;
- unload returns allocated VRAM to zero, so `shutdown()` has a working shape to
  copy;
- one gap: the runtime model dtype is not exposed on the UNet patcher, so the
  adapter must derive it elsewhere rather than read it off the object.

---

## 3.8 Phase 2B — the device boundary is crossed, one blocker remains

```text
Phase 2B: the Anima-family triplet FAILED AFTER COMPATIBLE PREFLIGHT.
Not an incompatibility -- a missing option-initialization step.
```

CUDA initializes under an enforced 14 GiB ceiling, the retained loader reads all
three files, and the UNet/CLIP/VAE patchers construct. `Anima.__init__` then
fails because `modules.shared.opts` is `None`
(`backend/text_processing/anima_engine.py:24`).

For the adapter this means:

- `get_capability` can stop returning `UNKNOWN` as soon as a model is resident --
  the device facts (RTX 5060 Ti, cc 12.0, bf16, 15.93 GiB) are now established;
- residency, dtype, and VRAM reporting have real plumbing behind them;
- the adapter must run Forge's option initialization before constructing an
  engine, and must accept that doing so currently imports Gradio.

See `HEADLESS_MODEL_CATALOGUE_AND_LOAD.md` §9.

---

## 3.7 Phase 2A — the adapter now exists

```text
Phase 2A validates model selection and loader handoff.
It does not open or load a checkpoint.
```

`HeadlessBackendAdapter` (in `forge_headless/studio_adapter.py`) implements the
full `BackendAdapter` contract. Catalogue and residency reads work; generation,
progress, cancellation, result, and capability refuse with stable codes;
`load_model` refuses **after** full validation, so a caller can tell an unknown
model from an unauthorized one.

Consequences for §4 onward:

- the model catalogue is no longer blocked — it never needed `modules.sd_models`,
  because enumeration is owned code over an explicitly configured root;
- `list_models()` returns real entries through the **existing** `ModelSummary`
  wire contract; no second model API was created and the mock's response shape is
  untouched;
- alignment and safe limits (the A7 finding) are still `UNKNOWN` and still
  require a resident model — `get_capability` says so rather than guessing;
- the remaining gate is device initialisation, named exactly:
  `modules/shared.py:7` -> `backend.memory_management`, for one value read by one
  caller.

See `HEADLESS_MODEL_CATALOGUE_AND_LOAD.md`.

---

## 3.6 Headless boundary — landed

Gradio excision Phase 1 added an owned headless integration layer,
`forge_headless/`, sitting between the Studio adapter boundary and the retained
`backend/` tree. `ForgeBackendAdapter` will be implemented **against that
facade**, not against Forge globals.

The load-bearing finding: `backend/` has **zero** module-level Gradio imports
across 91 modules. Contamination is confined to `modules/`, and four edges
carry it -- `modules.shared`, `modules.script_callbacks`,
`modules.infotext_utils`, and `modules.ui` (reached only for a five-line
rounding helper).

Consequences for the adapter:

- readiness, identity, capability, and shutdown are available **now**, Gradio-free;
- model catalogue, load, generation, and progress are blocked on the four
  `modules/` edges -- Excision Phase 2 and 3;
- result handoff is already unblocked: `ForgeResultDescriptor` ->
  `ResultRegistry` -> opaque handle, proven with a synthetic file under an
  active import blocker;
- device, dtype, and attention facts stay `UNKNOWN` until
  `backend.memory_management` can be imported, which probes device memory at
  module scope and therefore needs its own authorization.

See `HEADLESS_FORGE_ARCHITECTURE.md` and `GRADIO_EXCISION_PLAN.md`.

---

## 4. Blocking dependency

The six MUST FIX items were contract work needing no network, no package
mutation, and no launch, so they were completed while Runtime Unblock remains
blocked. That work is done.

What remains blocked is unchanged: `ForgeBackendAdapter` itself needs a
launched Forge process, and launch is gated behind Acceptance Matrix G8, which
is behind the Gradio/Pillow conflict and an owner receipt that does not exist.

`ForgeBackendAdapter` now has a contract it can actually implement. When
Runtime Unblock clears, the adapter's obligations are:

1. implement all 11 abstract `BackendAdapter` methods;
2. report real `step` / `total_steps` from `modules.shared.state`;
3. own model residency, and refuse `submit_generation` when nothing is resident
   — the owned contract requires it and `MockBackend` cannot demonstrate it
   without changing semantics 63 existing tests depend on;
4. declare `supported_generation_parameters()` honestly, adding `sampler` and
   `scheduler` only once genuinely honoured;
5. report per-model, per-operation capability that matches real behaviour, and
   disclose requested-versus-effective dimensions rather than rounding;
6. write results to its own owned output root so the existing registry delivers
   them, and set `image_data_url=None` for real images;
7. release the checkpoint, CUDA context, and owned threads in `shutdown()`.
