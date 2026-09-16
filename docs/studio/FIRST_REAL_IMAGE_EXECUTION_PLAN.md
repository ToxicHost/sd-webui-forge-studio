# First Real Image — Execution Plan

Plan for producing the first real generated image through the owned Studio
boundary.

**Nothing in this plan is executed by the task that wrote it.** No Forge launch,
no model load, no CUDA initialization, no generation.

---

## 1. Scope

Exactly this, and nothing more:

```text
one owner-selected local checkpoint
one txt2img request
one image
one metadata record
one cancellation path
one repeat generation
```

### Explicitly deferred

```text
LoRA            img2img         Hires           ADetailer
ControlNet      Gallery         Workshop        packaging
updater         multi-model orchestration
```

Also deferred: batch size above 1, refiner, upscaling, inpainting, XYZ plot,
prompt scheduling, wildcards, and every extension not required to reach a
single image.

---

## 2. Preconditions

All must hold before step 1. Each is verifiable, and none may be assumed.

| # | Precondition | Status | Source of truth |
|---|---|---|---|
| P1 | The six MUST FIX contract items are landed, `MockBackend` updated, suites green | **MET** | `FORGE_BACKEND_ADAPTER_READINESS.md` §0 |
| P2 | Acceptance Matrix G0-G7 passed | **G0 only** | `RUNTIME_UNBLOCK_ACCEPTANCE_MATRIX.md` |
| P3 | Forge launch authorized by a distinct owner receipt with `allow_launch: true`, naming CUDA and model-load permission explicitly | **NOT MET** | Matrix G8 |
| P4 | Owner has named the exact checkpoint by path, and confirmed it is inside the workspace or explicitly authorized its exact location | **NOT MET** | owner message |
| P5 | Working tree clean, Neo parity `0 0`, canonical frontend byte-identical | **MET** | `git status`, preflight plan |
| P6 | Contained result-delivery boundary implemented and tested against mock | **MET** | `STUDIO_RESULT_DELIVERY_CONTRACT.md` |

**P6 is no longer the blocker.** The contained same-origin delivery boundary is
implemented, exercised over real HTTP, and covered by 33 unit tests plus four
loopback checks. Section 11 below describes the implemented mechanism rather
than a design to be built.

**P4 is still not satisfiable from inside the workspace.** No checkpoint has
been named, and model directories outside `Studio-Standalone\` must not be
discovered automatically. The owner must supply the exact path and authorize
the exact read.

**P3 is the remaining hard gate**, and it sits behind the unresolved
Gradio/Pillow conflict.

---

## 2.32 Internal Alpha Phase 1: the product surface, and the three gaps closed

The functional usable-alpha is accepted (§2.31). This milestone made it
owner-usable and closed the live run's three gaps together.

```text
telemetry     forge_headless/memory_report.py: ONE schema adapter (attributes
              in, wire names out, fail-closed on None/missing/negative/bool;
              the live regression pinned) + ONE classifier that can never
              accept unmeasured memory, and where ownership inconsistency
              beats clean numbers
argv          the exact original object restores -- identity AND values --
              on success and on both failure sides of the modules import
cancellation  JobCoordinator makes the lifecycle token THE public job id,
              minted before any lease or backend work; async 202 submits on
              lifecycle hosts; POST /api/jobs/{id}/cancel covers queued and
              backend jobs; queued cancels never touch adapter/session/port;
              seven race rows deterministic; old hosts byte-for-byte intact
frontend      studio-model-controls.js, Studio-authored, self-contained:
              profiles, load/unload, truthful state chips, job states with
              queued-cancel, opaque-handle results surviving unload; a
              one-line optionalScripts delta; s07 pins moved deliberately
launcher      launch_studio.py --config: bounded validated contract, plain-
              sentence refusals, loopback only, NO_MODEL start, autoload
              refused, contained results/logs, per-load explicit access via
              forge_headless/explicit_access.py
packaging     runbook + placeholder-guarded template + AGPL/asset notices +
              limitations + rollback under docs/studio/internal-alpha/
```

The guard story worth keeping: the first branch validation failed
`test_no_public_bypass_exists` because the launcher draft built
authorizations inside `forge_studio`. The capability moved to the headless
layer and the guard passes unmodified -- four milestones after it was
written, it caught its first real mistake.

### Not done

```text
one internal-alpha live UI smoke     the owner drives the Model panel against
                                     the real model; the fixed telemetry path
                                     captures the numeric memory thresholds
                                     the final live run lost
live A -> B switching                 deferred; one owner-approved profile
active-job cancellation in the panel deferred by design
installer / final branding           beta concerns
```

```text
Studio canonical runner   1409 OK, 3 skipped   (1358 + 51)
Studio discovery runner   1409 OK, 3 skipped
canonical preflight        179 OK
Neo parity                 0 0
```

No real model or CUDA work occurred. A fresh live authorization must bind to
the new integrated HEAD.

Evidence: `Evidence/studio-internal-alpha-frontend-packaging/`.

---

## 2.31 The final live test: functional usable-alpha ACCEPTED

One authorized run at the closure-sweep baseline (bde99c27) proved the whole
product lifecycle live:

```text
real Anima engine        12.0 s explicit load to READY through the real
                         loader defaults
two jobs                 12/12, first completed step 1, one warm session,
                         zero intermediate unloads
queued cancellation      job 3 queued behind BUSY work, cancelled with zero
                         port access, stable MODEL_JOB_CANCELLED on the wire
results                  byte-identical before and after unload -- and
                         byte-identical to their golden anchors: job 1 to
                         the anchor shared since the first controlled image,
                         job 2 to the three-cycle neon-alley render. The
                         product path reproduced the diagnostic path bit for
                         bit.
unload                   FakeInitialModel sentinel, registry, safe_open and
                         managed paths restored exactly; every owned weakref
                         dead; truthful NO_MODEL refusal after
guards                   twelve Gradio counters zero; server 1/1; 35.6 s of
                         the 900 s budget
```

Recorded position: **FUNCTIONAL_USABLE_ALPHA_ACCEPTED /
MEMORY_OBSERVABILITY_INCOMPLETE.** The numeric memory thresholds are not
claimed for that run: the harness read `.allocated_bytes` off objects whose
attributes are `.allocated`, and the numbers died with the process. The
14 GiB peak ceiling held by allocator enforcement. §2.32 made that mistake
structurally unrepeatable.

Evidence: `Evidence/studio-live-final-usable-alpha/`, including both PNGs.

---

## 2.30 The live-path closure sweep: every prerequisite audited at once

Two live attempts stopped before payload access -- neither authorization was
consumed, no live image was produced. The second failed inside the load on a
missing `sys.path` entry that no stub-based rehearsal could see. This sweep
stopped fixing that path one defect per attempt: one broad audit of the whole
call graph, every bounded defect fixed, and the stub blindness itself removed.

### The fixes

```text
managed package path   StudioStartupGlobals owns modules_forge/packages (and
                       the repository root): installed only during explicit
                       load, before the terminal imports, deduplicated,
                       removed exactly once on every exit
argv ownership         modules/shared_cmd_options.py parses argv STRICTLY at
                       import; the product now normalizes argv before any
                       modules.* import and restores the exact object at
                       close. Found by the inventory, beyond the mandate.
watch ordering         the safetensors patch is acquired before the terminal
                       import, the import sits inside the protected region,
                       and the loader unwind owns the restore -- an import
                       failure or a cancellation after the payload step can
                       no longer leave safe_open patched
```

### The stub blindness, closed

A probe subprocess (GPU hidden by construction, `--cpu` through Forge's own
argv channel) now runs in the suite:

```text
phase 0   without the managed path, import backend.loader dies on the FIRST
          vendored module -- gguf, one line before the huggingface_guess the
          live evidence inferred; same root cause, corrected record
stage A   REAL backend.loader / anima / huggingface_guess imports through the
          product startup, stopped by a sentinel at the first payload open --
          which fired exactly once and consumed the authorization object
stage B   actual product classes over the really-imported modules: real
          modules.processing import, real shared.sd_model property
          publication, real queue and transport, full three-job rehearsal
          with every required count met
```

What only the real modules could teach: Forge idles on `FakeInitialModel`
(a stub world cannot distinguish a correct unload restore from a leak);
`backend.memory_management` queries the torch device at import, which is why
the controlled CUDA initialization runs first under the authorized ceiling.

### Not done

```text
the real forge_loader body against real payloads     THE live test
the real sampler                                      THE live test
active CUDA cancellation                              deferred, unchanged
the one-time warm ~2.1 GiB allocation                 deferred, unchanged
```

Those two live items are now the ONLY unexecuted steps on the path.

```text
Studio canonical runner   1358 OK, 3 skipped   (1309 + 49)
Studio discovery runner   1358 OK, 3 skipped
canonical preflight        179 OK
frontend                   unchanged; no route added
Neo parity                 0 0
existing assertions        unchanged
```

No real model file was accessed, no CUDA tensor was allocated, and no image
was generated. A fresh live authorization must bind to the new integrated
HEAD.

Evidence: `Evidence/studio-live-path-closure-sweep/`.

---

## 2.29 The first live attempt stopped at the gate, and three seams are fixed

The live single-profile lifecycle test was authorized and **did not run**. Its
required non-live rehearsal failed, so the run stopped before payload access.

```text
authorization consumed   no
payload opens            0
CUDA                     0
images                   0        none expected, none produced
repository changed       no
```

That is the rehearsal working. Without it the live run would have opened all
three authorized payloads, initialized CUDA, built the engine, reached READY,
and then failed on the first generation -- spending a single-use authorization
to discover a wiring mistake.

### The three defects, and what they have in common

```text
A  the lifecycle published LoadedStudioSession; the adapter needs the inner
   HeadlessGenerationSession, and calls resident_model on it
B  a job submitted while another ran was recorded as queued and RAN ANYWAY:
   queued_jobs 1, port calls 3, max concurrent 2
C  result_root defaulted to None and the port called Path(result_root), so the
   load failed at step 7 of 8, after every payload was open
```

Each component was correct and tested. The connections were not:

```text
A  both objects correct, the seam wrong
B  lease() correct and thoroughly tested -- its caller ignored the answer
C  optional result root reasonable, Path() reasonable, combination unexercised
```

Third consecutive milestone with that shape. §2.27 shipped a correct lifecycle
nothing depended on; §2.28 shipped a correct loader publishing into a seam
nothing exercised; this found a correct queue that governed nothing.

### The fixes

```text
A  publish wrapper.session, unwrapped by SURFACE not class. The lifecycle keeps
   the wrapper, because unload must release the engine, port, globals and
   bookkeeping -- none of which the inner session owns.

B  begin_job calls acquire, which waits on the condition release already
   notified. One active generation, FIFO promotion, no spin, no new threads.
   Promotion skips cancelled tokens; a queued cancellation reports the distinct
   MODEL_JOB_CANCELLED and never reaches the port. Shutdown stops promoting the
   moment it begins, then wakes every waiter after _closed is set.

C  RESULT_ROOT_NOT_CONFIGURED at a new step 0, before cancellation and payloads.
   A refusal rather than a resolved default: system temp is outside the product,
   an environment variable is a hidden fallback by definition, and a
   repository-relative directory writes where a load was not asked to write.
```

### The coverage that was missing

A 50-test seam suite driving the real composition, loader, adapter and queue
**together**, centred on one complete three-job rehearsal:

```text
3 submitted, 2 reaching the port, 2 completed, 1 cancelled while queued
max concurrent port calls 1, port observed order [1, 2]
2 publications, 2 per-job releases, 0 intermediate unloads
both results byte-identical before AND after unload
NO_MODEL generation refused, opening nothing
```

One long test rather than several short ones, deliberately: 1259 short tests
passed against all three defects.

```text
Studio canonical runner   1309 OK, 3 skipped   (1259 + 50)
Studio discovery runner   1309 OK, 3 skipped
canonical preflight        179 OK
frontend                   unchanged; no route added
Neo parity                 0 0
existing assertions        unchanged -- the defects were absences
```

### Not done

```text
one live single-profile usable-alpha lifecycle test        still required
a FRESH authorization bound to the new integrated HEAD      required first
live A -> B switching        deferred; one owner-approved profile exists
visible frontend controls    deferred; the transport contract is ready
active CUDA cancellation     untouched by this milestone
```

The previous authorization was never consumed, so it was not spent -- but it was
bound to a HEAD this milestone supersedes, and a baseline-bound authorization
cannot be carried across a code change.

No real model file was opened, no CUDA tensor was allocated, and no image was
generated.

Evidence: `Evidence/studio-usable-alpha-seam-fixes/` and
`Evidence/studio-live-single-profile-usable-alpha/`.

---

## 2.28 Usable alpha, Phase 2.5: the loader's defaults are bound

§2.27 wired the loader and left four steps refusing rather than guessing. All
four are now bound, so an explicit product load needs no test-only injection.

### What each one binds

```text
startup_globals   StudioStartupGlobals     headless_options + ForgeStateBridge
                                           + HeadlessCompatibilityContext
payload_opener    ControlledPayloadOpener  ControlledLoadAuthorization +
                                           validate_exact_path + initialize_cuda
                                           + PayloadWatch
engine_builder    build_forge_engine       backend.loader.forge_loader
port_factory      StudioLiveGenerationPort extracted from the smoke harness
```

Three needed no extraction. The primitives were already product-neutral modules
under `forge_headless/`; the diagnostics merely called them. What was missing
was wiring.

### The port needed reshaping, not lifting

The smoke harness's `LiveGenerationPort` sets `terminal = True` after one
generate. That is correct for a single authorized job and wrong for a warm
session, which exists to be reused. It also took a `LifecycleRecorder` and
reached `generation_residual` -- both prohibited in product code -- and it did
not perform the per-job release; the runner did, from outside.

The product equivalent survives repeated jobs, owns its own
`release_generation_references`, and carries no diagnostic dependency.
Ownership is now two-tiered by construction:

```text
per job      processing, processed, images, conditioning caches
             released in a finally, BEFORE the fields are dropped, because the
             seam needs the processing object to reach the caches
per session  engine, component aliases, bridge
             released only by release_engine(), which session close calls
```

### The ordering that could not be guessed

```python
import modules.processing        # FIRST
from modules import shared
shared.sd_model = engine
```

`sd_models.py:12` imports `processing`; `processing.py:32` imports back from
`sd_models`; the `shared.sd_model` setter reaches `sd_models` through
`shared_items.py:175`. The cycle resolves only when `processing` is imported
first. Two authorized live attempts were spent discovering this -- which is
precisely why §2.27's defaults refused instead of writing something plausible.

Publication shares one restore handle with the reload bookkeeping, so a failure
after it cannot leave a released engine as the retained code's current model.

### Bound defaults are not readiness

```text
load_configuration_required   true when no controlled access is configured
MODEL_LOAD_NOT_CONFIGURED     distinct from MODEL_LOAD_FAILED, on purpose
```

Nothing was attempted, so nothing failed; a caller that conflates the two
retries the one thing that can never succeed. Selection, status and capability
reads still work and still open nothing.

### Not done

```text
the defaults have never run against a real model            THE live gap
one live single-profile usable-alpha lifecycle test         ready
live A -> B switching        deferred; one owner-approved profile exists
visible frontend controls    deferred; the transport contract is ready
the one-time warm ~2.1 GiB allocation                       deferred
```

The remaining risk moved from "four unwritten steps" to "written steps whose
live behaviour has not been observed". The likeliest live failure is the
publication ordering, because a stubbed `modules` has no import cycle to
survive, so no non-live test can detect a regression in it.

```text
Studio canonical runner   1259 OK, 3 skipped   (1177 + 82)
Studio discovery runner   1259 OK, 3 skipped
canonical preflight        179 OK
frontend                   unchanged; no route added
Neo parity                 0 0
```

No real model file was opened, no CUDA tensor was allocated, and no image was
generated.

Evidence: `Evidence/studio-real-loader-default-bindings/`.

---

## 2.27 Usable alpha, Phase 2: the real session loader is wired

Phase 1 is integrated. It left one hole, named in its own "not done" list
below: production had no session loader, so `load()` would have failed
`MODEL_LOAD_FAILED`, and the request path did not depend on the lifecycle at
all. The lifecycle was correct and inert. This section closes both.

### What is now wired

`forge_headless/session_loader.py` is the single product-owned loader. Eight
ordered steps, each individually injectable:

```text
cancellation checked      before anything opens
startup globals           must predate the modules.processing import
payloads opened           checkpoint, text_encoder, vae -- fixed order
engine built              exactly one
identity attached         before the port or the session exist
bookkeeping installed
port built
session built
```

Release is reverse acquisition on any partial failure. Once a payload has been
opened, a second attempt is refused with `step="single_attempt"` -- a failed
load never silently reopens payloads.

### The request path now depends on the lifecycle

```text
generation, nothing loaded   -> MODEL_NOT_READY, and nothing opens
explicit load                -> LOADING -> real loader -> READY
generation while READY       -> lease the warm session -> BUSY -> release -> READY
explicit unload              -> close once -> NO_MODEL
shutdown while READY         -> close the session
```

`submit_generation` leases **before** it validates: with nothing loaded there is
nothing a request could be valid for, and the earlier ordering reported
`INVALID_GENERATION_REQUEST` instead of the truthful `MODEL_NOT_READY`. The
lease is released in a `finally`. The gate is scoped to compositions that own a
session, so every pre-existing host keeps its original path.

The lifecycle publishes its session into the backend adapter on load and clears
it on unload, failed switch and shutdown -- the application and the adapter can
never hold different sessions.

### What is deliberately not finished

```text
startup_globals   payload_opener   engine_builder   port_factory
```

These four have **no real default**. They refuse. Every one of the 58 tests
injects them, so this milestone proves the orchestration around a real load is
correct -- it does not prove a real model can be loaded, because opening one was
prohibited. Refusing was chosen over guessing: a plausible default wired without
a live run would look finished and fail on first contact, during the live test
rather than here.

> **CLOSED by §2.28.** All four are now bound. The choice to refuse was
> vindicated: the engine publication ordering is a mutual import cycle that a
> guess would almost certainly have got backwards.

### Not done

```text
one live single-profile usable-alpha lifecycle test                remains
live A -> B switching        deferred; one owner-approved profile exists
visible frontend controls    deferred; the transport contract is ready
the one-time warm ~2.1 GiB allocation                              deferred
```

```text
Studio canonical runner   1177 OK, 3 skipped   (1119 + 58)
Studio discovery runner   1177 OK, 3 skipped
canonical preflight        179 OK
frontend                   unchanged; no route added
Neo parity                 0 0
```

No real model file was opened, no CUDA tensor was allocated, and no image was
generated.

Evidence: `Evidence/studio-real-session-loader/`.

---

## 2.26 Usable alpha, Phase 1: model selection and warm-session lifecycle

The product path is proven and its cleanup is closed. This section records the
first usable-alpha slice: turning a proven diagnostic session into a
product-owned lifecycle.

### The finding

The **backend contract was already complete**. `BackendAdapter` has declared
`load_model`, `unload_model`, `get_current_model` and `shutdown` since the
adapter boundary was written. What was missing was everything above it -- no
profile, no state, no owner -- so "is a model loaded" was a boolean the backend
happened to know. This is therefore a layer, not a rewrite, and no existing
contract changed.

### The flow

```text
NO_MODEL -> select -> PROFILE_SELECTED -> load -> LOADING -> READY
         -> jobs lease and release; the session stays warm
         -> unload -> NO_MODEL    or    switch -> READY on the next profile
         -> shutdown (terminal)
```

Nine states, a declared transition table, and refusals by name. Ten impossible
transitions are asserted to raise. Eight threads racing to enter `LOADING`
produce exactly one acceptance and seven named refusals.

### What is guaranteed

```text
one warm session            a single slot; six racing loads produce one
repeated jobs reuse it      five jobs, zero closes, still READY
per-job cleanup != unload   separate by construction
one active job, FIFO queue  nothing is silently dropped
unload/switch while BUSY    deferred to the next boundary
cancelled queued job        withdrawn; the session stays READY
switch A->B                 A closes before B loads; overlap zero
failed switch               does NOT resurrect A
shutdown during work        cancel requested, bounded wait, closes anyway
autoload                    off by default
```

### Redaction by shape

A profile's public projection has **no field** for a path -- not a filter that
strips one. Settings report `result_root_configured`, never the root. Four
service response shapes are serialized in tests and searched for the reference.

### Construction stays free

A fresh `-I -S -B` subprocess that builds a standalone application, selects a
profile and shuts down reports torch 0, cuda 0, `backend.*` 0, `modules.*` 0,
gradio 0, socketserver 0. The lifecycle modules import only stdlib and
`forge_studio.contracts`.

### The rehearsal

One synthetic run through the canonical `/studio/*` path: A loaded once and
reused for two jobs, a queued third withdrawn, a switch to B with zero session
overlap, all three opaque results retrieved **after** the switch and the unload,
back to `NO_MODEL`, then shutdown. 19 of 19 checks; A 1/1, B 1/1, 3 completed,
1 cancelled, 3 publications, 3 post-switch retrievals, 0 overlap.

### A defect worth recording

Five composition tests errored under the **canonical runner only** and passed
under discovery: `test_import_boundaries` purges `forge_studio` from
`sys.modules`, so the deferred import inside `build()` produced a second
`ModelProfile` class object and `isinstance` failed. `_implements_backend_port`
exists in that same file for exactly this reason and says so. Both checks are now
by surface, with tests pinning it. Third occurrence of this shape in the project.

### Not done

```text
a real session loader wired to the headless backend -- the only component that
would open a payload; without it, load() in production fails MODEL_LOAD_FAILED
                                                   CLOSED by 2.27
catalogue browsing, broad model-family support, public packaging   deferred
the one-time warm ~2.1 GiB allocation                              deferred
one live usable-alpha lifecycle test                               remains
```

```text
Studio canonical runner   1119 OK, 3 skipped   (1013 + 106)
Studio discovery runner   1119 OK, 3 skipped
canonical preflight        179 OK
frontend                   unchanged; no route added
Neo parity                 0 0
```

Evidence: `Evidence/studio-usable-alpha-model-lifecycle/`.

---

## 2.25 The conditioning cache is confirmed; per-job release is integrated

The candidate named in 2.24 was measured live and is now cleared by the shipped
seam on every job.

### The three-cycle live diagnostic

One warm model session, one loopback server, three canonical jobs -- two
identical, one with a different prompt and seed.

```text
12/12 steps and decode                       every job
first completed step index                   1, every job
three valid 768x768 PNGs, opaque delivery    complete
six retrievals, all 200, byte-identical      complete
jobs 1 and 2 == the golden anchor            yes; job 3 differs
per-job generation weakrefs dead             4 of 4, three times
registry at pre-load count, no-model state   confirmed
twelve Gradio counters zero                  confirmed
post-cleanup baselines                       -4,096 and 0 bytes of movement
                                             against a 1,048,576 tolerance
```

### The measurement

```text
JOB   cached_c / cached_uc BEFORE   AFTER   FREED
1     3 / 3                         0 / 0   4,784,640
2     3 / 3                         0 / 0   4,784,640
3     3 / 3                         0 / 0   4,784,640

predicted from source alone   4,784,128
difference                          512
```

Identical on all three jobs, including the differing prompt, through the shipped
seam, with the caches never touched directly.

### The gap, and the fix

The probe reached `release_generation_references` because it owned the
`StableDiffusionProcessing` instance directly. The service path did not: that
object is created inside `LiveGenerationPort.generate`, three layers below
cleanup, and the runner cleared the port's **references** -- which drops the
instance. A dropped instance does not clear a class attribute.

`_cleanup` now reads the cache counts, calls the seam exactly once, reads them
again, and only then releases the port, closes the session and collects. The seam
runs first because the port release is what drops the object it needs.

```text
one call site                    asserted by AST
one caller of it                 asserted by source count
_cleanup invoked twice           success path, and the exception path guarded
                                 by `if not report.cleanup:`
caches cleared directly          never; the runner has no clear_prompt_cache
CUDA clears added                zero
reporting triggers an import     no; sys.modules is read, never imported
```

`forge_headless/failure_cleanup.py` is unchanged. The fix is a call to it.

### Ownership sampling

`OwnershipSampler` refuses to read owned weakrefs until every release is marked,
returning `OWNERSHIP_NOT_SAMPLEABLE` instead of a clean-looking result, and
`ownership_facts` overwrites any caller claim with the measurement. The live
run's surviving `engine` weakref is reproduced synthetically -- a bound local, or
a closure capturing one, is sufficient to cause it and dropping it is sufficient
to clear it -- but it is **not claimed** that this was that run's holder. That
process exited and was not re-run.

Negative control: a retained local yields `OWNERSHIP_STATE_INCONSISTENT` at
allocated 0 / reserved 0 with every other fact true.

### Deferred

```text
one-time warm generation allocation   2,105,710,592 bytes
stable across jobs 1-3, not cumulative, not the conditioning cache
not yet attributed, not an architecture blocker
```

For the usable-alpha reliability milestone: cold-unload behaviour and model
switching.

```text
Studio canonical runner   1013 OK, 3 skipped   (961 + 50 + 2)
Studio discovery runner   1013 OK, 3 skipped
canonical preflight        179 OK
frontend                   unchanged
Neo parity                 0 0
```

Evidence: `Evidence/studio-three-cycle-live-reliability/`,
`Evidence/studio-per-job-generation-release/`.

---

## 2.24 The residual is generation-only; telemetry and first-step reporting fixed

The tracked rerun proved the product path and failed acceptance on one number.
This section records what that number is, what it is not, and what was corrected
about how it was reported.

### Preserved: the path works

```text
real Studio route, adapter, session, gateway, tracked port    complete
12/12 sampler steps, decode, one valid 768x768 PNG            complete
byte-identical to the golden anchor                           yes
pre- and post-cleanup opaque retrieval, identical bytes       yes
all owned weakrefs dead, registry at pre-load count           yes
shared/model_data no-model, identity detached                 yes
twelve Gradio counters zero, server 1 start / 1 stop          yes
```

### Unresolved: generation-only CUDA retention

```text
this run     14,352,384 allocated   25,165,824 reserved
prior         9,568,256 allocated   23,068,672 reserved
delta         +4,784,128            +2,097,152
cause         unknown
bounded across repeated jobs   unproven
```

`memory_allocated()` reports live allocator-owned tensor storage, so something
holds those bytes. The accurate statement is that **none of the currently
tracked model, session or harness owners retained it** -- not that nothing does.

### Two reporting defects, corrected

**The classifier read the wrong keys.** It used `allocated`/`reserved`;
`VramSample.to_dict()` emits `allocated_bytes`/`reserved_bytes`, so it saw `-1`
and reached its verdict by fallthrough. `read_sample` now consumes the canonical
schema only, refuses the legacy spelling by name, and has no `-1` anywhere.
Malformed telemetry classifies as `TELEMETRY_SCHEMA_INVALID` and carries no byte
fields at all. A contract test builds every sample through the **real** producer,
so a rename fails a test instead of a live run.

**The first step read 11.** The tracked port sampled
`progress.snapshot().step` once, after the loop. Retained Forge writes a
zero-based index -- `state.sampling_step = d["i"]` at
`modules/sd_samplers_common.py:431` -- so the last of `0..11` is `11`, and
`launch_sampling` writes `0` before the loop, making a zero-based `0` ambiguous
between reset and first step. The next line, `state.preview_step = step + 1`, is
Neo's own one-based counter and is not ambiguous. The observer takes the first
`preview_step >= 1` as the first completed step and keeps the zero-based value
as a cross-check, so **we do no arithmetic**.

```text
first_completed_step_index   1        final_completed_steps   12
first_completed_step_total   12       final_total_steps       12
zero_based_first / last      0 / 11   consistent              true
```

Both defects are reproduced as regression tests beside their corrections.

### Six classifications, ownership first

```text
TELEMETRY_SCHEMA_INVALID        OWNERSHIP_STATE_INCONSISTENT
ABSOLUTE_ZERO                   KNOWN_GENERATION_RESIDUAL_MATCH
NEW_OR_GREATER_RESIDUAL         LOWER_BUT_NONZERO_RESIDUAL
```

A byte count is never classified while the owner graph is inconsistent. The
first live run is why: 264,719,360 bytes with a live engine weakref would, on
bytes alone, look like an ordinary large residual.

### The census and the candidate

`StageSnapshotRecorder` records S0-S15 with allocated, reserved and both maxima
plus the ownership counts, refuses to record a stage twice, names unavailable
stages instead of interpolating them, and computes the first stage at which
bytes rise above baseline. It is inert by default and imports no torch, no
`backend`, no `modules` and no gradio; no product module imports it.

The leading candidate is inventoried and **not claimed**:
`StableDiffusionProcessing.cached_c` / `cached_uc` are **class attributes**
(`modules/processing.py:196-197`), filled at `:485-487`, and `close()` skips
`clear_prompt_cache()` whenever `persistent_cond_cache` is set -- which it is by
default (`modules/shared_options.py:321`). Dropping instances, ports, sessions
and engines cannot reach a class attribute. The shipped
`release_generation_references` clears all four names; `first_image_probe` calls
it and the tracked runner **does not**. The two paths ended at different
residuals. That is a mechanism and a number, not a proof, and closing the gap now
would change what the reliability run measures.

### Not proved

No leak is claimed: growth across cycles has not been measured. A three-cycle
same-session reliability run is designed and waiting on fresh authorization.
This is a reliability blocker, not an application-architecture blocker.

```text
Studio canonical runner   961 OK, 3 skipped   (890 + 71)
Studio discovery runner   961 OK, 3 skipped
canonical preflight       179 OK
frontend                  unchanged
Neo parity                0 0
```

Evidence: `Evidence/studio-generation-residual-attribution/`.

---

## 2.23 The live service path generated an image; the harness failed acceptance

One authorized single-use live smoke sent one canonical `/studio/*` request over
a bounded loopback server and it reached the real backend and came back with a
real image.

```text
StudioPresentation -> StudioApplication -> HeadlessBackendAdapter
  -> StudioGenerationTranslator -> GenerationGateway -> Tier-0 port
  -> process_images_inner -> 12/12 sampler steps -> decode
  -> ResultRegistry -> one opaque handle -> 768x768 PNG
```

The PNG is **byte-identical to the golden anchor**. Twelve Gradio counters read
zero, no mock backend was constructed, and the result reached the client as an
opaque handle with no filesystem path.

### Acceptance still failed, and the owner was the harness

After cleanup, 264,719,360 bytes stayed allocated, one Forge registry entry
survived, and the engine, VAE component, VAE module and VAE patcher were all
still reachable. The cause was one field in a throwaway scratch port:

```text
self._engine = engine        # stored at construction, never cleared
```

**No shipped Studio component was an owner.** The session holds a
`ResidentModel` description, not the engine; the gateway drops its port with the
session; the adapter and application clear on shutdown. The defect was entirely
in diagnostic code that was deleted with the scratch directory.

Three further problems were harness-owned and equally real: cleanup called
application shutdown, which clears the `ResultRegistry`, so post-cleanup result
durability could not be measured at all; the lifecycle recorder watched a
placeholder progress object while the gateway drove a different one, so a run
that completed 12/12 steps reported `denoising_started: False, step: 0`; and
nine cache clears were disclosed as one number with no owner attribution.

### The harness is now tracked, and it releases what it owns

`scripts/headless/studio_service_smoke.py` replaces the scratch runner.

```text
release_engine()      clears _engine, _forge_objects, _vae_component,
                      _bridge, processing_request, processed; called on the
                      success path and again from the exception handler, so it
                      runs after success, generation failure, publication
                      failure, cancellation and server failure alike; a raising
                      release is reported, not masked
single-use            a second run refuses with GENERATION_PORT_TERMINAL
durability            publish -> retrieve -> release port and session ->
                      retrieve again -> compare -> THEN stop the application
                      and the server; ordering is load-bearing because
                      StudioApplication.shutdown clears the registry
lifecycle             LifecycleRecorder owns no progress object; facts come
                      from Studio job progress plus explicit port callbacks,
                      and a contradiction raises
cache clears          loader / retained generation / Studio terminal / total,
                      four counters that never collapse into one
```

Proven by 41 new tests against a synthetic eight-object engine graph, including
a negative control that keeps the old defect alive on purpose and shows the
allocator stays non-zero until the field is cleared by hand. The canonical
runner forbids loopback sockets and makes no exception for `127.0.0.1`, so every
server-touching scenario runs in a subprocess — the guard was respected, not
weakened.

```text
Studio canonical runner   890 OK, 3 skipped   (849 + 41)
Studio discovery runner   890 OK, 3 skipped
canonical preflight       179 OK
frontend                  unchanged
Neo parity                0 0
```

### What is not proved

**The live Studio service smoke has not passed.** It failed acceptance, and this
correction did not rerun it — no model file was opened, no CUDA was initialized,
and no image was generated. The tracked harness has never driven a live run: its
`LiveGenerationPort` is exercised only against a synthetic port, so the bridge
re-pointing and the real sampler writing into the gateway's progress remain
proven by the earlier scratch run alone.

**One rerun of the live smoke remains required, and needs a fresh
authorization.** Evidence: `Evidence/studio-live-frontend-backend-smoke/`,
`Evidence/studio-live-smoke-harness-cleanup/`.

---

## 2.22 The real headless backend is selectable

Phase 1 gave Studio a composition root and proved it with a fake backend. This
section wires the **real** `HeadlessBackendAdapter` behind the existing Studio
backend port, non-live.

### Backend selection

```text
build_standalone(backend_kind="mock" | "headless", ...)
```

One closed selector. Unknown kinds fail with `BACKEND_NOT_WIRED`; nothing falls
back from headless to mock; nothing is read from an environment side effect
(asserted by AST over `StandaloneHost`); both branches import inside the method,
so selecting the mock imports no `forge_headless` module and selecting headless
imports no `forge_studio.mock_backend`.

`STUDIO_BACKEND=forge-headless` previously *looked* honoured while the mock ran,
because nothing consumed it. It now has to be passed in.

### Generation is opt-in, so Phase 2A is preserved

```python
HeadlessBackendAdapter(runtime, *, generation=None)
```

With no session injected every generation-side method refuses with
`HEADLESS_BACKEND_NOT_READY`, exactly as before, and the plumbing suite that
pins those refusals is unchanged.

### Translation

`forge_headless/studio_generation.py` maps Studio's 8-field request and 5-state
job onto the headless 18-field request and 9-state lifecycle.

```text
carried verbatim     all 8 Studio fields
backend-defaulted    sampler, scheduler, distilled CFG, batch size,
                     output count, operation, preview -- reported, not invented
pinned off           enable_hr, enable_adetailer, enable_extensions,
                     reference_image_enabled
refused              seed < 0 -> REQUEST_UNSUPPORTED, field "seed"
```

A random seed is refused rather than substituted: substituting one would make
the seed Studio reports back a lie.

The nine headless stages project onto five Studio states, with the stage carried
in `ProgressEvent.message` and the sampler counters in `step`/`total_steps`.
Studio's enum cannot express nine states and widening it would break every
existing consumer, so the stage travels beside the state.

### Errors, results, cancellation

Ten stable Studio codes, with every headless code mapped onto one and anything
unlisted becoming `GENERATION_FAILED` rather than leaking a runtime code. The
originating code lives on the exception for logs, never in `StructuredError`.
Records are scalar-only and retain no exception, traceback, or frame.

Result publication stays Studio's: the session returns contained metadata and
`ResultRegistry` mints the opaque handle. A result outlives adapter cleanup.

Cancellation is cooperative through `HeadlessProgress`. A job cancelled before it
runs never reaches the port at all.

### Four defects found by building it

```text
GenerationGateway.submit builds its OWN HeadlessProgress -- reading the wrong
  object made every job report "queued" forever
get_backend_status read the catalogue runtime only, so a configured session
  with no catalogue reported not-ready
the transport allowlist spells the field "model", not "model_id"
generation_authorized=False does NOT stop an injected port; the gateway
  filters that code out by design and the PORT is what gates generation
```

### What is not proved

The real Tier-0 generation port has not run through this seam. `load_model`
still refuses, `PolicyGatedGenerator` still refuses, and the proof uses the
repository's own `RecordingGenerator`. **One controlled live
frontend-to-backend smoke test remains required, and needs a fresh
authorization.**

```text
Studio canonical runner   849 OK, 3 skipped   (773 + 76)
canonical preflight       179 OK
frontend                  unchanged
Neo parity                0 0
```

The fixed 9,568,256-byte post-generation allocation remains deferred to
reliability testing. No broader feature-parity claim is made. Evidence:
`Evidence/studio-headless-backend-integration/`.

---

## 2.21 Product integration begins: a dual-mode composition root

The Tier-0 backend proof is closed. This section records the first product
integration slice, and one correction that changes how the migration is framed.

### Studio is not a Neo extension in this repository

The migration was described as moving Studio from
*extension -> Neo host -> Forge* to *application -> Studio host -> Forge*. The
first half of that premise does not hold here, verified five ways:

```text
on_ui_tabs                only its definition exists, modules/script_callbacks.py:479
                          zero callers repo-wide
Neo tab list              modules/ui.py:868-880 -- no Studio tab
extensions-builtin/       no forge-studio directory, no scripts/studio*.py
webui.py / launch.py      zero case-insensitive matches for "studio"
Neo FastAPI               /sdapi/v1/* and /internal/* only; no /studio route
```

Studio is hosted by its **own stdlib HTTP server** (`presentation.py:389`,
`:405`), reached through `launch_studio.py`, binding `127.0.0.1:7865` only in
`run_loopback_ui`. `webui.py` is a separate, disjoint Neo product in the same
tree with no code linking the two. The extension arrangement in the docs is the
upstream reference snapshot, which lives outside this Git repository.

So there was no in-repo extension mode to preserve, and none was removed. What
this slice delivers is the **seam** an extension host will plug into.

### The composition root

`forge_studio/composition.py`. `StudioHost` with `StandaloneHost` and
`ExtensionHost`, both feeding one `StudioApplication`.

```text
what existed already   application, backend port, result delivery, contracts,
                       mock backend, stdlib transport -- all reused unchanged
what was missing       the seam that assembles them, and any factory at all
```

`presentation._create_mock_presentation` and `run_socket_free_demo` each built
`StudioApplication(MockBackend(...))` inline, and `backend_selection` returned a
backend *name* that nothing constructed --
`docs/studio/FORGE_BACKEND_ADAPTER_READINESS.md:290` already named this as the
gap. Both now build through `build_standalone`.

`ExtensionHost` takes host facilities by **injection**, never by import.
`test_import_boundaries.py:131` asserts by AST that no file under
`forge_studio/` imports `gradio`, `modules`, `modules_forge`, or `webui`; an
extension adapter that imported Neo would have required weakening it.

Business logic cannot fork: `test_business_logic_lives_in_one_place` parses the
module and fails if either host defines `submit_generation`,
`poll_or_stream_progress`, `cancel_generation`, `get_result`, `result_asset`, or
`read_result_asset`.

### Two latent defects fixed on the way

**`ResultRegistry` fails closed when its root does not exist.** It resolves its
case policy by writing a probe file inside the root; an absent root yields
`INCONCLUSIVE` and every later `register` would raise `RESULT_OUTSIDE_ROOT`. The
inline arrangements constructed the registry before anything created the
directory, and worked only because it survived from an earlier run -- on a clean
checkout the first result would have failed. `build()` now creates the
configured root.

**The wait-to-terminal policy lived in the canonical-frontend adapter.**
`SourceFrontendAdapter.generate` owned a 120-second synchronous wait loop with a
timeout-then-cancel rule. A second host not reusing that adapter would have had
to reimplement it. It is now `composition.run_to_terminal` with a
`TerminalWaitPolicy` carrying the values the adapter always used, and the
adapter calls it.

### Standalone import safety, measured

From a fresh isolated subprocess, importing and building the composition pulls
in zero gradio, zero `modules`/`modules_forge`/`webui`, zero torch, zero `cuda`,
and no `socketserver`. No model, no CUDA, no image, no server, no port.

```text
Studio canonical runner   773 OK, 3 skipped     (718 + 55 new)
canonical preflight       179 OK
frontend                  unchanged
Neo parity                0 0
```

Broader parity remains unproven. Evidence:
`Evidence/studio-standalone-product-bootstrap/`.

---

## 2.20 Cleanup-only diagnostic: ABSOLUTE_ZERO, and the residual localized

One authorized model load and unload, no generation.

```text
                     allocated        reserved
B0                           0               0
B0_prime                     0               0
MODEL_READY      3,934,860,288   3,944,742,912
B1                           0   3,944,742,912
B2                           0               0
```

`B0_prime == B0` -- importing the retained Forge backend allocates **zero**
device bytes, so there is no non-zero runtime baseline hiding under the
residual. `B1.allocated == 0` was reached **before** any cache clear, with all
twelve owned weak references dead and the registry at its pre-load count, so the
release is attributable to reference dropping rather than to `empty_cache()`.

Registry counts stayed `0` throughout, confirming live what §2.19 traced
statically: direct-load registration is lazy, and this run performed no
conditioning, sampling, or decode, so none of the three entries was created.

`_HADAMARD_CACHE` was empty at every measurement point, including with the model
resident. That candidate is closed.

### The finding

```text
load + publish + unload                       ->  0 bytes retained
load + generation + publish + the same unload ->  9,568,256 retained
```

Same loader, same publication, same ownership contracts, same cleanup. The only
difference is generation. The residual is not the model session, not the
registry, not an import baseline, and not `_HADAMARD_CACHE` -- it belongs to
prompt setup, conditioning, sampling, or decode.

The golden anchor was accepted on combined evidence:
`docs/decisions/ADR-0009-TIER0-STANDALONE-GOLDEN-ANCHOR-ACCEPTED.md`. Attempt 06
still did not reach absolute-zero cleanup after generation, and that is not
rewritten.

One disclosure: the owned cleanup performed exactly one cache clear, and a second
`torch.cuda.empty_cache()` occurred inside Forge's own loader at
`backend/loader.py:828`. Attempts 05 and 06 counted only the Studio call and so
reported "exactly one"; the same call happened there and went unreported.

Evidence: `Evidence/studio-tier0-cuda-baseline-diagnostic/`.

---

## 2.19 Forge registry rejected as the residual owner; no teardown integrated

```text
Standalone Tier-0 generation and deterministic result reproduction are proven.

The remaining CUDA allocation is stable across two successful runs but has not
yet been classified as either a Forge registry retention or a legitimate
pre-load runtime baseline.

Golden-anchor acceptance is pending a Forge-native unload and comparison against
the measured pre-load CUDA baseline.
```

Attempt 06's named candidate — `backend.memory_management.current_loaded_models`,
drained by `unload_all_models()` — was investigated non-live and **rejected**.

### The registry retains nothing

```text
backend/memory_management.py:446   self._model = weakref.ref(model)
backend/memory_management.py:490   self.real_model = weakref.ref(real_model)
backend/memory_management.py:437-443
    the only strong state: a torch.device, a bool, two weakref.finalize handles
backend/memory_management.py:491   finalize(real_model, cleanup_models)
backend/memory_management.py:749-757
    cleanup_models pops every entry whose real_model() is None
```

A `LoadedModel` cannot keep a UNet, VAE, text encoder, `ModelPatcher`, or device
tensor alive. Popping an entry frees zero bytes, and stale entries prune
themselves. The single owning edge is `backend/patcher/base.py:172`, reached
through `model_data.sd_model` → engine → `ForgeObjects` → the three patchers —
which is exactly the chain Studio's `_release()` already walks
(`scripts/headless/first_image_probe.py:796-816`). That is why attempt 06's peak
of 6,790,045,696 bytes fell to 9,568,256: the weights are already being freed.

### The cache-clear policy is independently incompatible

```text
unload_all_models()   backend/memory_management.py:1373   no parameters
free_memory(...)      :573    no cache-control parameter
soft_empty_cache      :1343   `force` is accepted and never read
clear sites           :607 primary, :612 else-branch, :742 via cleanup_models_gc
```

Adding the native teardown in front of Studio's terminal clear yields **two**
`torch.cuda.empty_cache()` calls normally and three when any entry is dead. The
only no-clear removal path, `unload_model` (`:1358-1370`), never calls
`model_unload`/`detach`, so it moves no weights — a step that would report
success and do nothing. Neo's own path is not single-clear either: `free_memory`
clears once and `modules/sd_models.py:279` clears again.

**Cache-clear ownership remains explicit and singular** — exactly one intentional
clear, Studio's own, at `forge_headless/controlled_device.py:151`, sampled after
the reference release and before the clear.

### Registration trace

Nothing registers at load: `backend/loader.py:880` `forge_loader` never calls
`load_models_gpu`. Three entries appear lazily, one per component, at first use:

```text
text encoder   backend/diffusion_engine/anima.py:43
UNet           backend/sampling/sampling_function.py:381
VAE            backend/patcher/vae.py:211
```

Deduplicated by patcher identity (`__eq__` at `:515`, via `index` at `:640`).
The entries are separate objects from `shared.sd_model`, which holds the engine.

### What was and was not done

```text
runtime source changed                      none
Forge-native registry unload integrated     no -- two stop conditions fired
guard suite added                           40 tests, pinning the whole contract
cache clears in the owned cleanup           still exactly one
concrete residual owner identified          no
attempt 07                                  none
```

One static candidate remains unsettled and needs a live read, not a source read:
`backend/quant_rotation.py:13` `_HADAMARD_CACHE`, a module-level dict of device
tensors that is never evicted and is populated only on the INT8 rotation path.

A **cleanup-only live diagnostic** is requested — no image — recording `B0`
(after Torch/CUDA init, before any payload), `B0'` (after the Forge backend
import, which the current probe does not sample), `B1` (after release, before the
clear), `B2` (after it), plus registry entry counts and owned-model weak-reference
status. Under the owner's baseline-relative acceptance model that single run
decides between `RETURNED_TO_NONZERO_RUNTIME_BASELINE` and `ABOVE_BASELINE —
CLEANUP INCOMPLETE`.

Broader feature parity remains unproven. Evidence:
`Evidence/studio-tier0-forge-registry-teardown/`.

---

## 2.18 Attempt 06 reproduced the image byte-for-byte; the residual did not move

**The Tier-0 pipeline is deterministic.** Attempt 06 produced the same PNG as
attempt 05 — sha256 `6c23288148…0607e135`, 754,452 bytes, 768x768 RGB — from the
same seed and profile.

```text
sampler steps                              12 / 12
decode                                      1 call
ResultRegistry publication                  complete
post-release opaque-handle resolution       complete
resolved sampler / scheduler                Euler / Automatic, from the sampler object
all twelve Gradio counters                  zero
peak VRAM                       6,790,045,696 / 7,195,328,512   45% of ceiling
allocated before cache clear            9,568,256
allocated after cache clear             9,568,256
reserved after cache clear             23,068,672
```

20 of 21 golden-anchor criteria met. **Not accepted**, on cleanup alone.

### The decisive negative result

Attempt 06 released everything attempt 05 did **plus** the instance-level
`latents_after_sampling`, `pixels_after_sampling`, `extra_result_images`,
`modified_noise`, and the whole `Processed` object — all confirmed field by field
in the release report — and allocated changed by **exactly zero bytes**.

```text
attempt 04   13,762,560   no sampling, no decode, no class-cache release
attempt 05    9,568,256   class caches released          delta -4,194,304
attempt 06    9,568,256   + instance + Processed         delta 0
```

The 4 MiB step proves the measurement is sensitive to a real release of that
size. So §2.17's attribution to `p.latents_after_sampling` was correct as a
description of a real unreleased reference and **wrong about the bytes**.

`torch.cuda.memory_allocated()` counts live tensors in the caching allocator, so
9.13 MiB is live tensors, not CUDA context. Reserved fell from 6.08 GiB to 22 MiB
across the single clear while allocated did not move — the allocator returned
everything it could.

Evidence: `Evidence/studio-first-tier0-image/`.

---

## 2.17 First real image generated; post-decode teardown owner found

**Attempt 05 produced the first real standalone Studio-backend image.** One
768x768 RGB PNG, seed 123456789, through retained Forge/Neo conditioning,
sampling, and VAE decode, published through Studio's owned `ResultRegistry` with
an opaque handle.

```text
prompt setup / inner reload fast path      complete
dynamic callback fallback                  complete, through retained code
conditioning / initial noise               complete
denoiser entered                           yes
sampler steps                              12 / 12
decode calls                               1
PNG                                        valid 768x768 RGB, 754,452 bytes
ResultRegistry + opaque handle             proven, byte-identical round-trip
Gradio counters                            12 / 12 zero, across the full run
peak VRAM                                  6.32 GiB allocated, ceiling respected
```

This is the first complete demonstration that Studio can operate the retained
Forge generation backend without Neo owning application startup.

### Golden-anchor acceptance was withheld

Post-success CUDA teardown retained 9,568,256 allocated bytes across the cache
clear. That is the only unmet criterion.

### The owner has now been found — and it is not decode

Decoded samples are moved to the host at `modules/processing.py:1011`, so VAE
decode does not retain device memory.

The owner is `p.latents_after_sampling`: the sampler's CUDA output, appended at
`modules/processing.py:996`. Line 271 makes it an **instance** attribute
shadowing the class list at line 236 — and the previous cleanup cleared the
class attribute, an empty list, reporting success while the tensors stayed
resident. Forge's outer `process_images` wrapper would have released the
instance; Tier-0 bypasses that wrapper, the same structural gap that left
`p.close()` uncalled.

The release now empties the instance accumulators in place and detaches them,
releases `Processed`'s images and latents, and records which instance fields
were cleared so a future shadowing change is visible.

### Telemetry

Resolved sampler and scheduler are now captured from the sampler object Forge
builds, not from `Processed` — so they survive a failure after sampler creation.
Stage-timing measurement is proven by contract with a deterministic clock;
wiring conditioning, sampling, decode, and publication boundaries into the live
probe remains outstanding.

```text
Studio, both runners     643 -> 678 OK, 3 skipped
new post-decode suite    35 OK
prior five suites        36 / 28 / 23 / 23 / 18 OK, unchanged
preflight self-test      179 OK
```

**No attempt 06 has occurred, and a fresh authorization is required to prove
real `0/0/0`.** Broader feature parity — the outer wrapper, catalogue parity,
scripts and extensions, metadata parity, Hires, refiner, warm performance,
optional optimizers, batch size above 1, repeat generation — remains unproven.

Final golden-anchor acceptance is **not** claimed and must wait for a live run
that meets every criterion.

Evidence: `Evidence/studio-tier0-post-decode-release/` and the attempt-05 record
in `Evidence/studio-first-tier0-image/`.

---

## 2.16 Fourth Tier-0 attempt entered the denoiser; option fallback and failure cleanup fixed

Attempt 04 **passed the direct-load reload fast path** and went further than any
predecessor: identity, all three startup globals, prompt setup, initial noise,
and conditioning all completed, and the run entered the real
`CFGDenoiser.forward`.

```text
inner forge_model_reload   returned the loaded engine, reloaded=False
                           checkpoint selection / catalogue / loader / second payload: 0
conditioning               completed (inferred from failure position and a
                           1.87 GiB VRAM rise over every prior attempt)
initial noise              completed
CFGDenoiser.forward        entered
completed sampler steps    0 / 12
```

It stopped at:

```text
modules/sd_samplers_cfg_denoiser.py:134   cfg_denoiser_callback(denoiser_params)
modules/script_callbacks.py:192           getattr(shared.opts, "prioritized_callbacks_" + category, [])
HEADLESS_OPTION_NOT_AVAILABLE
```

### Studio's boundary was stricter than retained code expects

Forge wrote that `[]` default deliberately: the option is generated at runtime
by `modules/shared_items.py:154`, one per registered callback category, and
genuinely may not exist. `getattr` honours a default only on `AttributeError`,
and Studio raised a plain `HeadlessError`.

This is the first blocker of that kind — the previous three were things Studio
failed to supply.

**Missing options are now Python-compatible while direct reads stay strict.**
`HeadlessOptionMissing(HeadlessError, AttributeError)` makes
`getattr(opts, x, d) == d` and `hasattr(opts, x) is False`, while `opts.x` still
raises and `opts[x]` / `opts.require(x)` raise a plain `HeadlessError` no
`getattr` default can swallow. Every existing `except HeadlessError` still
matches. `modules/script_callbacks.py` is untouched, and the fallback is proven
for **all 21** declared categories, not only `cfg_denoiser`.

### The 13.1 MiB residual was identified and released

`p.close()` never runs on this path — the outer `process_images` wrapper owns
that call and Tier-0 skips it — and `persistent_cond_cache` defaults to `True`,
so even a `close()` that did run would skip `clear_prompt_cache()`. The
conditioning caches are **class attributes** on `StableDiffusionProcessing`
(`modules/processing.py:196-197`), so they outlive the instance, the traceback,
and `gc.collect()`.

`forge_headless/failure_cleanup.py` releases them, along with the sampler,
denoiser, latents, bridge preview state, and the exception's traceback frames,
before VRAM is measured. No additional `empty_cache()` call was added.

```text
Studio, both runners     607 -> 643 OK, 3 skipped
new option/cleanup suite 36 OK
prior four suites        28 / 23 / 23 / 18 OK, unchanged
preflight self-test      179 OK
```

**Attempt 05 has not occurred and requires fresh authorization.** No sampler
step has ever completed. Decode, publication, the outer `process_images`
wrapper, broader catalogue parity, scripts and extensions, metadata parity,
Hires, refiner, warm performance, and optional optimizers all remain unproven.

Evidence: `Evidence/studio-tier0-option-fallback-cleanup/`.

---

## 2.15 Third Tier-0 attempt reached prompt setup; inner-loop reload now a native no-op

### Correction to a claim this document carried from the start

> Entering at `process_images_inner` bypasses the outer wrapper's reload call,
> but **does not bypass reload entirely**. `process_images_inner` contains its
> own batch-loop `forge_model_reload()` call at `modules/processing.py:945`.

Every prior section, and the probe's own docstring, said the inner entry point
avoided `forge_model_reload()`. It avoids only `:785`, inside
`manage_model_and_prompt_cache`. Live attempt 03 proved the difference.

### Attempt 03

Consumed. It reached **prompt setup** and confirmed, against the actual Anima
session, that the identity contract and all three startup compatibility objects
work:

```text
identity attached, reads at :894-895 passed
prompt setup at :904 -> :407 passed        (prompt_styles supplied)
sd_unet identity read at :929 passed
compatibility context: all three installed, all three restored
owned cleanup 0/0/0 · all 12 Gradio counters serialized and zero
truthful denoising boundary: false, 0 of 12 steps
```

It stopped before conditioning:

```text
modules/processing.py:945   sd_models.forge_model_reload()
modules/sd_models.py:352    ValueError: Failed to find available model...
```

The direct-loaded engine was already in `model_data.sd_model`, but the reload
fast-path bookkeeping was absent, so Forge fell through to catalogue resolution.

### The fix: satisfy Forge's own early return

```python
current_hash = str(model_data.forge_loading_parameters)
if model_data.forge_hash == current_hash:
    return model_data.sd_model, False
```

That return precedes the catalogue lookup. `forge_headless/direct_load_reload.py`
installs both fields consistently at the seam that already attaches identity, so
the inner call returns the loaded engine with `reloaded=False` — proven against
the real function with checkpoint selection, catalogue lookup, and the loader all
doubled to raise, all with zero calls.

**No checkpoint catalogue entry is fabricated**, no catalogue lookup or payload
reread occurs on the direct-session no-op path, and nothing in `modules/`
changed. A mismatch test proves the native reload logic is still entered when the
bookkeeping disagrees — the contract is satisfied, not suppressed.

### Call inventory, not just field reads

The previous milestone guarded `shared.<field>` reads; attempt 03 failed on a
**call**. The closure is now inventoried for calls into reload, catalogue, and
model-selection machinery: **15 sites, 2 reachable** under the exact profile —
`apply_unet` at `:929` and `forge_model_reload` at `:945`, both with declared and
proven direct-load behavior.

```text
Studio, both runners     579 -> 607 OK, 3 skipped
new reload suite         28 OK
startup-globals / contract / P0   23 / 23 / 18 OK, unchanged
preflight self-test      179 OK
```

**Attempt 04 has not occurred, and a fresh authorization is required.** Nothing
past the inner reload has ever executed: initial noise, sampling, decode, and
publication remain unproven, as do the outer `process_images` wrapper, broader
catalogue/reload parity, scripts and extensions, metadata parity, Hires, refiner,
warm performance, and optional optimizers.

Evidence: `Evidence/studio-tier0-reload-bookkeeping/`.

---

## 2.14 Second Tier-0 attempt reached the real inner loop; startup globals now supplied

The second authorized attempt was consumed. It **reached the real inner
generation path** and confirmed, against the actual Anima session, that every
correction from §2.13 works:

```text
Tier-0 identity attached, reads at processing:894-895 passed
generation inner entered
owned engine cleanup, allocated 0 before AND after cache clear, reserved 0
all 12 Gradio counters serialized and zero
truthful denoising boundary: false, 0 of 12 steps
```

It stopped **before conditioning**:

```text
modules/processing.py:904   p.setup_prompts()
modules/processing.py:407   shared.prompt_styles.apply_styles_to_prompt(...)
AttributeError: 'NoneType' object has no attribute 'apply_styles_to_prompt'
```

`shared.prompt_styles` is assigned by `modules/shared_init.py`, which the
Gradio-free Studio boundary deliberately never runs.

### The pattern, and the sweep that ends it

Both failed attempts share one shape: the retained closure reads a
`modules.shared` field that `shared_init` supplies and Studio does not. Rather
than discover a third one authorization at a time, a full closure walk
inventoried all eight `shared_init` fields against the minimal txt2img path.

Five are read. Two were already supplied (`opts`, `state`). **Three were not:**

```text
prompt_styles   modules/processing.py:407          before conditioning
device          modules/rng.py:167                 during sampling
total_tqdm      modules/sd_samplers_common.py:433  during sampling
```

`shared.device` is the one a narrower sweep missed — it is read at the last line
of `ImageRNG.first()`, and `p.rng` is built at `processing.py:959` inside
`process_images_inner` and used by `p.sample()` at `:993`. Supplying only the
first two would have failed at initial noise creation, past the denoising
boundary.

`shared.total_tqdm.update()` sits one line after the owned bridge receives a real
sampling step, so it would have fired on the first denoising step.

### All three are now supplied

`forge_headless/headless_compat.py` installs them before
`process_images_inner`, using the production classes, and restores them in a
`finally`. `TotalTQDM` is silenced through the existing `multiple_tqdm` option,
so no bar is built and the owned progress bridge stays the only progress
authority. **Neither retained consumer was guarded** — `modules/processing.py`
and `modules/sd_samplers_common.py` are unchanged.

```text
Studio, both runners     556 -> 579 OK, 3 skipped
new startup-global suite 23 OK
Tier-0 contract suite    23 OK (unchanged)
P0 suite                 18 OK (unchanged)
preflight self-test      179 OK
```

A closure guard now fails on any newly reachable `shared_init` field that Studio
does not supply, so the next one surfaces in CI rather than in a live run.

**No third live attempt has occurred, and a fresh authorization is required.**
Everything past the first sampler step remains unproven: no run has sampled.

Still unproven, unchanged: the outer `process_images` wrapper, scripts,
extensions, metadata parity, catalogue/reload parity, Hires, refiner, warm
performance, and optional optimizers.

Evidence: `Evidence/studio-tier0-startup-globals/` and the preserved attempt
records in `Evidence/studio-first-tier0-image/`.

---

## 2.13 First Tier-0 attempt failed at the model-identity boundary; contract now fixed

The single authorized Tier-0 attempt was consumed. All three payloads opened
and the model loaded, but the run stopped **before conditioning and before any
denoising step**:

```text
modules/processing.py:894
    p.sd_model_name = shared.sd_model.sd_checkpoint_info.name_for_extra
AttributeError: 'Anima' object has no attribute 'sd_checkpoint_info'
```

`p.sample(...)` is at line 993, ~100 lines later. No image was generated.

### Root cause: a missing contract, not a bug in either half

Neo attaches catalogue identity in `forge_model_reload()`
(`modules/sd_models.py:373-375`). Tier-0 deliberately skips that wrapper — it
would re-resolve a checkpoint from Forge's catalogue instead of using the three
authorized files — and so also skipped the attachment that
`process_images_inner` reads unconditionally. The direct loader and the inner
loop never had a shared identity contract.

### The contract

`forge_headless/model_identity.py` attaches identity after the loader returns
and before `shared.sd_model` publication. Exactly three fields, being every
identity read reachable from `process_images_inner`:

```text
sd_checkpoint_info.name_for_extra   modules/processing.py:894
sd_model_hash                       modules/processing.py:895   -> None
sd_checkpoint_info.model_name       modules/sd_unet.py:20, reached from
                                    modules/processing.py:929
```

The third would have been the *next* failure: `opts.sd_unet` defaults to
`"Automatic"`, which sends `get_unet_option()` to the identity object.

`sd_model_hash` is `None` — not fabricated. Forge's own `calculate_shorthash()`
returns `None` when no sha256 is cached, and the consumers already handle it.
The label is a privacy-safe runtime constant, never derived from a path.
**`modules/processing.py` was not modified**; weakening Neo's assumptions would
have hidden the defect rather than fixed it.

This is a compatibility identity, **not** production metadata parity.

### Failure-path cleanup and telemetry are now durable

The attempt also lost its engine reference and its Gradio counters, because both
were harvested only on success. Both are now harvested in `finally`:

```text
engine released after a post-load exception    yes
owned cleanup distinguishable from process exit  yes
all 12 Gradio counters serialize on failure    yes, numeric, no repr fallback
```

Worker-process exit reclaiming VRAM is not owned cleanup and is not reported as
such.

### `denoising_started` now means denoising started

It was previously set immediately before `process_images_inner`, so a run that
never sampled reported `true`. It is now driven by a real sampler step arriving
through the owned state bridge. `decode_started` comes from the actual
`decode_latent_batch` entry point. `conditioning_started` is **UNKNOWN** — no
signal the owned bridge observes, and an honest unknown beats an optimistic
true.

```text
Studio, both runners     533 -> 556 OK, 3 skipped
new contract suite       23 OK
preflight self-test      179 OK
```

**No second live attempt has occurred, and another fresh authorization is
required.** The contract is proven against a synthetic engine only; the real
path past line 894 remains unproven until a live run.

Still unproven, unchanged: scripts, extensions, the outer `process_images`
wrapper, metadata parity, Hires, refiner, warm performance, and optimizer
extensions.

Evidence: `Evidence/studio-tier0-generation-contract-fix/` and the preserved
failed-attempt record in `Evidence/studio-first-tier0-image/`.

---

## 2.12 P0 source cycle — complete; all three edges deferred

The two edges §2.11 stopped at are fixed, by the same narrow local-import
deferral already applied to `modules/sd_models.py`:

```text
modules/sd_models.py:12        -> local at 389   sole use 391  opt_f
modules_forge/main_entry.py:12 -> local at 150   sole use 152  need_global_unload
modules/infotext_utils.py:14   -> local at 280   sole use 282  old_hires_fix_first_pass_dimensions
```

**The source cycle is fixed — not merely the harness order.** Commit `2c0c322c`
reordered imports inside the probe, which avoided the defect without removing
it. These three edits remove it from Forge's own source, so every caller
benefits, not just the probe.

```text
Studio, both runners        533 tests, OK, 3 skipped   (515 pre-existing + 18 new)
generation import order     18/18 pass  (was 7/13)
import both directions      PASS
shared.sd_model setter      PASS in both orders and alone
opt_f preservation          runtime-proven (previously blocked)
need_global_unload          preserved, incl. control-flow position
old-Hires delegation        exact arguments and returns preserved
preflight self-test         179 OK
Gradio runtime guards       12 instrumented, 0 uninstrumented, all counters zero
```

### No fourth edge

A complete static inventory over `modules/`, `modules_forge/`, `scripts/`, and
`extensions-builtin/` finds **16** module-scope importers of
`modules.processing`, of which **zero** are reachable from `modules.sd_models`
initialization. A runtime import trace independently confirms `processing` is
never imported during `sd_models` initialization. The remaining 16 are UI-only,
compatibility-only, or generation-runtime callers that must stay module-scope
for now; removing them is not required to break the cycle.

### What this does and does not license

- **The model-load path remains Gradio-free.**
- **The minimal generation path may import Gradio as a compatibility library.**
  `modules.processing` keeps 17 module-level paths to it. Twelve UI, queue,
  server, route, cache, and delivery guard points are instrumented and measured
  zero — that is the claim, and "Gradio-free generation" still is not.
- The next live run remains a **Tier-0 minimal inference proof**, requiring
  fresh owner authorization.

Still unproven, unchanged from §2.11: scripts, extensions, metadata parity, the
outer `process_images` wrapper, Hires, refiner, warm performance, optimizer
extensions, API parity, and Mac MPS behavior.

Evidence: `Evidence/studio-p0-source-cycle-fix/` (followup files).

---

## 2.11 P0 source cycle — partially fixed; the cycle has three edges, not one

> **Superseded by §2.12**, which completes the remaining two edges. Retained as
> the record of how the three-edge finding was reached.


The import cycle that stopped §2.10 was traced to source and partially
removed. `modules/sd_models.py` no longer imports `modules.processing` at
module scope; the sole use at the `opt_f` assignment now takes a local import.
Eight insertions, one deletion, in one file.

**This did not clear the cycle.** `import modules.sd_models` still raises the
original `ImportError`, because `modules.processing` has seven module-scope
importers and two of them are reachable from `sd_models`:

```text
modules_forge/main_entry.py:12   sole use line 147
                                 processing.need_global_unload = True
modules/infotext_utils.py:14     sole use line 275
                                 processing.old_hires_fix_first_pass_dimensions
```

Both confirmed live — the first by a runtime import hook, the second from the
canonical runner's own traceback. Both are structurally identical to the edge
already fixed: one module-scope import serving exactly one attribute access.

This corrects the reference package, which described the cycle as a single
edge in `sd_models.py`.

```text
Studio, both runners     528 tests, 6 failures, 3 skipped
515 pre-existing         all pass — no regression
13 new import-order      7 pass, 6 fail, one shared cause
import order             processing-first PASS   sd_models-first FAIL
Gradio runtime guards    12 instrumented, 0 uninstrumented, all counters zero
```

The six failures are the new regression tests correctly detecting the two
remaining edges. They are the acceptance criteria for the follow-up: when
those edges are cleared, all six should pass unchanged.

Work stopped at the handoff's stop condition *"either import order fails"*
rather than expanding into P3 and P5 territory unauthorized. **P3 remains the
hard gate.** No live generation was authorized or attempted.

### This is a source fix, not the earlier harness reorder

§2.10's failure was worked around in `2c0c322c` by reordering imports **inside
the probe harness**. That left the defect in place and only avoided tripping
it. The change recorded here edits `modules/sd_models.py` itself. The two
should not be confused: the harness reorder is now redundant for the edge it
avoided, and inert for the two that remain.

### What is Gradio-free, and what is not

- **The load path is Gradio-free.** Model intake, catalogue, policy,
  authorization, device control, and the load itself reach Gradio through no
  module-level edge.
- **The retained generation closure is Gradio-importing compatibility code.**
  `modules.processing` keeps 17 module-level paths to Gradio. Importing it
  imports Gradio. That is expected and is not a defect.

The pending first image must therefore **not** be described as "Gradio-free
generation". The accurate claim is the one the probe emits only when every
runtime counter is zero: Gradio may be imported as a compatibility library,
but no UI, queue, server, route, or delivery path is constructed or launched.

### What the next live attempt would and would not prove

It would be a **minimal Tier-0 Neo-compatible inference proof** — one real
Anima txt2img through retained Neo sampling and VAE code, published through the
Studio ResultRegistry. Not production parity.

Explicitly unproven, and remaining so:

```text
scripts and extensions        metadata parity        Hires
refiner                       warm performance       API parity
production process_images wrapper semantics
option override and restore   checkpoint switching and reload
save and grid behavior        Mac MPS behavior
```

Evidence: `Evidence/studio-p0-source-cycle-fix/`.

---

## 2.10 First controlled image — attempted, failed before denoising

```text
FIRST_IMAGE_FAILED. No image was generated, nothing was published, and the
denoising boundary was not crossed.
```

CUDA initialised under the 14 GiB ceiling, all three authorized files were read
once each, and peak allocation reached 3.66 GiB -- identical to the proven
Phase 2B load. The run then failed on a circular import:

```text
modules/sd_models.py:12   from modules import ... processing ...
modules/processing.py:32  from modules.sd_models import apply_token_merging, ...
```

The cycle resolves only when `modules.processing` is imported first. The probe
published the engine with `shared.sd_model = engine` one line earlier, and that
property setter (`modules/shared_items.py:175`) does `import modules.sd_models`,
imposing the fatal order. A harness defect, not Forge and not the model.

Fixed and verified CPU-only; **not re-run**. Tensor payloads were read even
though denoising was not reached, so whether the single authorized attempt is
spent is a scope judgement for the owner, and the failure policy says to stop and
report when uncertain.

One thing the run did establish: `modules.processing` imports Gradio as a
library through the three edges documented since Phase 2A. No Gradio *UI* is
constructed -- the probe now counts `Blocks.__init__` and refuses `launch()` to
prove the difference rather than assert it.

Detail: `Evidence\studio-first-image\`.

---

## 2.9 First-image readiness — the remaining two gaps are closed

```text
Studio is ready for one separately authorized controlled test image.
No real generation occurred in this readiness phase.
```

Phase 2B left two named gaps. Both are closed.

**Generation options.** An AST scan of the Anima txt2img closure found 99
distinct settings; the declared minimal inventory is 31 and all 31 resolve from
Forge's own source. Six lived outside `modules/shared_options.py` and one used
the keyword declaration form, so the parser now reads
`modules_forge/shared_options.py` and `modules/processing_scripts/*.py` and
accepts `OptionInfo(default=...)`. Two output directories are computed at import
time and are supplied as explicit, contained overrides.

**Progress.** `HeadlessProgress` owns nine lifecycle states with an explicit
transition table, and `ForgeStateBridge` exposes exactly the fourteen
`shared.state` attributes the closure touches. No fake percentages, terminal
states never regress, cancellation is cooperative, and a cancelled job publishes
nothing. `modules/shared_state.py` is untouched.

**Proven end to end without generating:** `FIRST_IMAGE_READINESS_PROVEN`, 10/10 —
request validated, port reached, `HEADLESS_GENERATION_NOT_AUTHORIZED` returned,
denoise and decode call counts zero, no Torch, no CUDA, no Gradio, no network.

What the first image now needs is one authorization carrying the bounded profile,
the test prompt, and the same limits the load used.

---

## 2.8 Phase 2B retry — every precondition is now met

```text
The owner-authorized triplet loaded. P3 and P4 are satisfied.
```

Torch imports, CUDA initializes under an enforced 14 GiB ceiling, the retained
loader reads all three authorized files, and an `Anima` engine constructs with
unet, clip, and vae resident -- with Gradio absent throughout. Peak 3.665 GiB,
12.0 seconds, unloaded to zero.

**What a first image still needs**, beyond a separate authorization:

1. **Generation-time options.** `opts.anima_do_reference` is read by
   `encode_first_stage`, and a sampler pass will read more. The headless options
   boundary reports anything it cannot supply *by name*, so the full list can be
   discovered in one run instead of one failure at a time.
2. **Progress.** Forge drives progress through `modules.shared.state`, which the
   boundary does not provide. A generation can run without it; a *reportable*
   one cannot. This is the last genuinely unbuilt piece.

Neither requires a Gradio version change, and neither blocks a single controlled
test image.

---

## 2.7 Phase 2B — P3 and P4 are now met, one step short

```text
Phase 2B: FAILED AFTER COMPATIBLE PREFLIGHT.
No checkpoint remained resident; no image was generated.
```

**P4 is satisfied.** The owner named three exact files, and they are compatible:
an Anima DiT, a Qwen3-0.6B encoder, and a Wan-layout 16-channel VAE, all matching
the rules the retained loader applies.

**P3 is all but satisfied.** Torch imports, CUDA initializes under an enforced
14 GiB ceiling, and the retained loader reads every file. What blocked
construction was `modules.shared.opts` being `None` at
`backend/text_processing/anima_engine.py:24` -- one missing call to Forge's own
`shared_init.initialize()`.

**What the first image now needs:** one new authorization for one more
controlled load attempt, with the option-initialization fix in place. Same files,
same loader, same device, same limits. Then, separately, authorization to
generate.

**Progress is still the open design problem.** Anima's engine drives progress
through `modules.shared.state`, and there is still no `backend/` equivalent.

---

## 2.6 Phase 2A narrows P3 again, and moves P4

```text
Phase 2A validates model selection and loader handoff.
It does not open or load a checkpoint.
```

**P3 step 1 is done.** "Authorize editing `modules/` to clear four Gradio edges"
is complete — and it turned out to be seven files, because the four-edge count
was an artefact of a shortest-path analysis. `modules.shared`,
`modules.script_callbacks`, `modules.extensions`, `modules.options`, and
`modules.shared_items` now have zero module-level paths to Gradio.

**P3 step 2 is now a one-line source change plus one authorization.**
`modules/shared.py:7` imports `backend.memory_management` for exactly one value,
`xformers_available`, read by exactly one caller, `modules/errors.py:111`. Making
it lazy removes the import; actually loading a model still needs device-init
authorization.

**P4 changed shape.** The owner no longer needs to authorize a scan or an
external directory: `STUDIO_MODEL_ROOT` names one explicit root, containment is
enforced, and nothing is auto-discovered. What is still needed is one exact
checkpoint path, permission to open that one file, permission to import Torch,
permission to initialize the device, a timeout, a VRAM ceiling, and defined
rollback behaviour.

**Progress remains the open design problem**, unchanged by Phase 2A: Forge
progress lives on `modules.shared.state` and has no `backend/` equivalent.

---

## 2.5 The headless boundary changes P1 and P3

Gradio excision Phase 1 replaced the assumption that a first real image requires
launching Forge's Gradio process.

**P1 is met and then some.** Beyond the six contract items, an owned headless
facade now reaches `READY_NO_MODEL` with `gradio` and `gradio_client` blocked at
`sys.meta_path`, importing four real retained Forge modules.

**P3 is narrower than stated.** It is no longer "authorize a Forge launch" -- the
Gradio UI need never start. What it becomes:

1. authorize editing `modules/` to clear four Gradio edges (Excision Phase 2);
2. authorize importing `backend.memory_management`, which probes device memory at
   module scope and so necessarily initialises a device;
3. authorize loading the owner-named checkpoint.

That is a smaller and better-bounded ask than a full UI launch, and steps 1 and
2 are separable.

**Progress is the substantive unknown.** Forge progress lives on
`modules.shared.state` (`sampling_step`, `sampling_steps`, `interrupt`, `skip`)
and has no `backend/` equivalent. A headless progress source has to be built,
not just unblocked.

---

## 3. Adapter responsibilities

`ForgeBackendAdapter` implements `BackendAdapter` and owns, exclusively:

1. translating `GenerationRequest` into a retained Forge processing call;
2. serializing generation on its own gate — Forge's model, sampler, and
   progress state are process-global singletons;
3. mapping Forge progress to `ProgressEvent` with real `step`/`total_steps`;
4. reporting the resolved seed as a backend fact;
5. reporting selected-model capability, including exact alignment and safe
   limits;
6. owning the output file and its metadata sidecar;
7. mapping failures to `StructuredError` without leaking paths or stack traces;
8. cooperative cancellation that keeps terminal states terminal;
9. releasing the model, CUDA context, and VRAM on `shutdown()`.

It must **not**: construct Gradio UI, reference Neo component IDs, events, tabs,
JavaScript globals, or layout modules, or import from `modules/ui*`. Neo UI must
remain removable (State Snapshot §17).

---

## 4. Retained Forge entry points

To be confirmed by static reading before implementation, not assumed:

| Concern | Expected retained surface |
|---|---|
| Checkpoint load | `modules.sd_models` — load/reload by checkpoint info |
| txt2img processing | `modules.processing` — `StableDiffusionProcessingTxt2Img`, `process_images` |
| Progress / interruption | `modules.shared.state` — `sampling_step`, `sampling_steps`, `interrupt`, `skip`, `job` |
| Sampler and scheduler inventory | `modules.sd_samplers` |
| Options | `modules.shared.opts` |

Two hazards recorded from the dependency inventory:

- `modules/ui_tempdir.py` patches `gradio.processing_utils.save_pil_to_cache`
  and `async_move_files_to_cache`. **The adapter must not route real output
  through Gradio's cache.** It owns its own persistence.
- `modules/shared.state` is global. Concurrent access from the HTTP layer and
  the adapter must be mediated by the adapter's gate, not by hope.

---

## 5. Model load ownership

The **adapter** loads. The frontend adapter does not.

```text
list_models()        enumerate the owner-named checkpoint only; pure read
get_current_model()  pure read; no load, no mutation
load_model(id)       explicit, idempotent; failure is a StructuredError
submit_generation()  fails with a structured error if no model is resident
```

Generation must never load implicitly — that is the A5 correction, and it
matters more with a real backend where an implicit load costs seconds and VRAM.

`list_models()` for the first image enumerates exactly the one owner-named
checkpoint. It does not scan model directories.

---

## 6. Request mapping

| `GenerationRequest` | Forge target | Note |
|---|---|---|
| `model_id` | resident checkpoint | must already be loaded |
| `positive_prompt` | prompt | empty is valid (A6) |
| `negative_prompt` | negative prompt | negative-only is valid |
| `seed` | seed | `-1` means randomize; resolved value returned in metadata |
| `steps` | steps | |
| `cfg_scale` | CFG scale | |
| `width` / `height` | width / height | forwarded exactly; see below |

Dimensions: forwarded without rounding. If the selected model's capability
cannot honour the request, the adapter either rejects with a structured error or
reports requested-versus-effective explicitly. **It must not silently round**,
and must not inherit the retained API's silent latent-floor mismatch (VAE
factor 8, Flux2 16).

Sampler and scheduler are **not** request fields. For the first image the
adapter uses the backend's configured default and records what was actually
used as provenance (§10). This is the honest reading of A1: report owned facts,
fabricate nothing.

---

## 7. Progress mapping

```text
Forge state.sampling_step / sampling_steps
  -> ProgressEvent.step / total_steps        (exact integers)
  -> ProgressEvent.progress                  (derived percent, 0-100)
  -> monotonically increasing sequence
```

Observation stays non-consuming: repeated polls may return the same event and
must never advance, complete, cancel, or fail a job. Two observers of one job
must both see progress. Polling must not materialize the image — only the
terminal `completed` poll retrieves a result.

---

## 8. Cancellation mapping

```text
cancel_generation(job_id)
  -> set Forge interrupt
  -> return CancellationResult immediately; do not block on the sampler
```

Forge interruption is cooperative and checked between steps, so cancellation is
not immediate and may land after the job already completed. Required behaviour:

- a completed job stays completed; cancellation reports `cancelled: false`;
- a cancelled job never resurrects into completed;
- residual sampler, model, LoRA, and script state is cleared so the next
  request is unaffected (AGENTS.md hard rule);
- the next generation succeeds — this is the "recovery" check the mock suite
  already covers, and it must hold for real.

---

## 9. Resolved seed and effective dimensions

```text
GeneratedResult.metadata["seed"]    Forge's actual resolved seed, never -1, never the request echo
metadata["requested_width/height"]  as requested
metadata["effective_width/height"]  as generated
```

If requested and effective differ, both are recorded and the difference is
surfaced. A silent difference is a defect.

---

## 10. Sampler and scheduler provenance

Recorded as what the backend actually used, read back after processing — not
what a caller asked for and not a hardcoded string. This is the direct
counterpart of the H-1 correction, which removed a fabricated
`Sampler: DPM++ 2M SDE, Schedule type: Karras` from infotext. Infotext must
gain sampler and scheduler only once they are genuine backend readbacks.

---

## 11. Result ownership and browser delivery — implemented

```text
adapter writes:  <owned output root>/<job_id>.png
                 <owned output root>/<job_id>.json
returns:         GeneratedResult(output_path=<real path>, metadata_path=<real path>,
                                 image_data_url=None, mime_type="image/png")
```

The delivery boundary exists. The adapter's only obligations are to write
beneath an owned root and to declare the correct `mime_type`; everything below
already happens:

- `StudioApplication` mints an opaque handle
  (`studio-result/<32 hex>.png`) and registers the file, rejecting anything
  that does not resolve to a regular file beneath the injected root;
- `presentation.poll` strips `output_path` and `metadata_path` and adds
  `image_handle`;
- `SourceFrontendAdapter` emits
  `session_entries[0] = {entry_id, source: "scratch", path: <handle>}`;
- the canonical frontend turns that into
  `/studio/file?path=<handle>` unchanged — no vendored file is modified;
- `GET /studio/file` serves exact bytes under
  `default-src 'none'; …; sandbox`, `no-store`, `nosniff`, with an exact
  `Content-Length` and no path-bearing header;
- lookup is an exact registry match, so traversal, absolute paths, encoded and
  double-encoded separators, and even a *real registered filesystem path* all
  return 404;
- containment is re-verified on every read, so a symlink swapped in after
  registration fails closed.

For a real PNG the adapter should set `image_data_url=None`. The bridge then
emits a one-element `images` array containing an empty string, and the
canonical client renders from the session-entry URL. Inline base64 remains
supported and is what the mock still uses.

Full contract: [`STUDIO_RESULT_DELIVERY_CONTRACT.md`](STUDIO_RESULT_DELIVERY_CONTRACT.md).

One item to confirm at step 10: `image/png` is already in the delivery
allowlist, but no real PNG has ever passed through this path. The mock
exercises it with `image/svg+xml`.

---

## 11.5 Device capability reporting

At step 6, after `load_model()`, the adapter reports the active environment
through `ModelCapability`:

```text
device_type        cuda | mps | cpu | xpu | directml | unknown
dtype_policy       fp32 | fp16 | bf16 | mixed | backend_default | unknown
attention_backend  pytorch_sdpa | xformers | sage | flash | backend_default | unknown
```

These describe the **device**, never the host OS, and must be read from
`backend.memory_management` rather than inferred. Record them in evidence
alongside CUDA identity at step 4: a first real image whose device provenance
is unknown is much weaker evidence.

Per-model limits may legitimately differ by device and dtype, so capability and
dimension limits are reported together.

---

## 12. Shutdown

```text
shutdown()  interrupt any in-flight generation
            release the checkpoint
            release CUDA allocations
            join owned threads
            idempotent; safe with a job in flight
```

Required by Acceptance Matrix G12 and G13.

---

## 13. Execution sequence

| Step | Action | Evidence |
|---|---|---|
| 1 | Verify P1-P6; stop on any gap | preconditions record |
| 2 | Launch Forge per the G8 receipt, once | startup log, wall-clock to ready |
| 3 | Confirm loopback readiness; foreign Host/Origin rejected | response capture, CSP header |
| 4 | Record CUDA identity | device, capability, driver, VRAM, torch build |
| 5 | `list_models()` — exactly the owner-named checkpoint, pure read | response |
| 6 | `load_model()` — explicit; record load duration and VRAM delta | residency record |
| 7 | Submit one txt2img request with a fixed seed and modest steps | request record |
| 8 | Observe progress from two concurrent observers | ordered event log from both |
| 9 | Retrieve the result once | `GeneratedResult`, no internal paths in the browser response |
| 10 | Verify the image renders in the browser via the delivery boundary | screenshot or byte-length + digest |
| 11 | Verify metadata: resolved seed, requested vs effective dimensions, sampler/scheduler provenance | metadata sidecar |
| 12 | Submit a second request and cancel mid-generation | cancellation record, terminal state |
| 13 | Verify residual state cleared | post-cancel status read |
| 14 | Repeat generation with the step-7 request and identical seed | second result, digest comparison |
| 15 | `shutdown()`; verify VRAM released, port released, no orphan | shutdown record |

Step 14 tests reproducibility of the pipeline, not performance. Per AGENTS.md,
performance must not be compared across server restarts as proof, and no
optimization work belongs in this plan.

---

## 14. Evidence

Under `Evidence/studio-first-real-image/`:

```text
preconditions.md          P1-P6 with verification method per item
startup.log               full, unedited
cuda-identity.json        device, capability, driver, VRAM, torch build
model-load.json           checkpoint identity, load duration, VRAM delta
request.json              exact request
progress-observer-a.jsonl ordered events
progress-observer-b.jsonl ordered events, concurrent with A
result.json              GeneratedResult with internal paths retained (evidence only)
browser-response.json    what the browser actually received; must contain no filesystem path
metadata.json            resolved seed, requested vs effective, sampler/scheduler provenance
image.sha256             digest only
cancellation.json        cancel request, result, terminal state, post-cancel status
repeat.json              second generation, digest comparison
shutdown.json            method, exit, VRAM released, port released, orphan check
```

The image itself is **not** committed — AGENTS.md prohibits committing model
weights, outputs, private logs, tokens, and user paths. The digest is the
record. Any checkpoint path supplied by the owner is redacted from committed
evidence.

---

## 15. Stop conditions

Halt immediately and report:

- any precondition unverifiable;
- Forge attempts any network fetch not authorized by G8 — extension update,
  font, asset, or model download;
- CUDA initialization or model load without explicit G8 permission;
- a filesystem path reaches the browser response;
- the delivery boundary serves outside the owned output root;
- dimensions silently differ from the request with no requested-vs-effective
  record;
- infotext contains any sampler, scheduler, or seed value that is not a genuine
  backend readback;
- a cancelled job reports completed, or a completed job reports cancelled;
- the second generation fails after a cancellation;
- shutdown leaves VRAM allocated, the port bound, or a thread orphaned;
- any canonical frontend file changes;
- Neo parity leaves `0 0`;
- any access outside `Studio-Standalone\`, or any `Private-Local` access.

---

## 16. Not authorized

```text
Forge launch          CUDA init         model load        real generation
network access        package mutation  push              Private-Local
model directory discovery outside the workspace
```

Each requires its own owner receipt. This document plans; it does not permit.
