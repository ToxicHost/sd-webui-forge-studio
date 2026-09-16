# Project State

## 2026-09-15 — Git clone/pull installation

Owner chose GitHub clone/pull delivery after reviewing the portable ZIP size.
The checkout now has Start-Studio.bat and a thin adapter over the existing release
launcher. Setup creates/repairs its venv and installs the same six hash-pinned
auxiliary assets from a versioned GitHub release (or an explicit local cache).
Existing settings and differing asset files are preserved. Configuration/state
live beside the clone; checkpoint/VAE/text-encoder convenience folders start empty.
No automatic source updates, inference changes or Neo-owned edits.

Publication preparation found private machine paths in 15 historical blobs across
five files, although the current source passes privacy checks. Preserve the full
local development branch and publish only a clean current-tree snapshot parented
to public Neo 97ff3a40. Do not publish the old development ancestry.

The six assets (132,628,390 bytes) are staged locally under dist/git-install-assets;
they remain outside Git. The source branch and studio-assets-v1 release must both
be published before the documented online first run can work. Focused regressions passed (50 tests, one Windows symlink-privilege skip).
VERIFIED-LIVE: fresh Python 3.13.5 venv installation from the verified local wheel
and asset caches; normal Git fast-forward update; offline repeat; settings
preservation; empty model folders; pip check; non-mock startup with 23 samplers,
18 schedulers, six latent/four image upscalers and five verified detectors.
The initial fresh-clone check caught inherited placeholder text files; those
three files were removed from Git and the real update/recheck passed.

The separate legacy tester-guide suite has nine failing wording assertions in
seven tests; all required phrases were already absent at baseline fa19f9de.
These are recorded as pre-existing documentation-test debt, not product failures
or a full-suite pass. Public internet setup and real image generation were not
exercised. Final source identity, raw validation and publication status are in
Evidence/git-install-2026-09-15/SESSION_HANDOFF.md. Source review is in that envelope's
SOURCE_REVIEW.md. See docs/studio/GIT_INSTALL.md for installation and updates.
This remains an internal alpha; existing feature, advisory and license limitations
remain in TESTER_FEATURE_STATUS.md, PORTABLE_RUNTIME.md and BUNDLED_MODEL_ASSETS.md.

## 2026-09-13 — portable Windows/NVIDIA implementation

Owner requested the actual portable build in the repository. The profile now
bundles official CPython 3.13.15 and 95 hash-pinned libraries, preserves the six
approved auxiliary assets, and supplies empty checkpoint/VAE/text-encoder folders.
The launcher uses its private interpreter without pip, system-Python selection or
Git. Fresh extraction exposed missing pytz and GitPython's import-time Git check;
pytz 2026.2 is now declared and the portable adapter enables optional-Git import.
No engine or feature behavior was changed.

VERIFIED-LIVE: private-runtime startup in a path with spaces with empty PATH and
invalid PYTHONHOME/PYTHONPATH; 23 samplers, 18 schedulers, six latent upscalers,
four image upscalers and five verified detectors; configuration preservation;
CUDA tensor computation on RTX 5060 Ti. VERIFIED-TEST: focused packaging,
launcher, input-hash, traversal and privacy regressions. Final archive checksums,
source commit, command results and publication status are recorded alongside the
artifact and in Evidence/portable-build-2026-09-13/SESSION_HANDOFF.md.

This remains an internal alpha: real model generation and clean-Windows acceptance
are not certified. Ten dependency versions carry advisory matches; applicability
review/upgrades and the existing Remacri license-provenance gap remain open.
See docs/studio/PORTABLE_RUNTIME.md and TESTER_FEATURE_STATUS.md. Source review:
Evidence/portable-build-2026-09-13/SOURCE_REVIEW.md. Large binary assets belong in
GitHub Releases, with build code, locks and instructions tracked in Git.

## 2026-09-13 — current Standalone saved; tester package prepared

Branch release/tester-alpha-prep. Existing Canvas work preserved in de0a11ea,
including its tests; only stale aggregate fingerprint metadata was refreshed.
Release launchers are now versioned, first-run config uses model_roots,
requirements repair is checked, ultralytics is declared, and six explicit assets
are hash-verified at build time. Python minimum is 3.11 for the existing NumPy pin.

53 packaging/archive tests and 464 service-contract tests passed. Existing
Canvas set: 147/148 initially, remaining metadata check passed after correction.
Dependency dry-run succeeded; extracted source started with empty model roots
using the existing interpreter. All archive member hashes verified.
Real generation, a fresh installed environment and interactive browser/tablet
certification are still pending. See docs/studio/RELEASE_PREPARATION_REVIEW.md.

The remote push was rejected by automatic approval review pending explicit
approval for the source and destination; no upload occurred. Package artifacts
and evidence live under Evidence/package-readiness-2026-09-13 in the workspace.


## 2026-09-13 — tester package inventory checkpoint

Owner requested review, limited cleanup preserving functionality, and
usable/unavailable documentation, with contents explained before assembly.
See docs/studio/TESTER_PACKAGE_PROPOSAL.md and the workspace evidence handoff
Evidence/package-readiness-2026-09-13/SESSION_HANDOFF.md.

Documentation only: no new archive or runtime changes. Source review found an
obsolete profiles lookup in first-run configuration, omission of six documented
auxiliary weights from the builder, and no direct ultralytics declaration in
main requirements despite its runtime import. The six asset hashes match.
Existing Canvas/test changes are preserved. Current usability and fresh-install
execution remain to be verified; historical alpha evidence is not a new pass.


## Authority

The current master planning documents are, in precedence order:

```text
1. Reference/CLAUDE_CODE_STUDIO_STANDALONE_MASTER_EXECUTION_BOOK.md
2. Reference/STUDIO_STANDALONE_FULL_SCOPE_PROJECT_BOOK.md
3. the supplied Forge Neo source, as the behavior oracle for model
   loading and generation
4. the accepted standalone security, memory, result and ownership contracts
5. the supplied Studio extension source, as a feature/edge-case reference
6. earlier project and handoff documents, including this file's history
```

Where an earlier document and the books disagree, the books win. In
particular, no earlier statement that runtime profiles or an explicit Load
action are product requirements is still in force. Those were transitional.

`Evidence/studio-profile-removal/PROJECT_PLAN.md` is **superseded**: it
correctly identified that the profile concept must go, but still modeled an
explicit `Load -> READY` owner step, which is not the target experience. It is
retained as history.

## Product contract

```text
owner chooses Checkpoint / Text Encoder / VAE
  -> owner presses Generate
  -> Studio ensures the selected model is resident (load / reuse / switch)
  -> base -> Hires -> Auto Detail -> one final result
```

There is no owner-facing model profile, no profile configuration, no profile
API, no `PROFILE_SELECTED` product state, and no mandatory Load button. A
manual unload may remain as an optional VRAM action; it is never a
prerequisite for generating.

The loading semantics to preserve are Forge Neo's: selecting a dropdown
updates desired loading parameters without loading; generation compares the
desired selection against the resident one; unchanged reuses the warm session,
changed unloads and reloads once, then generates.

## Resolved scope decisions

Features the owner has ruled OUT, with the reason. Kept beside the product
contract rather than under "Unresolved decisions", because these are settled:
listing a closed ruling next to open questions is how it gets reopened.

Superseded is not missing. A missing feature is work owed; a superseded one is
work deliberately not done, and a ledger or handoff that reports them the same
way turns a decision back into a bug report. Each entry also appears in
`docs/15_PARITY_LEDGER.md` under "Feature decisions", generated from
`FEATURE_DECISIONS` in `scripts/parity_ledger.py`.

### INPAINT_SKETCH — intentionally superseded, 2026-08-19

Intentionally superseded by Canvas painting and Canvas mask workflows; not a
Studio 1.0 feature.

Painting directly on the image is already a native Canvas operation. A separate
Inpaint Sketch mode, its dedicated controls, and its dilation behaviour would
duplicate functionality Studio already has and rebuild a Gradio-era workflow
Studio exists to replace.

Do not port `_studio_inpaint_sketch_*`, add an Inpaint Sketch operation, or
create corresponding UI.

What was checked before ruling, so the decision is not resting on an assumption:

- The Extension's `_prepare_mask` (`studio_generation.py:778-800`) is SHARED by
  every inpaint mode. Its sketch-exclusive part is one block at :787-792 — a
  `MaxFilter` dilation of `int(0.015 * short_side) * 2 + 1`.
- Everything else in that function — the LANCZOS resize to the target size, the
  `>= 10` emptiness check, the `> 128` binarisation — is ordinary inpaint
  behaviour and is ALREADY ported, in
  `forge_headless/input_assets.py::decode_mask`. Declining the mode therefore
  loses no shared logic.
- Neo's own matches (`modules/ui.py`, `modules/shared_options.py`,
  `javascript/ui.js`, `javascript/aspectRatioOverlay.js`) are Gradio-era UI,
  which Studio does not use.
- `forge_studio/frontend/canvas-core.js` retains two
  `S.inpaintMode === "Inpaint Sketch"` branches (:3235, :3818). They are
  UNREACHABLE: the only two assignments to that field, at `canvas-core.js:3815`
  and `canvas-ui.js:4448`, both set `"Inpaint"`, and no control in
  `index.html` or `app.js` offers the mode. So this is residual code, NOT a
  dead control — there is nothing an owner can click that lies to them. Left in
  place; removing it is a `canvas-core.js` edit with no owner-facing effect.

Guarded by `SupersededScopeTests` in `tests/studio_alpha/test_inpaint_translation.py`,
which fails if an Inpaint Sketch operation appears, if either Studio package
gains the identifier, if the shared mask behaviour is dropped, or if the ledger
row is removed or downgraded to `missing`.

In scope and unaffected: ordinary inpaint translation, Soft Inpainting,
`_clip_to_mask`, and Canvas source/mask handling.

## Deferred requirements

Work that is correctly absent TODAY because the feature it belongs to is
absent, and that becomes owed the moment that feature lands. Distinct from
"Resolved scope decisions" above, which are ruled out permanently, and from
"Unresolved decisions", which are open questions. Recording them apart from
both is the point: a deferral that looks like a ruling never gets done, and a
deferral that looks like a gap gets built too early.

### WP10 — `_clip_to_mask` after Regional and attention-couple

The Extension calls `_clip_to_mask` at FOUR sites; Studio calls its equivalent
at two. The two Studio has cover its only pixel-changing path: after the main
pass, and again after Auto Detail. The other two Extension sites
(`studio_generation.py:3047` and `:3431`) belong to the regional and
attention-couple branches, which Studio does not implement.

That is not a parity gap now. It becomes one the moment WP10 lands: any stage
that changes pixels after the mask has been applied must be followed by the
outside-mask clip, or the owner's untouched regions stop being untouched.

Requirement when WP10 is implemented: `_clip_to_mask` runs after the FINAL
pixel-changing stage of each new path, and the preservation acceptance
(`Evidence/inpaint_preservation_live.py`) is extended to cover them.

This is not theoretical. A controlled mutation on 2026-08-19
(`Evidence/clip_site_materiality.py`) bypassed ONLY the second clip call, the
one that follows Auto Detail, and left **44,222 pixels** altered outside the
owner's permitted mask region where the normal build leaves zero. A stage that
changes pixels after the mask is applied and is NOT followed by the clip does
not merely risk leakage; it produces it at that scale. Regional and
attention-couple are such stages.

Noted while reviewing `_clip_to_mask` coverage, 2026-08-19. Studio's ordering
is currently SAFER than the Extension's on Hires: the Extension clips at
`:2013` before its separate img2img Hires step and again at `:2033` only if
Auto Detail runs, so with Auto Detail off its Hires output is never re-clipped.
Studio runs Hires inside the same `process_images_inner` call, so its first
clip already lands after it. Do not "fix" Studio toward the Extension here.

### WP5 — per-image seed increment

The Extension sets `batch_seed = use_seed + img_num` inside its per-image loop
(`studio_generation.py:3141`). Studio admits one image per job, so there is
nothing to increment today and the request carries a single fixed seed.

This is the exact parallel of the per-image aspect roll, which WAS collapsed to
one roll for a whole submission and had to be repaired (`94966813`). Recorded
here so the seed is not rediscovered the same way: when WP5 admits real batches,
each expanded per-image job must carry `seed + index`, frozen before enqueue,
and execution must draw no seed of its own.

### High Precision — dtype review obligation

`run_generation` reaches High Precision capture (`studio_generation.py:2486-2539`
and the `_hp_*` helpers). It was NOT reviewed during the 2026-08-19 pass and
nothing here claims parity for it.

When it is reviewed, four dtypes must be distinguished and never conflated:

- the VAE **decode compute** dtype;
- the **captured tensor** dtype;
- the **saved sidecar** dtype;
- the **Develop processing** dtype.

A Float32 sidecar does not prove the VAE decoded in FP32, and an FP16/BF16
decode upcast after execution must not be described as genuine FP32 source
data. High Precision may truthfully claim to preserve native pre-clamp decoder
values, values outside `[0, 1]`, avoidance of immediate 8-bit quantisation, and
Float32 Develop processing after capture — those are different claims and only
the measured ones may be made.

## Current phase

```text
P0 PROGRAM — STANDALONE CONVERGENCE WITH FORGE NEO BEHAVIOR
```

Generation, result delivery and the Canvas path are proven on real payloads.
The owner workflow reaches READY through the runtime catalogue, generates,
sends the result to Canvas with exact native pixel identity, unloads with
terminal release counted exactly once, and results survive both unload and a
process restart.

What remains is the convergence program: remove the transitional profile
plumbing, make Generate own model readiness, replace native folder dialogs
with a server filesystem browser, make Docker a first-class target, unify the
generation request against real Neo registries, and implement Hires and Auto
Detail through one public job.

## Source baseline

- Distribution repository: `https://github.com/ToxicHost/sd-webui-forge-studio`
- Integrated branch: `docs/phase0-complete-baseline`
- Forge Neo upstream: `https://github.com/Haoming02/sd-webui-forge-classic.git`
- Forge Neo branch: `neo`
- Forge Neo commit: `97ff3a4024be2f0d5316f16e868e5ef822768872`
- Forge Neo commit date: `2026-07-23T14:06:00+08:00`
- Baseline tag: `neo-baseline-2026-07-23`
- `neo...upstream/neo` at Phase 0 inspection: `0 0`
- Forge Studio frontend loader/cache identity: `4.17.0`
- Studio `version.json` commit: `b316a4d87abd69837cd723403a7a41e22f97482d`
- Reviewed canonical branch fast-forward integration:
  `882c90f3fb587938b300e3d7d7fa5e9d30b1edfd`
- License baseline: AGPL-3.0 text is present in both source trees

## Current status

```text
Tier-0 standalone backend golden anchor: ACCEPTED BY COMBINED EVIDENCE.

Attempts 05 and 06 proved deterministic standalone generation through retained
Forge inference. A separate cleanup-only diagnostic proved one model
load/unload returns CUDA memory to absolute zero without entering generation.

Post-generation memory growth is deferred to a dedicated reliability milestone.
No broader parity claim is made.
```

- **Model selection and the warm-session lifecycle are now product-owned.**
  > **Superseded in part.** The warm-session ownership, lease/release and
  > terminal-release properties described below remain the contract. The
  > *profile* and *explicit load* mechanics do not: they were transitional
  > carriers and are being removed. Read this paragraph as history of how the
  > ownership guarantees were first established, not as a product requirement.
  >
  > **Removal landed in P0.3d.** `PROFILE_SELECTED` no longer exists, and
  > neither do `select()`, `clear_selection()`, `load()` or `switch(profile_id)`
  > on the manager or the service. The machine is eight states; a job that
  > names its selection goes `NO_MODEL -> LOADING` directly, and recovers a
  > `FAILED` lifecycle the same way. Every ownership property below was
  > re-proven on the collapsed machine.

  Studio starts in `NO_MODEL` and loads nothing until asked: a user selects a
  configured `ModelProfile`, requests an explicit load, runs repeated jobs against
  **one** warm session, then unloads or switches. Readiness is a nine-state
  machine -- `NO_MODEL`, `PROFILE_SELECTED`, `LOADING`, `READY`, `BUSY`,
  `UNLOADING`, `SWITCHING`, `FAILED`, `SHUTDOWN` -- with a declared transition
  table; anything undeclared is refused as `MODEL_TRANSITION_REJECTED` rather
  than allowed because nothing checked. `WarmSessionManager` is the single owner:
  jobs **lease** the session and release it warm, one active job at a time with a
  FIFO queue, and an unload or switch requested during a job is **deferred** to
  the next safe boundary rather than refused or forced. Per-job generation
  cleanup stays separate from unload by construction -- five consecutive jobs
  close nothing. A switch closes A **before** loading B (overlap counted, zero),
  and a failed B load does **not** resurrect A, because the next job would then
  silently run on a model nobody asked for. A profile's public projection has no
  field for a path, so no response can carry one; settings report
  `result_root_configured` rather than the root, and `autoload` defaults **off**.
  The composition root owns the profile repository, settings service and
  lifecycle service, and closes the warm session before the application. A fresh
  `-I -S -B` subprocess that builds a standalone app, selects a profile and shuts
  down reports torch 0, cuda 0, `backend.*` 0, `modules.*` 0, gradio 0, socket 0.
  A complete synthetic rehearsal through the canonical `/studio/*` path loaded A
  once, reused it for two jobs, withdrew a queued third, switched to B with zero
  overlap, retrieved all three opaque results after the switch, unloaded and
  returned to `NO_MODEL` -- 19 of 19 checks, all required counts met. 1119 Studio
  tests OK. No existing contract changed: `BackendAdapter`, `StudioApplication`,
  the transport allowlist and the frontend are untouched, and no route was added.
  **Phase 1 was additive and not yet user-visible** -- a real session loader
  wired to the headless backend was the one remaining piece, and it is the only
  component that would open a payload. That loader is now wired; see the next
  entry. Catalogue browsing, broad model-family support and public packaging
  remain deferred, as does the one-time warm ~2.1 GiB allocation. Evidence:
  `Evidence/studio-usable-alpha-model-lifecycle/`.
- **The real headless session loader is wired behind explicit product load, and
  the request path now depends on the lifecycle.** `forge_headless/session_loader.py`
  is the single product-owned loader: eight ordered steps -- cancellation check,
  startup globals, the three role opens in fixed `checkpoint, text_encoder, vae`
  order, engine, identity, bookkeeping, port, session -- each individually
  injectable, releasing in reverse acquisition order on any partial failure, and
  refusing a second attempt once a payload has been opened. Every heavy import is
  deferred, so importing the module and constructing a standalone application
  still reports torch 0, cuda 0, `backend.*` 0, `modules.*` 0, gradio 0, socket 0.
  **Profile selection remains model-free; explicit load is still the only payload
  boundary, and autoload still defaults off.**
  > **Superseded — this is now inverted.** "Explicit load is the only payload
  > boundary" and "a generation request can no longer open a model" describe
  > the transitional design. The product requirement is the opposite: Generate
  > owns model readiness and MUST be able to open a model, via a single
  > serialized `ensure_loaded(selection)` with one resident owner. What
  > survives is the *ownership* guarantee — a generation must never silently
  > run on a model nobody asked for — which is now satisfied by carrying the
  > selection on the job rather than by refusing the load.

  `StudioApplication.submit_generation`
  now leases the lifecycle-owned warm session before it validates a request, so a
  generation with nothing loaded is refused as `MODEL_NOT_READY` and **a
  generation request can no longer open a model**; the lease is released in a
  `finally`, and the gate is scoped to compositions that actually own a session,
  leaving every pre-existing host on its original path. The lifecycle publishes
  its session into the backend adapter on load and clears it on unload, failed
  switch and shutdown, so the application and the adapter can never hold
  different sessions. Per-job generation release and model unload remain distinct
  by construction -- three jobs, one session, three releases, zero closes.
  Synthetic A->B switching stays overlap-free against the real loader. 1177
  Studio tests OK (58 new), preflight 179 OK, Neo parity 0 0, frontend unchanged,
  no route added. **No real model file was opened and no CUDA work occurred in
  this milestone**: four loader steps -- startup globals, payload opener, engine
  builder and port factory -- deliberately have no real default and refuse rather
  than guess, so wiring them to the proven Forge primitives is the first task of
  the live milestone. **One live single-profile usable-alpha lifecycle test
  remains**, and live A->B switching stays deferred until a second owner-approved
  profile exists. Evidence: `Evidence/studio-real-session-loader/`.
- **The loader's four refusing defaults are bound, so an explicit product load
  now needs no test-only injection.** `startup_globals`, `payload_opener`,
  `engine_builder` and `port_factory` are wired to the proven primitives behind
  deferred imports: `StudioStartupGlobals` composes the Gradio-free options, the
  state bridge and the compatibility context that supplies `prompt_styles`,
  `device` and `total_tqdm`; `ControlledPayloadOpener` validates every role
  **before** initializing the device, then spends the authorization at the first
  *observed* open; `build_forge_engine` calls Forge's own `forge_loader` and
  restores the `safetensors` patch in a `finally`. Three of the four needed no
  extraction -- the primitives were already product-neutral and the diagnostics
  merely called them. The fourth did: the smoke harness's generation port is
  single-use by design, so a product-neutral equivalent now survives repeated
  jobs, owns its own per-job `release_generation_references`, and carries no
  diagnostic recorder. Engine release moved to session close, making the per-job
  versus per-session split structural. Engine publication keeps the order two
  live attempts established -- `modules.processing` is imported **before**
  `shared.sd_model` is assigned, because `sd_models` and `processing` form a
  mutual cycle that resolves only that way -- and publication shares one restore
  handle with the reload bookkeeping, so a later failure cannot leave a released
  engine as the retained code's current model. **Bound defaults are not
  readiness**: a Studio with no controlled access reports
  `load_configuration_required` and refuses a load with the distinct
  `MODEL_LOAD_NOT_CONFIGURED` rather than the generic failure, so a caller
  cannot retry the one thing that can never succeed. Selection, status and
  capability reads still work and still open nothing. 1259 Studio tests OK (82
  new), preflight 179 OK, Neo parity 0 0, frontend unchanged, no route added. A
  fresh isolated subprocess that imports both binding modules, builds a headless
  application, selects a profile and shuts down still reports torch 0, cuda 0,
  `backend.*` 0, `modules.*` 0, gradio 0, safetensors 0, socket 0. **No real
  model file was opened and no CUDA work occurred in this milestone**: the bound
  defaults are proven against an intercepted graph, not against a model, so
  their live behaviour remains the one unobserved thing. **One live
  single-profile usable-alpha lifecycle test is now genuinely ready**; live A->B
  switching and visible frontend controls remain deferred. Evidence:
  `Evidence/studio-real-loader-default-bindings/`.
- **The first live attempt stopped before payload access, and the three
  integration defects its rehearsal found are fixed.** The authorization was
  **not consumed** -- no payload was opened, no CUDA initialized, no image
  expected or produced -- because the required non-live rehearsal failed, which
  is what a rehearsal is for. All three defects lived *between* components that
  were each individually correct and tested: the lifecycle published the
  `LoadedStudioSession` wrapper where the backend adapter needs the inner
  `HeadlessGenerationSession`, so every generation failed on the first job; a job
  submitted while another was running was recorded as queued and **ran anyway**,
  putting two generations inside one warm engine; and a missing result root
  failed the load at `port_built`, step 7 of 8, after every payload was already
  open. **Adapter publication now uses the inner session**, unwrapped by surface
  rather than by class, while the lifecycle keeps the wrapper so unload still
  releases the engine, port, startup globals and bookkeeping. **Queue state now
  gates execution**: `begin_job` waits for its turn on the condition `release`
  already notified instead of recording a position nobody honoured, so exactly
  one generation is active, promotion is FIFO, and a shutdown drains what is
  running rather than starting what is not. **A queued cancellation never
  reaches the generation port** and reports the distinct `MODEL_JOB_CANCELLED`.
  **Result-root validation happens before payload access**, refusing with
  `RESULT_ROOT_NOT_CONFIGURED` at step 0 rather than resolving a default, since
  every silent fallback available -- system temp, an environment variable, a
  repository-relative directory -- writes somewhere the owner never named. A new
  50-test seam suite drives the real composition, loader, adapter and queue
  together, including one complete three-job rehearsal: 3 submitted, 2 reaching
  the port, 2 completed, 1 cancelled while queued, max concurrent 1, 2 releases,
  both results byte-identical before and after unload, and a NO_MODEL refusal
  that opens nothing. 1309 Studio tests OK (50 new), preflight 179 OK, Neo parity
  0 0, frontend unchanged, and **no existing assertion changed** -- the defects
  were absences, not wrong assertions. **No live model work occurred.** One live
  single-profile lifecycle test still remains, and **a fresh authorization must
  be bound to the new integrated HEAD**: the previous one was bound to a HEAD
  this milestone supersedes. Evidence:
  `Evidence/studio-usable-alpha-seam-fixes/` and
  `Evidence/studio-live-single-profile-usable-alpha/`.
- **The second live attempt also stopped before payload access, and the
  live-path closure sweep then audited the entire call graph at once.** Both
  live attempts ended with the authorization unconsumed, no payload opened and
  no image produced. The second failed inside the load on a missing `sys.path`
  entry: `backend.loader` reaches vendored packages (`gguf`, then
  `huggingface_guess`) that exist only under `modules_forge/packages`, which
  the proven diagnostic put on `sys.path` and the product startup did not.
  **The product now owns its runtime environment**: `StudioStartupGlobals`
  installs the packages directory and the repository root (load-scoped,
  deduplicated, removed exactly once on every exit) and **normalizes
  `sys.argv`** -- `modules/shared_cmd_options.py` parses argv strictly at
  import, so a product process launched with its own arguments would otherwise
  die at the first `modules.shared` import. **Watch protection now begins
  before the terminal imports**: the `safetensors.safe_open` patch is acquired
  before `backend.loader` is imported, the import sits inside the protected
  region, and the loader's unwind owns the restore, so an import failure or a
  cancellation after the payload step can no longer leave the patch installed.
  **The stub blindness that hid the failure is closed**: a probe in the suite
  runs the REAL import chain -- backend.loader, anima, huggingface_guess --
  through the product's own startup with the GPU hidden, stops at a sentinel
  on the first payload open (which fired exactly once and consumed the
  authorization object), then continues the whole lifecycle with actual
  product classes over the really-imported modules, including the real
  `shared.sd_model` property publication and a full three-job rehearsal over
  the real transport. Only two things on the live path have now never
  executed for real: the `forge_loader` body against real payloads, and the
  sampler. 1358 Studio tests OK (49 new), preflight 179 OK, Neo parity 0 0,
  frontend unchanged, no existing assertion changed. **No live model work
  occurred**, and **a fresh live authorization must bind to the new
  integrated HEAD**. Evidence: `Evidence/studio-live-path-closure-sweep/` and
  `Evidence/studio-live-single-profile-usable-alpha-v2/`.
- **The final live test ran, and the functional usable-alpha is accepted.** One
  authorized run at the closure-sweep baseline loaded the real Anima engine
  through the product path in 12.0 s, completed two ordinary jobs 12/12 with
  first completed step 1 on one warm session, queued a third behind active work
  and cancelled it with zero port access, and retrieved both results before and
  after explicit unload **byte-identically -- and byte-identical to their golden
  anchors** (job 1 to the anchor shared since the first controlled image, job 2
  to the three-cycle neon-alley render): bit-exact determinism across execution
  architectures. Unload restored the exact `FakeInitialModel` sentinel, the
  registry, `safe_open`, and the managed paths; every owned weakref died; all
  twelve Gradio counters stayed zero; 35.6 s of the 900 s budget. The recorded
  position is **FUNCTIONAL_USABLE_ALPHA_ACCEPTED /
  MEMORY_OBSERVABILITY_INCOMPLETE**: the run's numeric memory thresholds are
  NOT claimed -- the harness read the wrong `VramSample` attribute names and
  every number died with the process (the 14 GiB peak held by allocator
  enforcement). Evidence: `Evidence/studio-live-final-usable-alpha/`, including
  both generated PNGs.
- **Internal Alpha Phase 1 delivered the owner-usable product surface and
  closed all three live-run gaps together.** Telemetry now flows through one
  fail-closed schema adapter (`forge_headless/memory_report.py`) that reads the
  sample ATTRIBUTES and emits the wire names, with the exact live regression
  pinned, plus one central classifier whose verdict can never be
  `MEMORY_ACCEPTED` with missing or invalid telemetry.
  `StudioStartupGlobals` restores the **exact original `sys.argv` object**, by
  identity and values, on success and on both failure sides of the first
  `modules.*` import. **Queued lifecycle jobs are cancellable over the
  canonical HTTP API**: `JobCoordinator` (`forge_studio/jobs.py`) makes the
  lifecycle token the one public job id -- minted before any lease or backend
  work, stable through queued/running/completed/failed/cancelled;
  `POST /api/generate` returns 202 immediately on lifecycle hosts;
  `POST /api/jobs/{id}/cancel` covers queued and backend jobs with one verb; a
  queued cancel never reaches the adapter, session, or port; seven cancellation
  races are deterministic; hosts without a lifecycle keep the old blocking
  semantics byte-for-byte. **The Studio frontend gained its first model
  controls** -- a Studio-authored panel (`studio-model-controls.js`) with
  profile selection, explicit load/unload, truthful lifecycle state, job states
  with queued-cancel, and opaque-handle result thumbnails/downloads that
  survive unload -- added through a one-line `optionalScripts` delta, with the
  s07 provenance pins updated deliberately and the milestone named at the
  changed hash. **The internal-alpha launcher** (`launch_studio.py --config`)
  validates a bounded contract with plain-sentence refusals (autoload refused,
  loopback only, workspace-contained results and logs, template placeholders
  refused by role name), starts in NO_MODEL with no model or CUDA work, and
  grants explicit access per load through
  `forge_headless/explicit_access.py` -- placed there because the
  no-public-bypass guard rightly forbids the HTTP-reachable layer from naming
  the authorization machinery, a mistake the guard itself caught on the first
  branch validation. Runbook, config template, AGPL/asset notices, limitations
  and rollback shipped under `docs/studio/internal-alpha/`. A complete loopback
  rehearsal drove the whole flow through the real frontend assets, routes and
  server with every required count held. 1409 Studio tests OK (51 new),
  preflight 179 OK, Neo parity 0 0. **No real model or CUDA work occurred.**
  Next: **one internal-alpha live UI smoke** bound to the new integrated HEAD,
  which finally captures the numeric memory telemetry through the fixed
  fail-closed path. Evidence:
  `Evidence/studio-internal-alpha-frontend-packaging/`.
- **The first internal-alpha live UI smoke stopped before payload access, and
  the UI gate corrections closed all four defects it found together.** The
  smoke's authorization was **not consumed**: no payload opened, no CUDA, no
  model, no image, repository unchanged. Its pre-payload gates found four
  bounded defects, fixed in this milestone as one unit. **D1**: the shipped
  Generate action bypassed the coordinator; the Model panel now owns the
  transport (`window.StudioModelControls.submitGenerate`), `doGenerate`
  routes lifecycle hosts through `POST /api/generate` before any legacy
  work, one public id spans QUEUED through terminal state, queued
  cancellation is reachable from the shipped UI on that same id, and the
  legacy blocking route survives only for source-compatible hosts with no
  silent fallback (source-pinned). **D2**: the font-CDN stylesheet is gone;
  every frontend asset is local (s07 pins now assert zero external
  references). **D3**: the launcher announces `STUDIO_READY host= port=`
  flushed, exactly once, only after a successful bind; a piped consumer
  needs no `python -u`; a refused configuration exits 2 with no false ready
  line. **D4**: an `onboarding` configuration mode makes first run
  deterministic -- owner mode keeps the once-only overlays, smoke mode
  announces `?onboarding=off` and both nonessential overlays stay away
  per-visit with nothing written. A fresh-profile browser rehearsal over
  CDP drove the whole section-8 flow through the real DOM against a
  synthetic world: three Generate clicks, Job 2 held in the port, Job 3
  queued and cancelled through the panel button under the same public id
  with zero port calls, both opaque results byte-identical before and
  after explicit unload, a truthful NO_MODEL refusal, zero external
  requests, zero CSP violations, clean privacy scans, cooperative
  shutdown. 1435 Studio tests OK (26 new), preflight 179 OK, Neo parity
  0 0. **No real model or CUDA work occurred.** Next: **a fresh live UI
  authorization must bind to the new integrated HEAD.** Evidence:
  `Evidence/studio-internal-alpha-live-ui-smoke/` (the stopped smoke) and
  `Evidence/studio-internal-alpha-ui-gate-fixes/`.
- **The internal-alpha live UI smoke ran end to end on the real model, and
  the product passed everything it was asked.** One authorized load through
  the Model panel reached READY on a real **Anima** engine in 15.0 s; two
  jobs submitted through the real Generate button completed **12/12** on one
  warm session with first observed step 1, and **both PNGs are byte-identical
  to their golden anchors** -- deterministic generation reproduced through the
  shipped UI. A third job queued behind BUSY work, rendered QUEUED with its
  own Cancel button, and was cancelled through the canonical route under the
  **same public id** with **zero** port calls and no backend submission. Both
  results stayed downloadable through opaque handles across an explicit UI
  unload, byte-identical; unload restored the `FakeInitialModel` sentinel, the
  **exact original `sys.argv` object**, `safe_open`, and the managed path, and
  every owned weakref died; the NO_MODEL refusal rendered truthfully; twelve
  Gradio counters, console errors, CSP violations, external requests, and
  path-leak scans were all zero; launcher, server, and browser stopped
  cooperatively in 47.5 s of the 900 s budget with the authorization consumed
  exactly once. **The corrected telemetry captured every sample as a valid
  integer** (warm growth -4,096 bytes; peak 6.32 GiB under the 14 GiB
  ceiling). The recorded position is **FUNCTIONAL_INTERNAL_ALPHA_LIVE_UI_
  ACCEPTED / MEMORY_OBSERVABILITY_VERIFIER_FLAWED**: the post-unload
  thresholds and the central verdict are NOT claimed, because the harness held
  strong references across the product's cleanup window and measured its own
  grip; the trailing sample (allocated 9,568,256 once it let go) shows the
  product releasing completely. Evidence:
  `Evidence/studio-internal-alpha-live-ui-smoke-v2/`, including both PNGs.
- **The usability closure gave results their own workspace, fixed the
  verifier, and made UNLOADING visible.** Completed results now live in a
  **dedicated dockable, resizable Results panel** -- thumbnail grid, selected
  preview with dimensions and MIME, Open/Download through opaque handles under
  safe public names (`studio-result-000001.png`), completed/failed/cancelled
  filtering, automatic arrival, stable expired/undecodable states, and
  durability across unload -- while the Model panel rescopes to lifecycle
  controls plus a **compact** job summary whose completed rows link into
  Results and whose failed rows carry a stable scalar code with recovery
  guidance. No second job state machine: both panels read the one public job
  id. **`forge_headless/unload_verification.py` now owns the reference
  discipline** the V2 run got wrong -- capture scalars and weakrefs without
  retaining anything, drop every verifier reference BEFORE the unload, wait
  for the observed objects to die, and only then sample registry and globals
  -- with a regression proof pinning both directions (held reference ->
  `OWNERSHIP_STATE_INCONSISTENT`; dropped -> `MEMORY_ACCEPTED`) and a
  fail-closed unreadable registry. **The Unload click paints UNLOADING
  synchronously**, disables both lifecycle buttons, and refuses a duplicate
  request (one POST after a deliberate double-click). A fresh-profile browser
  rehearsal drove the whole flow through the real DOM with six screenshots:
  both panels distinct, two results clearly visible together, cancelled Job 3
  with no thumbnail, results surviving unload, zero external requests, zero
  CSP violations, zero unexpected console errors, `real_payload_opens=0`,
  `cuda=0`, cooperative shutdown. 1460 Studio tests OK (25 new), preflight 179
  OK, Neo parity 0 0. **No real model or CUDA work occurred.** Next: **the
  final owner-trial live smoke, bound to the new integrated HEAD.** Evidence:
  `Evidence/studio-internal-alpha-usability-closure/`.
- **The owner-trial live smoke passed functionally on the first attempt, and
  found exactly one real product defect.** One authorized load reached READY on
  a real **Anima** engine in 8.4 s; two jobs submitted through the frontend
  completed **12/12** on one warm session with first observed step 1, and both
  PNGs were **byte-identical to the golden anchors** for the third independent
  time. A third job queued behind BUSY work and was cancelled through its own
  UI control on the canonical route under the **same public id**, with zero
  port calls and no backend submission. Both results appeared together in the
  **dedicated Results workspace**, were opened and downloaded through opaque
  handles under safe generated names, and remained byte-identical after an
  explicit UI unload; the Model panel carried no result surface at all.
  UNLOADING rendered synchronously; the verifier dropped every strong
  reference BEFORE the click and the required event order matched exactly; the
  `FakeInitialModel` sentinel, registry 0, exact original `sys.argv` object,
  `safe_open`, and managed path all restored, and **all owned weakrefs died
  including the engine**. Every memory sample was a valid integer (warm growth
  -4,096 bytes; peak 6.33 GiB under the ceiling; **post-unload allocated
  9,568,256 <= 11,206,656**). Twelve Gradio counters, console, CSP, external
  requests, and privacy scans were all zero; everything stopped cooperatively
  in 27.7 s of the 900 s budget with the authorization consumed exactly once.
  The one failure: **post-unload reserved 6,111,100,928 against a 26,214,400
  ceiling**, so the central verdict was `MEMORY_THRESHOLD_EXCEEDED`. Recorded
  position: **FUNCTIONAL_OWNER_TRIAL_ACCEPTED /
  TERMINAL_RESERVED_VRAM_RELEASE_INCOMPLETE**. Evidence:
  `Evidence/studio-owner-trial-live-smoke/`, including both PNGs and six
  screenshots.
- **The terminal VRAM release closure fixed that one defect.** The owner trial
  proved the memory was genuinely released — allocated fell to 9.6 MB with
  every owned weakref dead and the registry at zero — but **all twelve cache
  clears in that run happened BEFORE the final reference release**, and Studio
  performed none after it, so the caching allocator never returned its ~6.1 GB
  reserve. `LoadedStudioSession.close()` now ends with **exactly one terminal
  allocator release**, after the reverse-order closers and after `session`,
  `engine`, and `port` are dropped — still inside the lifecycle's UNLOADING
  window, before `NO_MODEL` is published, so no state-machine boundary had to
  move. Explicit unload, switch, and shutdown all reach it through the ONE
  existing close seam; a duplicate close reaches it not at all; a failed load
  clears once on rollback only when it had reached the engine step.
  `forge_headless/terminal_release.py` supplies **order, not a second
  primitive**: it reads `sys.modules` rather than importing (a process that
  never loaded cannot gain torch by calling it), no-ops with a stable scalar
  reason on CPU-only or never-initialized hosts, delegates the clear to the one
  sanctioned site (`controlled_device.release_cuda_cache`), and never raises —
  a clear failure leaves ownership released and the state truthfully
  **NO_MODEL with a cleanup warning**. Terminal clears are counted separately
  from every other clear, and **no per-job clear was added**. A synthetic
  allocator regression asserts the exact ten-event order and four negative
  orders, including a missing clear that reproduces the owner trial's
  `MEMORY_THRESHOLD_EXCEEDED` with `["post_unload_reserved"]`; a browser
  rehearsal drove the real launcher, frontend, and lifecycle to reserved
  6,000,000,000 -> 0 with one clear, before NO_MODEL, all counts met.
  **No existing assertion needed changing** — the Tier-0 boundary guard, which
  pins exactly one Studio cache-clear site, caught and rejected a first draft
  that called Forge's helper directly. 1488 Studio tests OK (28 new),
  preflight 179 OK, Neo parity 0 0. **No real model or CUDA work occurred.**
  Next: **one focused live terminal-VRAM confirmation bound to the new
  integrated HEAD.** Evidence: `Evidence/studio-terminal-vram-release/`.
- **The floating Model and Results boxes were rejected, and the product
  integrated into Studio's own native surfaces.** Both floating panels are
  gone -- `studio-results-panel.js` is deleted, and `studio-model-controls.js`
  survives only as an ADAPTER that creates no element, positions nothing
  fixed, and appends nothing to the body. **The Canvas Strip owns model and
  session parameters**: a native `data-block="session"` group beside the other
  generation parameters carrying the profile selector, lifecycle chip,
  Load/Unload, an active/queued summary, and the queue list behind the group's
  own disclosure with per-row Cancel -- built from existing parameter classes,
  so `app.css` and `app.js` stay byte-identical. **The Session Strip owns
  completed results**: they are appended to `State.sessionEntries`, the one
  registry Studio already renders, under safe generated names
  (`studio-result-000001`) through opaque handles, newest first, surviving
  unload; cancelled and failed jobs never receive a thumbnail, and delivery is
  idempotent. **The Canvas owns the opened image** through the existing
  double-click -> Gallery ephemeral view -> Send to Canvas path; the adapter
  contains no viewer of its own. One public job id links the queue row, the
  result entry, and its display name -- no second lifecycle state machine and
  no parallel result store (asserted as a whole by an ownership-map test). A
  browser rehearsal met every required count: 3 jobs submitted / 2 reaching the
  port / 2 completed / 1 cancelled, max concurrency 1, 2 publications, 2
  per-job releases, 1 unload, **1 terminal cache clear**, 2 Session Strip
  results, **2 Canvas opens before and 2 after unload**, 0 external requests, 0
  CSP violations, 0 console errors, `real_payload_opens=0`, `real_cuda=0`,
  cooperative shutdown. 1508 Studio tests OK (22 new; only the two classes that
  directly encoded the floating boxes were rewritten), preflight 179 OK, Neo
  parity 0 0. **No real model or CUDA work occurred.** One visual item is NOT
  met and is reported rather than claimed: the Session Strip renders its
  thumbnails but presents as a 22 px rail in the rehearsal environment, so the
  two results are not on screen there (they are visible together in the deck's
  output row); no strip markup, CSS, or layout code was touched by this
  milestone. Next: **one final native-UI owner trial plus the live
  terminal-VRAM confirmation, bound to the new integrated HEAD.** Evidence:
  `Evidence/studio-native-canvas-session-integration/`.
- **The reported Session Strip width defect did not exist, and the audit that
  established that also found the one real defect.** A CDP viewport sweep
  measured the strip at **192 px with a 191 px scroll container at every
  viewport above 1600 px**, and at 22 px with a 0 px scroll container at or
  below it — because `app.css` carries a deliberate
  `@media (max-width: 1600px)` block whose own comment reads *"below 1600px
  the strip auto-collapses"*. The previous milestone's rehearsal ran at
  `--window-size=1600,950`, so it sat at that breakpoint; **its attribution to
  a broken layout rule was wrong**, and the audit says so plainly. Every
  symptom it listed — 22 px width, 0 px scroll, `collapsed` absent, "toggle
  adds collapsed rather than expanding" — follows from that one fact. What WAS
  real: inside the responsive block the strip still rendered its collapse
  control as a rotated **expand** affordance, though clicking it only toggles
  the class that same block overrides, so the control claimed an action it
  could never perform. **One rule now hides that control where the layout owns
  the rail**; above the breakpoint the toggle is untouched. No inline width, no
  `!important`, no script-side mutation — none was needed. A 1920×1080
  rehearsal proved the full contract: expanded 192/191 px empty, one result
  visible, **two results visible together**, collapse to the 22 px rail with
  its toggle kept, **re-expand restoring both thumbnails**, both fetched, both
  opened in Canvas before and after unload, one terminal cache clear, results
  surviving unload, zero external/CSP/console, `real_payload_opens=0`,
  `real_cuda=0`, cooperative shutdown. 1525 Studio tests OK (17 new), preflight
  179 OK, Neo parity 0 0. `app.css` changed for the first time in this line of
  work — one rule, pins named. **No real model or CUDA work occurred.** Next:
  **one final native-UI owner trial plus the live terminal-VRAM confirmation,
  bound to the new integrated HEAD.** Evidence:
  `Evidence/studio-session-strip-width-closure/`.
- **The conditioning-cache owner was confirmed live, and the tracked Studio path
  now calls the shipped per-job release seam.** A three-cycle live diagnostic ran
  three canonical `/studio/*` jobs through one warm model session and one loopback
  server: 12/12 steps and decode every job, first completed step index **1** every
  job, three valid 768x768 PNGs, six retrievals all HTTP 200 and byte-identical
  across cleanup, jobs 1 and 2 byte-identical to the golden anchor and job 3
  different, all per-job generation weakrefs dead, registry at pre-load count,
  twelve Gradio counters zero, and **no cumulative growth** -- post-cleanup
  baselines moved by -4,096 and 0 bytes against a 1,048,576-byte tolerance. At S11
  of every job the shipped `release_generation_references` seam cleared
  `cached_c`/`cached_uc` from 3/3 to 0/0 and freed **4,784,640 bytes**, identical
  on all three jobs including the differing prompt, against **4,784,128**
  predicted from source alone -- a 512-byte difference. That closed the question:
  the tracked runner had simply never called the seam, and dropping a processing
  instance does not clear a class attribute. `_cleanup` now calls it exactly once
  per job, from one call site, **before** the port release that drops the object
  the seam needs; the caches are read for counts only and never cleared directly;
  no CUDA clear was added; result durability and warm-session reuse are proven
  intact. `OwnershipSampler` now refuses to sample owned weakrefs until
  `port_engine_cleared`, `session_closed`, `gateway_released` and
  `diagnostic_locals_released` are all marked, returning
  `OWNERSHIP_NOT_SAMPLEABLE` rather than a clean-looking result -- the live run's
  one surviving `engine` weakref is reproduced synthetically as a bound
  diagnostic local, though it is **not claimed** that this was that run's holder.
  1013 Studio tests OK. Deferred and untouched: the one-time warm
  **2,105,710,592-byte** allocation established by the first generation -- stable
  across jobs, not cumulative, not the conditioning cache, not yet attributed, and
  a reliability question rather than an architecture one. Evidence:
  `Evidence/studio-three-cycle-live-reliability/`,
  `Evidence/studio-per-job-generation-release/`.
- **The live Studio service path is functionally proven; what remains is
  generation-only CUDA retention.** One authorized rerun through the tracked
  runner sent a canonical `/studio/*` request over loopback to the real
  `HeadlessBackendAdapter`, session, gateway and tracked `LiveGenerationPort`,
  completed 12/12 sampler steps and decode, and published one 768x768 PNG
  **byte-identical to the golden anchor**. The same opaque handle returned
  identical bytes, digest, MIME and dimensions **before and after** port/session
  cleanup. All eight owned weakrefs died, the Forge registry returned to its
  pre-load count, `shared.sd_model` and `model_data.sd_model` returned to
  no-model, identity detached, reload bookkeeping restored, all twelve Gradio
  counters stayed zero, the 14 GiB ceiling held at a 5.81 GB peak, and the
  loopback server started once and stopped once. Acceptance was withheld for one
  reason: final CUDA was **14,352,384 allocated / 25,165,824 reserved** against a
  prior residual of 9,568,256 / 23,068,672 -- `NEW_OR_GREATER_RESIDUAL`. Note the
  wording: `memory_allocated()` means live allocator-owned storage exists, so the
  accurate claim is that **none of the currently tracked model, session or
  harness owners retained it**, not that nothing holds it. Two reporting defects
  were corrected afterwards: the classifier read `allocated`/`reserved` while the
  schema emits `allocated_bytes`/`reserved_bytes` (it saw `-1` and reached the
  right verdict by fallthrough), and `first_completed_sampler_step` read `11`
  because it sampled the counter once after the loop -- retained Forge writes a
  **zero-based** index at `modules/sd_samplers_common.py:431` and its own
  one-based translation on the next line. Both are fixed, with the `11` and the
  `-1` reproduced as regression tests. A stage recorder (S0-S15) and a
  liveness-driven model now distinguish a fixed reusable plateau from cumulative
  per-job growth without a device. The leading candidate for the delta is
  inventoried but **not claimed**: the class-level conditioning caches
  (`modules/processing.py:196-197`), which `close()` skips while
  `persistent_cond_cache` defaults true, and which the shipped
  `release_generation_references` clears -- a call the probe makes and the
  tracked runner does not. 961 Studio tests OK. **This is a reliability blocker,
  not an application-architecture blocker, and a three-cycle live reliability run
  needs fresh owner authorization.** Evidence:
  `Evidence/studio-generation-residual-attribution/`,
  `Evidence/studio-live-frontend-backend-smoke-rerun/`.
- **The full live Studio service path generated and delivered a deterministic
  image; acceptance failed on the throwaway harness, which is now replaced.** One
  canonical `/studio/*` request over loopback reached `StudioPresentation`,
  `StudioApplication`, the real `HeadlessBackendAdapter` and a real Tier-0
  generation port, completed 12/12 sampler steps and decode, and returned one
  768x768 PNG through `ResultRegistry` -- **byte-identical to the golden anchor**,
  with all twelve Gradio counters zero and no mock fallback. It failed acceptance
  because the scratch harness stored `self._engine` and never cleared it, leaving
  the engine, VAE component, VAE module and VAE patcher reachable, one Forge
  registry entry, and 264,719,360 bytes allocated. **No shipped Studio component
  was an owner** -- the session holds only a `ResidentModel` description. A
  tracked runner at `scripts/headless/studio_service_smoke.py` now owns and
  releases every diagnostic reference on both the success and the exception path,
  is single-use and refuses a second run, measures post-cleanup result durability **before** stopping the
  application (`StudioApplication.shutdown` clears the registry, so ordering is
  load-bearing), derives lifecycle facts from Studio job progress plus explicit
  port callbacks rather than a second progress object, and keeps loader /
  retained / Studio-terminal cache clears as separate counters. The canonical
  runner forbids loopback sockets, so every server-touching scenario runs in a
  subprocess -- the guard was respected, not weakened. 890 Studio tests OK. **The
  live smoke has NOT passed; a fresh owner authorization is required to rerun
  it.** Evidence: `Evidence/studio-live-frontend-backend-smoke/`,
  `Evidence/studio-live-smoke-harness-cleanup/`.
- **The real headless backend is now a selectable Studio backend.** Backend
  selection is one closed selector at the composition root -- `build_standalone(
  backend_kind="mock" | "headless")`. An unknown kind fails closed with
  `BACKEND_NOT_WIRED`, nothing falls back from headless to mock, nothing is read
  from an environment side effect, and both imports are deferred so selecting
  the mock imports no `forge_headless` module. Generation on
  `HeadlessBackendAdapter` is **opt-in**: with no session injected every
  generation-side method refuses exactly as Phase 2A did, so the plumbing suite
  that pins those refusals is untouched. `forge_headless/studio_generation.py`
  translates between Studio's 8-field request and 5-state job and the headless
  18-field request and 9-state lifecycle -- all eight Studio fields carried
  verbatim, seven backend-defaulted fields reported rather than invented, four
  Tier-0 flags pinned off, and a random seed **refused** rather than substituted.
  The nine headless stages project onto five Studio states with the stage carried
  in the event message. Ten stable Studio error codes; records are scalar-only
  and retain no exception, traceback, or frame. Result publication stays Studio's
  through `ResultRegistry`, and a result outlives adapter cleanup. Both the
  canonical `/studio/*` and compatibility `/sdapi/v1/*` vocabularies reach the
  same application and the same adapter. The **real** adapter is exercised with
  injected non-live doubles -- port, executor, clock, identifiers, result writer,
  cleanup hook -- with no model, CUDA, image, or server. 849 Studio tests OK.
  **One live frontend-to-backend smoke test remains required, and needs a fresh
  authorization.** Evidence:
  `Evidence/studio-headless-backend-integration/`.
- **Product integration has begun: a dual-mode composition root now assembles
  Studio.** `forge_studio/composition.py` is the seam that was missing --
  `presentation._create_mock_presentation` and `run_socket_free_demo` each built
  `StudioApplication(MockBackend(...))` inline, and `backend_selection` returned
  a backend *name* with no factory. Both call sites now build through
  `build_standalone`, and `StudioHost` has two implementations,
  `StandaloneHost` and `ExtensionHost`, feeding **one** `StudioApplication`; an
  AST test fails if either host defines a job method, so business logic cannot
  fork. `ExtensionHost` takes host facilities by **injection**, never by import,
  which is what keeps `test_import_boundaries` unweakened. Injection points:
  clock, identifiers, executor, result root, environ. Standalone import is
  measured from a fresh isolated subprocess to pull in zero gradio, zero
  `modules`/`modules_forge`/`webui`, zero torch, zero `cuda`, and no
  `socketserver`. **Correction to a long-standing assumption: Studio is not
  registered as a Neo extension anywhere in this repository** -- verified five
  ways, including zero callers of `on_ui_tabs` and zero matches for "studio" in
  `webui.py`/`launch.py`. The extension arrangement in the docs is the upstream
  reference snapshot, outside this repo. Nothing was removed; `ExtensionHost` is
  the forward seam for the planned Stage A. Two latent defects were fixed on the
  way: `ResultRegistry` fails closed when its root does not exist, so a clean
  checkout would have failed its first result, and the wait-to-terminal policy
  that lived inside the canonical-frontend adapter is now shared. 773 Studio
  tests OK. **No model, CUDA, image, or server was involved.** Evidence:
  `Evidence/studio-standalone-product-bootstrap/`.

- **Forge's loaded-model registry is not the residual owner, and no teardown was
  integrated.** `backend/memory_management.py:424` `current_loaded_models` holds
  `LoadedModel` entries that reference their `ModelPatcher` and its module
  **weakly** (`:446`, `:490`); their only strong state is a device, a bool, and
  two `weakref.finalize` handles, and the finalizer at `:491` prunes the registry
  itself (`:749-757`). Popping an entry frees nothing. Two independent stop
  conditions fired: the candidate is disproven with no concrete owner identified,
  and `unload_all_models()` — which takes no parameters, over a `free_memory`
  with no cache-control parameter and a `soft_empty_cache` whose `force`
  argument is never read — would produce **two** `torch.cuda.empty_cache()` calls
  in the normal case and three when any entry is dead. Cache-clear ownership
  therefore remains explicit and **singular**: exactly one intentional clear,
  Studio's own, at `forge_headless/controlled_device.py:151`. The registration
  trace is now proven: nothing registers at load, and three entries appear lazily
  at first use — text encoder `backend/diffusion_engine/anima.py:43`, UNet
  `backend/sampling/sampling_function.py:381`, VAE `backend/patcher/vae.py:211`.
  A 40-test guard suite pins the contract so the rejection cannot go stale.
  718 Studio tests OK. **No runtime source changed. No attempt 07 has occurred;
  a cleanup-only live diagnostic is requested before any further image
  authorization.** Evidence:
  `Evidence/studio-tier0-forge-registry-teardown/`.
- **The first real standalone Studio-backend image exists, and reproduces
  byte-for-byte.** Attempts 05 and 06 each produced the same 768x768 RGB PNG
  (seed 123456789, 12/12 sampler steps, one VAE decode, sha256
  `6c23288148…0607e135`, 754,452 bytes) through retained Forge/Neo code,
  published via Studio's owned `ResultRegistry` with an opaque handle, a
  byte-identical round-trip, and post-release durability, with resolved sampler
  and scheduler captured from the sampler object, all twelve Gradio counters
  zero, and peak VRAM 6.32 GiB inside the 14 GiB ceiling. Every Studio-owned
  generation reference was released and proven released.
  **Formal golden-anchor acceptance was withheld** on one criterion: teardown
  retained 9,568,256 allocated bytes. Attempt 06 released everything attempt 05
  did **plus** the instance accumulators `latents_after_sampling`,
  `pixels_after_sampling`, `extra_result_images`, `modified_noise`, and the whole
  `Processed` object — and the figure moved by **exactly zero**. The earlier
  attribution to `p.latents_after_sampling` was therefore correct as a
  description of a real unreleased reference and **wrong about the bytes**; the
  4 MiB step from attempt 04 to 05 shows the measurement is sensitive enough to
  have seen it. **Final golden-anchor acceptance is not claimed.** Evidence:
  `Evidence/studio-first-tier0-image/`,
  `Evidence/studio-tier0-post-decode-release/`.
- The fourth authorized Tier-0 attempt was consumed and entered the real
  denoiser**. It passed the direct-load reload fast path (returned the loaded
  engine, `reloaded=False`, zero checkpoint/catalogue/loader/second-payload
  calls), completed prompt setup, conditioning, and initial noise, and reached
  `CFGDenoiser.forward` — 1.87 GiB more peak VRAM than any prior attempt. It
  stopped at `modules/script_callbacks.py:192`, where Forge reads
  `getattr(shared.opts, "prioritized_callbacks_" + category, [])`: the option is
  generated at runtime by `modules/shared_items.py:154` and Studio raised a
  non-`AttributeError`, defeating a fallback Forge had already written. This was
  the first blocker caused by Studio's boundary being *stricter* than retained
  code expects. `HeadlessOptionMissing(HeadlessError, AttributeError)` now makes
  `getattr(opts, x, d) == d` and `hasattr` false while `opts[x]`/`require(x)`
  stay strict; the fallback is proven for all 21 callback categories and
  `modules/script_callbacks.py` is untouched. The 13.1 MiB residual was
  attributed from source — `p.close()` never runs on this path and
  `persistent_cond_cache` defaults True, leaving the **class-level** conditioning
  caches alive — and is now released by
  `forge_headless/failure_cleanup.py` before VRAM is measured, with no extra
  cache clears. 643 Studio tests OK. **No fifth live attempt has occurred; a
  fresh authorization is required.** Evidence:
  `Evidence/studio-tier0-option-fallback-cleanup/`.
- The third authorized Tier-0 attempt was consumed and reached prompt
  setup, confirming the identity contract and all three startup compatibility
  objects against the actual Anima session, with owned cleanup 0/0/0 and all 12
  Gradio counters serialized and zero. It stopped before conditioning at
  `modules/processing.py:945`: `process_images_inner` calls
  `sd_models.forge_model_reload()` itself, so entering at the inner loop
  bypasses only the outer wrapper's call at `:785`, not reload. Every prior
  document claimed otherwise. `forge_headless/direct_load_reload.py` now
  installs the bookkeeping Forge's own early return checks
  (`forge_hash == str(forge_loading_parameters)`), so the call returns the
  loaded engine with `reloaded=False` -- proven against the real function with
  checkpoint selection, catalogue lookup, and the loader doubled to raise, zero
  calls each. No catalogue entry is fabricated and nothing in `modules/`
  changed; a mismatch test proves native reload logic is still entered when the
  bookkeeping disagrees. The closure is now inventoried for **calls** as well as
  field reads: 15 sites, 2 reachable. 607 Studio tests OK. **No fourth live
  attempt has occurred; a fresh authorization is required.** Evidence:
  `Evidence/studio-tier0-reload-bookkeeping/`.
- The second authorized Tier-0 attempt was consumed and reached the real
  inner generation path, confirming the identity contract, owned engine
  cleanup (allocated and reserved 0), all 12 serialized Gradio counters, and
  truthful denoising telemetry against the actual Anima session. It stopped
  before conditioning at `modules/processing.py:407` on an uninitialized
  `shared.prompt_styles`. A full closure walk then inventoried all eight
  `modules/shared_init.py` fields against the minimal txt2img path: five are
  read, two were already supplied, and **three were not** —
  `prompt_styles` (before conditioning), `device` (`modules/rng.py:167`, initial
  noise), and `total_tqdm` (`modules/sd_samplers_common.py:433`, per sampler
  step). `forge_headless/headless_compat.py` now supplies all three using the
  production classes and restores them in a `finally`; neither retained consumer
  was guarded. 579 Studio tests OK. A closure guard fails on any newly reachable
  unsupplied field. **No third live attempt has occurred; a fresh authorization
  is required.** Evidence: `Evidence/studio-tier0-startup-globals/`.
- The first authorized Tier-0 image attempt was consumed and failed before
  conditioning, at `modules/processing.py:894`, because the directly loaded
  engine lacked the catalogue identity `process_images_inner` reads
  unconditionally. Neo attaches it in `forge_model_reload()`, which the Tier-0
  path deliberately skips. That contract now exists in
  `forge_headless/model_identity.py`: three fields, attached before
  `shared.sd_model` publication, `sd_model_hash` truthfully `None`, a
  privacy-safe runtime label, and `modules/processing.py` unmodified. The
  probe's failure path now releases the real engine and serialises all 12
  Gradio counters, and `denoising_started` reflects a real sampler step rather
  than intent. 556 Studio tests OK. **No second live attempt has occurred; a
  fresh authorization is required.** Evidence:
  `Evidence/studio-tier0-generation-contract-fix/`.
- The `modules.sd_models` / `modules.processing` source cycle is **fixed**. It
  had three live edges, not the one the reference package described:
  `modules/sd_models.py`, `modules_forge/main_entry.py`, and
  `modules/infotext_utils.py`, each a module-scope import serving exactly one
  attribute. All three are now narrow local-import deferrals. This is a source
  fix, not the earlier harness reorder. A complete inventory over `modules/`,
  `modules_forge/`, `scripts/`, and `extensions-builtin/` finds 16 remaining
  module-scope importers of `modules.processing`, none reachable from
  `sd_models` initialization; a runtime trace confirms it. Validation is green:
  533 Studio tests OK (3 skipped) on both runners, 18/18 import-order tests,
  preflight self-test 179 OK, 12 Gradio guard points instrumented and zero.
  The model-load path is Gradio-free; the minimal generation path imports
  Gradio as a compatibility library. Evidence:
  `Evidence/studio-p0-source-cycle-fix/`.
- Phase 0 baseline, provenance, runtime, launch, dependency, and preflight work
  is complete.
- Neo remains unchanged as the compatibility and rollback surface.
- The prior handcrafted S0.7 reconstruction was rejected because its
  geometry, control density, copy, tool inventory, and canvas composition did
  not match the live Forge Studio interface.
- The complete pinned 63-file Forge Studio frontend and its separate
  `ag-psd.js` helper are now mirrored byte-for-byte as the Canvas shell.
- Owner visual acceptance is `APPROVED` and supersedes the earlier
  `OWNER_REVIEW_REQUIRED` milestone language. Fallback DM Sans and JetBrains
  Mono metrics remain an accepted non-blocking limitation.
- The copied source is isolated from the owned application through a
  source-route adapter; the frontend itself is not rewritten around the mock
  backend.
- S0.6 application, concurrency, Host-validation, accessibility, and mock
  lifecycle work remains preserved.
- The S0 application contracts and backend interface do not import Neo or
  Gradio UI construction.
- Progress reads are elapsed-time observations: concurrent pollers do not
  consume state, completion does not depend on polling, and cancellation is
  terminal.
- Presentation calls are no longer globally serialized; Host validation covers
  GET, HEAD, and POST on the active loopback listener.
- A deterministic mock backend demonstrates success, visible progress,
  cancellation, controlled failure, recovery, and repeated generation.
- The reduced S0.5 shell, rejected handcrafted S0.7 shell, oversized controls,
  centered canvas card, invented mode switcher, and partial tool rail are
  superseded.
- The 49-test post-integration baseline passed. The expanded correction suite
  is 63 green, alongside socket-free evidence and exact source manifests.
- Loopback validation confirms byte-identical canonical `index.html` and
  `app.css`, 19 tools, source-shaped model/generation/progress routes,
  cancellation, controlled failure, recovery, repeat generation, WebSocket
  upgrade, Host rejection, and clean shutdown.
- The source-compatible CSP authorizes the pinned inline loader and event
  handler by exact hash, permits canonical inline styles, and limits scripts,
  fonts, and connections to local/self sources.
- The canonical Google Fonts link remains byte-identical but is blocked by the
  local-only CSP. DM Sans and JetBrains Mono are not bundled, so system
  fallbacks remain a non-blocking accepted typography limitation.
- Opus found the canonical frontend, owned application boundary, and contained
  HTTP/WebSocket surface safe to preserve and integrate. It required the
  listed foundation corrections before a real adapter.
- A1-A6 now correct owned infotext/seed semantics, progress scaling,
  unsupported-parameter notices, non-materializing observation, pure model
  reads, and negative-only generation.
- The A7 static capability audit found no universal multiple-of-64 rule and no
  universal safe maximum:
  `FORGE_ADAPTER_CAPABILITY_REQUIRED = YES`.
- The mock now accepts positive-integer dimensions and preserves them exactly.
  A future real adapter must expose per-model/per-operation alignment and safe
  limits, then reject unsupported requests or disclose normalization before
  dispatch.
- Generated results may use inline data or an owned internal output path.
  Browser responses strip internal paths and no arbitrary file serving exists.
  `ModelSummary.is_mock` defaults to `false`; mock models declare `true`.
- The correction loopback/WebSocket runtime passes all 21 checks. Exact mock
  standalone launch on `127.0.0.1:17865`, empty-prompt generation, recognized
  ignored-setting notices, exact 520x776 output, legacy polling, socket-free
  demo, shutdown, and port release pass.
- The Gradio 4.40.0 / Pillow 12.3.0 dependency conflict remains unresolved.
- No real Forge adapter, real model loading, CUDA initialization, or real
  generation has begun, and Runtime Unblock has not been authorized.
- The reviewed correction range `882c90f3..67a36eb5` was fast-forward
  integrated into `docs/phase0-complete-baseline`. Verdict was
  `SAFE TO FAST-FORWARD INTEGRATE` with three non-blocking observations.
- The Resolve preflight falsely reported `RESOLVE_PLAN_DIRTY_WORKTREE` on a
  clean tree. Root cause: the in-process cleanliness proof failed closed on the
  mere existence of any `.gitattributes` file, because checkout attributes can
  make worktree bytes differ from blob bytes and the comparator modelled only a
  single hardcoded `core.autocrlf` heuristic.
- `feature/runtime-unblock-preflight-fix` replaces that presence check with a
  fail-closed semantic attributes parser covering `text`, `-text`,
  `text=auto`, `eol=lf`, `eol=crlf`, and the non-checkout-affecting
  `whitespace` family, with last-match-wins resolution per tracked path.
  Macros, `filter`, `working-tree-encoding`, `ident`, unknown attributes,
  nested attributes files, `.git/info/attributes`, malformed or unreadable
  files, corrupt index data, missing objects, and mid-proof mutation all still
  fail closed. No Git subprocess was introduced and no filename allowlist is
  used.
- Preflight suite is 179 green, including all 143 pre-existing tests
  unmodified. The Studio suite remains 63 green.
- A deleted or renamed tracked path yields `INCONCLUSIVE_GIT_STATE` rather than
  `DIRTY_BOUND_INPUTS`, because the snapshot-recheck loop re-reads every index
  entry with no missing-file guard. This predates and is unchanged by the
  attributes fix; both outcomes are authorization-ineligible. A separately
  scoped follow-up is recommended.
- The authoritative Gradio pin is `modules/launch_utils.py:295`, not
  `requirements.txt`, and the `is_installed("gradio")` guard means a pin change
  alone will not move an installed Gradio.
- Excluding Gradio, the mandatory Pillow intersection is `>=11.1.0`, satisfied
  by installed 12.3.0. Gradio 4.40.0's `pillow<11.0` is the single binding
  conflict.
- Twelve private Gradio internals are monkeypatched by retained Forge code
  (`gradio.blocks`, `gradio.processing_utils`, `gradio.data_classes`,
  `gradio.utils`, `gradio.networking`, `gradio.context`). These are the real
  upper bound on any Gradio version change, not the Pillow arithmetic.
- All six `MUST FIX BEFORE ADAPTER` items are now implemented on
  `feature/studio-real-adapter-contracts`. Four are `FIXED AND TESTED`;
  model capability and backend shutdown are
  `OWNED CONTRACT FIXED — RUNTIME VERIFICATION DEFERRED` because verifying a
  real backend's behaviour needs a launched Forge process. No unfixed
  owned-contract blocker remains.
- `BackendAdapter` now declares 11 abstract methods, up from 6:
  `load_model`, `unload_model`, `get_current_model`, `get_capability`, and
  `shutdown` were added, plus a non-abstract
  `supported_generation_parameters()` defaulting to empty.
- `ProgressEvent` carries optional `step`/`total_steps`; the frontend bridge
  prefers those over percent-derived arithmetic. New `ModelResidency` and
  `ModelCapability` contracts.
- `SourceFrontendAdapter` no longer holds a private `_selected_model_id`.
  Residency is read from the backend, so a backend-side load or unload is
  immediately visible to the bridge.
- Contained same-origin result delivery is implemented in
  `forge_studio/result_delivery.py`: a bounded registry maps opaque handles
  (`studio-result/<32 hex>.<ext>`) to files beneath one injected result root,
  served by `GET /studio/file` under a sandboxed, script-free response CSP.
  Lookup is exact registry matching, so traversal, absolute paths, encoded and
  double-encoded separators, and even a real registered filesystem path all
  return 404. Containment is re-verified on every read.
- The canonical frontend already defined that delivery contract
  (`app.js:558-560`, `2575-2588`, `455-472`), so it was matched rather than
  replaced. No vendored frontend file changed. The handoff's proposed
  `/studio/results/<id>` route was not used.
- `MockBackend._persist_result` now returns real resolved filesystem paths.
  The former `result_public_root` string prefix looked like a servable URL but
  was served by nothing; that confusion is removed.
- Studio suite is 146 green, up from 63, with 2 skipped where the OS forbids
  symlink creation. Preflight remains 179 green. The loopback validator is
  25/25, up from 21.
- Still not done and still gated: `ForgeBackendAdapter`, Forge launch, CUDA,
  model loading, and real generation. No real PNG has yet traversed the
  delivery path.
- Gradio excision Phase 1 is integrated. Gradio is a temporary legacy
  compatibility dependency and is not part of the target Studio architecture.
- The load-bearing audit finding: `backend/` -- the retained inference engine --
  has **zero** module-level Gradio imports across 91 modules. All 37 importers
  are in `modules/` and `modules_forge/`.
- An earlier analysis pass wrongly reported `backend.memory_management` as
  contaminated. `backend/args.py:131` sits inside `if TYPE_CHECKING:`, which
  never executes. Correcting the analyser to skip TYPE_CHECKING bodies and
  function-local imports flipped that answer to clean.
- Four edges block headless inference, all in `modules/`: `modules.shared`
  (a `gr.Blocks` annotation plus `gr.themes.Base()`), `modules.script_callbacks`
  (reached by `sd_models`), `modules.infotext_utils` (reached by `processing`),
  and `modules.ui` -- reached by `modules/processing.py:35` solely for `sRound`,
  a five-line rounding helper, which also drags in the twelve Gradio
  monkeypatches as an import side effect.
- New owned package `forge_headless/` holds the headless facade. It sits outside
  `forge_studio/` because that package is purity-locked, and outside `modules/`
  because that is where the contamination lives. It imports no Forge module at
  module scope, so importing it is side-effect free.
- The facade implements construct, probe_readiness, get_runtime_identity,
  get_runtime_capability, and shutdown. Model catalogue, load, and generation
  raise stable `HEADLESS_OPERATION_NOT_IMPLEMENTED` or
  `HEADLESS_BACKEND_NOT_READY` and never fake success. Ten lifecycle states are
  defined; Phase 1 reaches at most `READY_NO_MODEL`, asserted by test.
- The subprocess readiness probe reports `HEADLESS_READY_NO_MODEL` with
  `gradio` and `gradio_client` blocked at `sys.meta_path`, importing four real
  retained Forge modules (`backend.args`, `backend.shared`,
  `backend.misc.eps`, `backend.text_processing.parsing`). No Torch, no device
  initialisation, no model, no generation, no network.
- Device, dtype, and attention facts are reported `UNKNOWN`, not guessed:
  establishing them needs `backend.memory_management`, which probes device
  memory at module scope. `cuda_initialized` is `UNKNOWN` for the same reason --
  reading it requires importing Torch.
- Result delivery is detached from Gradio. `ForgeResultDescriptor` validates a
  backend-owned file and `ResultRegistry` mints an opaque handle; no
  `gradio.processing_utils`, `gradio_client.utils`, or Gradio cache object is on
  the path. Proven with a synthetic file under an active import blocker.
- None of the 16 `modules/ui_tempdir.py` surfaces blocks real generation. 13 are
  legacy-compatibility only, 2 are unreachable from the Studio path, 1 is
  replaced by `ResultRegistry`. Twelve live in one function that works around a
  Gradio cache the Studio path does not use.
- `STUDIO_BACKEND` (mock | forge-headless, default mock) and
  `NEO_UI_COMPATIBILITY` (default disabled) are independent selectors. Neither
  broadens network or model access. `GET /studio/runtime_status` reports the
  selection, headless state, and blocking reason with no path or traceback.
- Studio suite is 314 green, up from 245, with the same 3 symlink skips.
  Preflight remains 179 and the loopback validator 26/26.
- Two test-infrastructure defects were found and fixed: the import blocker
  leaked `sys.modules` state and broke a pre-existing isolation guarantee, and
  the import-graph helper under-reported `from modules import ui_tempdir`.
- Phase 1 deleted no Gradio file and modified nothing under `modules/`,
  `modules_forge/`, or `backend/`. The legacy Neo shell works exactly as before.
- Gradio version research is **not** recommended on current evidence: the four
  blocking edges are all fixable without changing Gradio's version. The parked
  `CANDIDATE_METADATA` plan `sha256:04697b62...` remains valid and unused.
- Phase 2A is integrated. Phase 2A validates model selection and loader
  handoff; it does not open or load a checkpoint.
- Phase 1's "four blocking edges" count was **wrong**, and the error was in the
  analysis. The walk recorded the first path found per module. Enumerating every
  module-level chain shows `modules.shared` had **fifteen** paths to Gradio
  through **nine** direct importers, so `modules/options.py`,
  `modules/shared_items.py`, and `modules/extensions.py` had to be cleared too.
  All three were the same small kind of change: move a UI reference to its point
  of use.
- `modules.shared`, `modules.script_callbacks`, `modules.extensions`,
  `modules.options`, and `modules.shared_items` now have zero module-level paths
  to Gradio. Two new pure modules hold relocated logic:
  `modules/resolution.py` (imports only `math`) and `modules/infotext_core.py`
  (imports only `json`).
- Blocker 4 was far smaller than planned. `modules/script_callbacks.py` used
  `Blocks` only in one annotation and already had postponed annotations, so
  moving the import under `TYPE_CHECKING` was the entire fix -- no split, no
  shim, no callback-architecture change.
- Legacy Neo compatibility is preserved by four re-exports
  (`modules.ui.sRound`, `modules.ui._STEP`, `infotext_utils.quote`/`.unquote`)
  plus an explicit lazy `shared.reload_gradio_theme` accessor and
  `shared.ensure_gradio_theme()` at the two UI theme read sites. The removed
  `gr.themes.Base()` was a placeholder that startup always overwrote, because
  `Options.onchange` defaults to `call=True`.
- One deliberate behaviour change: `_STEP` is now frozen on first `sRound` call
  rather than at `modules/ui.py` import. Both happen after options load, rounding
  is asserted byte-for-byte identical, and the change removes a latent
  import-order fragility.
- The remaining device boundary is one line: `modules/shared.py:7` imports
  `backend.memory_management` solely for `xformers_available` (line 30), whose
  only reader is `modules/errors.py:111`. `backend/memory_management.py` calls
  `torch.device`, `torch.xpu.device_count`,
  `get_total_memory(get_torch_device())`, and `get_torch_device_name(...)` at
  module scope, so importing `modules.shared` initializes a device. Its
  Gradio-freedom proof is therefore static, by design.
- `modules.processing` still has 17 module-level paths to Gradio through
  `modules.scripts`, `modules_forge.main_entry`, and `modules.profiling`. All are
  additionally behind the Torch boundary (`modules/processing.py:17` imports
  `torch`), so they are Phase 3 work and were not imported to make a test pass.
- New owned modules: `forge_headless/catalogue.py`,
  `forge_headless/loader_port.py`, `forge_headless/studio_adapter.py`.
  `INTEGRATION_REVISION` is `headless-forge/phase2a`.
- The catalogue enumerates only an explicitly configured root inside the
  workspace. No default root, no auto-discovery, no environment expansion into an
  external path, no recursion by default, symlinks rejected rather than resolved,
  containment re-verified per entry, and inconclusive case behaviour fails
  closed. The case probe is read-only -- it reuses an existing directory entry
  instead of writing one.
- Recognized formats come from source, not convention: `.safetensors`, `.ckpt`,
  `.gguf` are load-plumbed (`modules/sd_models.py:136` plus
  `backend/utils.py::load_torch_file`); `.sft` is recognized by the loader but
  never enumerated by Forge, so it is reported as recognized-not-plumbed;
  `.vae.ckpt`/`.vae.safetensors` are excluded by Forge's own blacklist.
- `model_id` is `sha256(resolved_root || 0x00 || relative_location)[:32]`:
  stable per file and root, root-scoped so a root swap invalidates every id, and
  one-way so it cannot be reversed into a path. No absolute path crosses HTTP.
- No checkpoint content is ever read. Sizes come from `stat`. The probe wraps
  `builtins.open`, `io.open`, `os.open`, and `Path.open` and raises on any access
  beneath the catalogue root, so `CHECKPOINT_NOT_OPENED` is enforced rather than
  inspected.
- The load pipeline is lookup, containment revalidation, availability, format,
  immutable request, loader port, refusal -- refusal last, so unknown, missing,
  unsupported, outside-root, and not-authorized are ten distinct stable codes
  rather than one generic error. Exactly one request reached the port in the
  probe; the unknown and not-plumbed attempts were rejected upstream.
- `STUDIO_MODEL_ROOT` names the one root and is returned verbatim by
  `forge_studio.backend_selection`, which performs no filesystem access at all
  (module-level imports are exactly `os` and `typing`).
- `GET /studio/runtime_status` gained five additive fields:
  `catalogue_configured`, `catalogue_ready`, `catalogue_count`,
  `model_load_plumbing_ready`, `real_model_load_authorized` (always false).
  Counts only -- no root, path, filename, traceback, or environment value.
- Studio suite is 386 green under both the canonical runner and standard
  discovery, up from 314, with the same 3 pre-existing symlink skips. Two skips
  that the first test draft introduced were removed by testing the policies
  directly instead of depending on Windows privileges.
- The pre-existing import-boundary tests caught a real regression in this work:
  a new test imported `modules.resolution` for real and left it in `sys.modules`,
  breaking the standing guarantee that importing Studio pulls in no `modules`.
  Fixed with an exact snapshot/restore. Same defect class as Phase 1's blocker
  leak.
- `EXPECTED_PHASE2A_TESTS = 72` is asserted against the discovered count, so a
  loader failure can no longer silently omit the suite -- the exact failure mode
  that hid 68 Phase 1 tests.
- Phase 2B is integrated. Result: **failed after compatible preflight**. This is
  not a statement that Studio supports the Anima family generally -- it is one
  load of one triplet.
- `shared.xformers_available` is now lazy via a module-level `__getattr__` in
  `modules/shared.py`, cached on first read, with its single reader at
  `modules/errors.py:111`. `modules.shared` therefore has zero module-level
  paths to Gradio **and** zero to Torch, and imports for real with both blocked.
  The device boundary Phase 2A named is gone.
- The owner authorized three exact files under `Private-Local\Models\` for
  `stat`, header read, and one load attempt. No parent directory was enumerated
  -- enforced by `os.lstat`-first intake and asserted by a test that spies on
  `os.listdir` and `os.scandir`. No sibling, hash, copy, move, rename, or write
  occurred, and no other `Private-Local` path was opened.
- `ControlledLoadAuthorization` is constructible only in-process, is single-use,
  matches by absolute path (a sibling does not match), carries its own timeout
  and VRAM ceiling clamped to the owner's maxima, and does not persist across
  restart. A test asserts no module under `forge_studio/` even names it, so
  there is no HTTP bypass.
- Preflight is header-only and every rule is transcribed from retained source
  rather than re-derived: `detection.py:201-221` and `:438-446`,
  `model_list.py:464-489`, `loader.py:527` and `:734-738`, `anima.py:20-22`.
- The triplet is genuinely Anima: `net.`-prefixed DiT with in_channels 16 and
  model_channels 2048; a Qwen3-0.6B encoder at hidden 1024 matching
  `Anima.clip_target`; and a Wan-layout 16-channel VAE. The VAE is named for
  Qwen-Image and *is* Wan-family -- `loader.py:527`, `anima.py:22`
  (`VAE(is_wan=True)`), and the bundled `model_index.json`
  (`AutoencoderKLQwenImage`) all agree. The bundled config also declares
  `CosmosTransformer3DModel`, which is why `detection.py` labels `net.` "cosmos".
- Device established: NVIDIA GeForce RTX 5060 Ti, compute capability 12.0,
  15.93 GiB VRAM, Torch 2.11.0+cu130, bf16 supported. The 14 GiB ceiling is
  enforced by `torch.cuda.set_per_process_memory_fraction` **before** any
  checkpoint is opened, so exceeding it would raise OOM rather than consume the
  card. Only CUDA is ever initialized.
- The load failed at `backend/text_processing/anima_engine.py:24`,
  `opts.emphasis`, because `modules.shared.opts` is `None`. This is reached from
  `Anima.__init__` **after** all three state dicts loaded and the UNet, CLIP, and
  VAE patchers were constructed. The weights loaded; option state was missing.
  Not a model, dtype, device, or file-interpretation problem.
- **A fully Gradio-free real model load is not currently possible.**
  `backend.loader` has zero module-level paths to Gradio, but the fix --
  `modules.shared_init.initialize()`, Forge's own startup step -- imports
  `modules.shared_options`, which has 12. Splitting the option definitions is
  Excision Phase 3.
- Two probe invocations, one attempt. The first failed at
  `backend/utils.py:67` (`ckpt.lower()`) because the harness passed a `Path`
  where a `str` is required -- one line before `safetensors.safe_open`, so no
  file was opened and the tensor boundary was not crossed. Handoff §29 permits
  re-running in exactly that case. The probe now wraps `safetensors.safe_open`
  and records every call, so boundary-crossing is observed, not inferred.
- Cleanup proven: allocated VRAM reached **0 before** `empty_cache` was called,
  which is what shows owned references were released rather than masked;
  reserved was 3.94 GiB of allocator cache and dropped to 0 after
  `release_cuda_cache()`, reported separately. The worker exited on its own
  (returncode 1, not killed), so no hidden load thread survived.
- Peak allocated and reserved VRAM were **not recorded** -- the probe read them
  only on the success path and the exception routed around it. The attempt is
  spent, so they cannot be recovered. The ceiling still held: it was enforced
  before any open and no OOM occurred.
- Studio suite is 428 green under both runners, up from 386, with the same 3
  pre-existing symlink skips. The 42 Phase 2B tests use synthetic safetensors
  fixtures and never touch the three authorized files.
- The Phase 2B preflight initially returned a false `UNSUPPORTED` because the
  checker used a hard-coded prefix list that omitted `net.`. Transcribing
  `detection.unet_prefix_from_state_dict` exactly fixed it. Recurring lesson:
  when a check must agree with someone else's logic, copy that logic.
- **The Phase 2B retry succeeded.** `CONTROLLED_MODEL_LOAD_SUCCEEDED`: an
  `Anima` engine constructed on CUDA with unet, clip, and vae all resident,
  `is_wan` true, then unloaded. This is one load of one triplet and says nothing
  general about the family.
- Forge's own `modules.shared_init.initialize()` was **rejected** rather than
  used: it imports `modules.shared_options`, which carries 12 module-level paths
  to Gradio, while `backend.loader` carries 0. Using it would have made the
  first successful headless load import Gradio.
- New `forge_headless/headless_options.py` parses `modules/shared_options.py`
  **as source, never importing it**, and answers only the option reads the
  retained loader makes. It records every read, raises
  `HEADLESS_OPTION_NOT_AVAILABLE` by name rather than returning `None`, and
  restores the previous `opts` on success and on exception.
- The live load read **exactly one option**: `emphasis = "Original"`
  (`shared_options.py:223`), with nothing missing and `options_restored: true`.
  `gradio`, `modules.shared_options`, and `modules.shared_init` were all absent
  from `sys.modules` at exit.
- Ordering is load-bearing: `backend/text_processing/anima_engine.py:11` binds
  `opts` by value at import time, so options must be installed before
  `backend.loader` is imported. Installing them afterwards reproduces the
  original failure exactly.
- The option parser initially missed `emphasis` -- the one option the boundary
  exists for -- because options are declared as
  `OptionInfo(...).info(...).html(...)` and the outermost call is `.html`, not
  the factory. Unwrapping the chain took the parsed count from 143 to 269. Caught
  by a CPU-only test before the live run; shipping it would have spent the
  authorization on `HEADLESS_OPTION_NOT_AVAILABLE`.
- `ControlledLoadAuthorization.loader_path()`/`loader_paths()` normalize to `str`
  exactly once, so the first attempt's `Path.lower()` failure inside
  `load_torch_file` cannot recur. A test also asserts no module under
  `forge_studio/` can supply a path.
- The single-attempt boundary is now consumed at the **first observed payload
  open**, wired to a `safetensors.safe_open` wrapper, with per-role detection
  reporting which files were opened and whether any unauthorized one was. The
  live run recorded `["checkpoint", "text_encoder", "vae"]` and no unauthorized
  open.
- Stage timings: authorization 0.003 s, header preflight 0.004 s, options
  bootstrap 0.051 s, CUDA init 1.408 s, engine construction 10.308 s, cleanup
  0.228 s, total 12.004 s of a 600 s budget. Unreached stages report
  `not_reached`, never `0.0`.
- VRAM telemetry is collected in guaranteed terminal handling on success,
  exception, and timeout alike -- the gap that lost the first attempt's peaks.
  Before load 0/0; peak 3.665 GiB allocated and 3.674 GiB reserved; after owned
  release and **before** any cache clear 0 allocated with 3.674 GiB reserved;
  after cache clear 0/0. The 14 GiB ceiling was applied before payload access and
  was not exceeded.
- Allocated reaching zero *before* the cache clear is the measurement that
  distinguishes a released reference from a hidden one; `empty_cache()` is
  reported separately and called strictly afterwards.
- `model_dtype` is reported `UNKNOWN`: Forge does not expose dtype on the UNet
  patcher. The files declare BF16 and Anima supports bf16/fp16/fp32, but what the
  runtime selected was not captured and is not claimed.
- Studio suite is 460 green under both runners, up from 428, with the same 3
  pre-existing symlink skips. Three defects were caught before the live attempt:
  the option-parser miss, a `sys.modules` leak that broke three pre-existing
  import-boundary tests, and a `PayloadWatch.restore()` that re-imported
  `safetensors` and failed under `-S`.
- Both of Phase 2B's remaining gaps are closed. **Studio is ready for one
  separately authorized controlled test image. No real generation occurred in
  the readiness phase.**
- An AST scan of the Anima txt2img closure found **99 distinct
  `modules.shared.opts` reads**. The declared minimal inventory is 31, and all 31
  resolve from Forge's own source. The other 68 sit in branches the bounded
  profile disables and are answerable anyway, because the boundary holds Forge's
  whole parsed default map.
- Six generation-path options were invisible to the Phase 2B parser: they live in
  `modules_forge/shared_options.py` and `modules/processing_scripts/*.py`, not in
  `modules/shared_options.py`. One more, `refiner_lora_replacement`, uses the
  keyword form `OptionInfo(default=...)` that positional-only parsing skipped.
  Parsed defaults went from 269 to **281**.
- `outdir_init_images` and `outdir_videos` are computed at import time
  (`util.truncate_path(os.path.join(...))`), so no literal exists to parse. Studio
  owns result delivery, so both are supplied as explicit **overrides** and
  recorded as `override`, never as a Forge default. A test asserts they stay
  unparsed, so a future parser change cannot start guessing at output
  directories.
- New `forge_headless/headless_progress.py` owns nine lifecycle states with an
  explicit transition table. Three rules are enforced rather than documented:
  **fraction stays `None` until a backend declares a real total** (no invented
  percentages), **terminal states never regress**, and **steps are monotonic and
  clamped** so a late callback cannot move progress backwards.
- `ForgeStateBridge` exposes exactly the **fourteen** `shared.state` attributes an
  AST scan found the generation closure touching, writing through to the owned
  model so there is one source of truth. `current_image`, `id_live_preview`,
  `server_command`, and the rest of the UI surface raise an `AttributeError`
  naming the field. `modules/shared_state.py` is untouched and legacy Neo is
  unaffected.
- Cancellation is cooperative: a flag observed at a step boundary, never a kill
  mid-tensor. A cancelled job publishes no result -- proven by cancelling at step
  3 of 12 and confirming nothing reached the registry.
- New `forge_headless/generation_request.py` holds the bounded profile: txt2img,
  768x768, 12 steps, batch 1, explicit seed, Euler / Automatic, CFG 6.0,
  distilled CFG 3.0, with Hires, ADetailer, extensions, reference image, and
  preview all disabled. Frozen after construction.
- Every profile value is derived, not chosen, and recorded in `DERIVATIONS` with
  tests asserting the quoted lines still exist: `Euler` from
  `sd_samplers_kdiffusion.py:21`, `Automatic` from `sd_schedulers.py:268`, CFG 6.0
  from `ui.py:242`, distilled CFG 3.0 from `ui.py:241` **and**
  `model_list.py:473`. For Anima that last field is not a second guidance scale --
  `anima.py:39` sets `use_shift = True` and `processing.py:1342` feeds it to
  `set_shift()`, and the two independent sources agree on 3.0.
- `validate_request()` returns **every** problem rather than the first, across
  fourteen distinct codes, so one run reveals the whole list. A correct request in
  this phase yields exactly one problem:
  `HEADLESS_GENERATION_NOT_AUTHORIZED`.
- New `forge_headless/generation_port.py` validates everything and refuses last.
  A validation failure raises its own code, never reaches the port, and creates no
  job -- so nothing is left to clean up. `PolicyGatedGenerator.authorized` is a
  class attribute set to `False`.
- A recording backend proved the exact request object arrives, progress
  transitions are emitted, cancellation and failure both end terminally with no
  result, repeated requests stay isolated, and `denoise_calls`/`decode_calls`
  remain zero -- there is no denoiser or decoder on this side of the seam.
- A synthetic 1x1 PNG named `SYNTHETIC-NOT-GENERATED.png` passes through the real
  `ResultRegistry` to an opaque handle carrying neither filename nor root.
- The readiness probe reports `FIRST_IMAGE_READINESS_PROVEN`, 10/10, with
  `gradio`, `gradio_client`, `modules.shared_options`, and `modules.shared_init`
  all blocked at `sys.meta_path`: request validated, progress ready, policy
  refusal received, denoising and VAE decode never called, no real image, no
  Torch, no CUDA, no network.
- Studio suite is 515 green under both runners, up from 460, with the same 3
  pre-existing symlink skips. Three defects were caught: the six missing options,
  the keyword-form blind spot, and tests written against a `ResultRegistry` API
  that had been described rather than read.
- What a first image still needs is one authorization carrying the bounded
  profile, the test prompt (deliberately not stored in the repository or in
  runtime status), and the same limits the load used. The generation path will
  read options beyond the declared 31 as it enters branches a dry run cannot
  reach; all are answerable from the parsed map, and any that is not will name
  itself.
- **The first controlled image was attempted and failed before denoising.** No
  image was generated, nothing was published, and `results/` is empty.
- CUDA initialised, the 14 GiB ceiling applied, all three authorized files were
  read once each, and peak allocation reached 3.66 GiB -- identical to the proven
  Phase 2B load, which is the clearest evidence the weights loaded normally and
  the failure came afterwards.
- The failure is a **circular import**: `modules/sd_models.py:12` imports
  `processing`, and `modules/processing.py:32` imports `apply_token_merging`
  back from `sd_models`. The cycle resolves only when `processing` is imported
  first. The probe published the engine with `shared.sd_model = engine` one line
  earlier, and that property setter (`modules/shared_items.py:175`) does
  `import modules.sd_models`, imposing the fatal order. A harness defect, not
  Forge and not the model.
- A second harness defect made the report thin: partial facts were built into a
  local dict and merged only on success, so the exception erased `model_loaded`,
  the device record, and payload access. This is Phase 2B's lost-peak-telemetry
  mistake in a new form -- evidence discarded on the failure path -- and it has
  now happened twice.
- Both defects are fixed. The ordering fix is verified CPU-only: with
  `processing` imported first, `shared.sd_model = ...` no longer raises and
  `sd_models` reports fully initialised. **Neither fix has been executed against
  the model**, because re-running is not this milestone's call.
- The denoising boundary was not crossed, but tensor payloads were read, so
  whether the single authorized attempt is spent is a scope judgement for the
  owner. The failure policy directs stopping and reporting when uncertain.
- Cleanup was clean regardless: allocated reached 0 before any cache clear,
  reserved reached 0 after, `shared.state` and options were both restored, the
  worker exited on its own, and no model file was modified.
- `modules.processing` imports Gradio as a **library** through the three edges
  documented since Phase 2A (`modules.scripts`, `modules_forge.main_entry`,
  `modules.profiling`). No Gradio **UI** is constructed: the probe now counts
  `Blocks.__init__` and refuses `launch()`, so the distinction is observed rather
  than asserted. A fully Gradio-free generation needs Excision Phase 3.
- Three Mac-readiness boundaries are implemented and integrated. Status is
  `MAC ARCHITECTURE READY — RUNTIME UNVERIFIED`: **nothing has run on macOS**,
  no MPS device was initialised, and no volunteer tester was contacted.
- B1 — `modules/platform_selection.py` is a pure helper mapping platform,
  machine, environment overrides, and flags to a Torch command and index
  policy. It imports only the standard library and performs no subprocess,
  network, filesystem, or environment mutation, asserted by AST. Windows and
  Linux keep the retained CUDA 13.0 defaults byte-for-byte; macOS gets an
  unpinned PyPI command with no CUDA suffix and no CUDA index; an unrecognised
  platform selects nothing rather than falling back to CUDA.
- NVIDIA driver guidance is now gated on the CUDA acceleration family, so a
  macOS or CPU machine is never told to update an NVIDIA driver.
- Optional accelerators are rejected with a clear error on macOS. Previously
  macOS fell into the Linux branch and would have been offered a
  `linux_x86_64` FlashAttention wheel on an ARM Mac.
- B2 — `ModelCapability` gains `device_type`, `dtype_policy`, and
  `attention_backend`, each defaulting to `UNKNOWN` so an older backend's
  silence is recorded as silence rather than as a reported CPU. The mock
  reports `cpu`/`fp32`/`backend_default`, true of the mock rather than of the
  host. A test mutates `sys.platform` across four values and asserts the
  reported device does not move.
- B3 — result-root case sensitivity is now an explicit `CasePolicy` resolved
  once at `ResultRegistry` construction, replacing a decision derived from
  `os.name`. `AUTO_DETECT` runs a contained probe inside the owned root;
  `INCONCLUSIVE` fails containment closed. Case sensitivity is a property of
  the volume, and macOS exposes a POSIX API over a case-insensitive filesystem
  by default.
- Studio suite is 215 green, up from 146, with 3 skipped where the OS forbids
  symlink creation. Preflight remains 179 green and the loopback validator
  25/25.
- Two fail-open paths were found by the new tests and fixed: the case probe's
  collision check sat outside its `OSError` guard, and an owned-code purity
  check was a substring scan that failed on its own docstring and is now
  AST-based.
- The preflight Git-state proof still folds case on `os.name` at six sites.
  Deliberately unchanged this milestone; specified in
  `docs/studio/MAC_PREFLIGHT_CASE_SENSITIVITY_FOLLOWUP.md`, including the
  false-clean risk from index case collisions on a case-insensitive volume.
- A future Apple Silicon tester protocol, consent checklist, data disclosure,
  and rollback procedure are prepared under
  `Evidence/studio-mac-readiness/`. They are plans, not authorization. No
  volunteer has been contacted and no command has been sent to anyone.
- Fast Mac-hardening milestone closed three correctness gaps.
- xformers moved into the platform branch with the other optional
  accelerators, so macOS constructs no CUDA index string for it at all. The
  value is absent rather than present-but-guarded, and `is_cuda` no longer
  appears in `launch_utils.py`. Windows and Linux are unchanged.
- `ModelCapability` now requires enum members for `device_type`,
  `dtype_policy`, and `attention_backend`, with a stable `TypeError` naming the
  field. The enums subclass `str`, so a raw string previously serialized
  identically and passed unnoticed.
- `GET /studio/capability?model_id=<id>&operation=txt2img` exposes the
  capability contract as a pure read. It inherits Host validation, adds no CORS
  header, bounds its parameters, returns structured 400 for a missing or
  oversized parameter, reuses the existing structured errors for unknown model
  and unsupported operation, and never selects or loads a model.
- Studio suite is 245 green, up from 215, with the same 3 symlink skips.
  Preflight remains 179. Loopback is 26/26, up from 25.
- A minimal Mac T0/T1 volunteer package is built and statically reviewed at
  `Evidence/studio-mac-tester-t0-t1/studio-mac-t0-t1.tar.gz`
  (8.6 MB, sha256 `61d47c16...ffd180b6`). All twelve required checks pass.
  **It has not been sent, no volunteer has been contacted, and nothing has run
  on macOS.** T1 requires Python 3.13+ and stops with
  `T1_BLOCKED_PYTHON_VERSION` otherwise, installing nothing.

## Known issues relevant to the fork

- console generation time reportedly overstates observed time by about six seconds;
- reduced-latent preview path produced severely garbled previews for one user;
- Studio-specific preview scheduling previously caused measurable overhead;
- model/Hires/ADetailer transition latency needs phase-level measurement;
- JPEG/WebP quality state must remain separately persisted;
- ADetailer per-slot LoRA behavior must preserve blank prompt inheritance.

## Studio Alpha S0 evidence

- `Evidence/studio-alpha-s0/demo-report.json`
- `Evidence/studio-alpha-s0/results/mock-job-0001.svg`
- `Evidence/studio-alpha-s0/results/mock-job-0001.json`
- `Evidence/studio-alpha-s0/results/mock-job-0004.svg`
- `Evidence/studio-alpha-s0/results/mock-job-0004.json`

## Studio Alpha S0.5 evidence

- `Evidence/studio-alpha-s05/demo-report.json`
- `Evidence/studio-alpha-s05/loopback-validation.json`
- `Evidence/studio-alpha-s05/port-map.md`
- `Evidence/studio-alpha-s05/visual-review.md`

## Studio Alpha S0.6 evidence

- `Evidence/studio-alpha-s06/demo-report.json`
- `Evidence/studio-alpha-s06/loopback-validation.json`
- `Evidence/studio-alpha-s06/concurrency-probe.json`
- `Evidence/studio-alpha-s06/reference-identity.md`
- `Evidence/studio-alpha-s06/visual-acceptance.md`

## Studio Alpha S0.7 evidence

- `Evidence/studio-alpha-s07-canonical/source-manifest.txt`
- `Evidence/studio-alpha-s07-canonical/source-audit.md`
- `Evidence/studio-alpha-s07-canonical/websocket-validation.json`
- `Evidence/studio-alpha-s07-canonical/runtime-validation.md`
- `Evidence/studio-alpha-s07-canonical/test-summary.txt`
- `Evidence/studio-alpha-s07-canonical/visual-acceptance.md`
- `Evidence/studio-alpha-s07-canonical/privacy-review.md`
- `Evidence/studio-alpha-s07/design-authority.md`
- `Evidence/studio-alpha-s07/visual-acceptance-checklist.md`
- `Evidence/studio-alpha-s07/runtime-validation.json`
- `Evidence/studio-alpha-s07/test-summary.txt`
- `Evidence/studio-alpha-s07/reference-manifest.txt`
- `Evidence/studio-alpha-s07/demo-report.json`

## Studio pre-adapter correction evidence

- `Evidence/studio-pre-adapter-corrections/opus-findings-disposition.md`
- `Evidence/studio-pre-adapter-corrections/contract-readiness.md`
- `Evidence/studio-pre-adapter-corrections/dimension-capability-audit-summary.md`
- `Evidence/studio-pre-adapter-corrections/runtime-validation.json`
- `Evidence/studio-pre-adapter-corrections/test-summary.txt`

## Next single task

**CURRENT PROGRAMME: Studio Friends Alpha 0.1**, packages AR0-AR9. See
"Friends Alpha 0.1 scope" below for the platform and feature boundary, and
`docs/16_ALPHA_MATRIX.md` for per-feature status, evidence class and release
disposition.

The golden-anchor task that stood here is complete and its successors are too:
generation, opaque publication, Canvas native pixel identity, terminal release
counted exactly once, and result durability across both unload and process
restart are all proven on real payloads.

### HISTORICAL — the P0 convergence board

Kept because it records how the project got here, and because several of its
rows are cited elsewhere. It is NOT the current task list, and two of its rows
below are stale as written; the corrections follow the block.

```text
P0.0  authority reset                                    DONE
P0.1  ModelSelection at the loader boundary              DONE
P0.2  ensure_loaded + Generate legal from NO_MODEL       DONE
P0.3  remove profile UI / API / lifecycle / config       DONE
P0.4  filesystem browser + model roots v2                DONE  7abe5d83
P0.5  standalone Docker foundation                       DONE  cb9c76a6
      (container legs externally pending)
P0.6  canonical generation request + real Neo registries DONE
      sampler/scheduler carried end to end               DONE  05c7ed32
      live proof, with a determinism control             DONE  this session
      unknown sampler/scheduler refused, not discarded   DONE  5095a0a9
      failed jobs keep their reason                      DONE  d5514a9d
      retire the profile-shaped surface                  DONE  2cbfcbaa
      nest the request under `generation`                DONE  afddac49
      restore image_pixels / import_pixels               DONE  06200181
P0.6  COMPLETE. Live acceptance 10/10 on an idle GPU,
      canonical 2198 OK exit 0.
P0.7  Hires Fix                                          NEARLY DONE
      carried end to end, both upscaler paths            DONE  cdfcc436
      proven live at 4x: 2048x2048, peak ~10 GB of 16    DONE  this session
      second-pass states, metadata recipe, page group    DONE  83b20c7b
      live: progress across a real pass, cancellation,
        repeatability, recipe in a real result           OPEN (GPU, bundled)
P0.8  ADetailer / Auto Detail, three slots               IN PROGRESS
      owner AUTHORIZED; the P0.8 stop is superseded
      Bing-su primary, ADetailer-Neo as Neo reference
      three-slot schema on the canonical request         DONE
      vendoring surface scoped (~15 KB of 30 files),
        download machinery deliberately excluded         DONE
      provenance by CONTENT HASH; upstream SHA is not
        derivable from the archive and is not guessed    DONE
      adapter, detector catalogue, pipeline order        OPEN
      owner prerequisites: ultralytics (one pip install,
        AGPL, network) and detector .pt weights          OPEN
```

P0.2 must land before P0.3. Removing the visible panel while Generate still
requires an explicit load produces a hidden Load button, which is renaming the
defect rather than fixing it. The requirement is functional removal.

### Corrections to the board above, 2026-08-19

Two rows describe a state that is no longer true. The board is left intact as
history; these supersede it.

**P0.7 Hires** read "NEARLY DONE" with only live progress/cancellation legs
open. Since then Hires also reaches image operations, which that row never
contemplated: `2a0f162c` implements the second pass for Canvas img2img and
inpaint, `9910ff9b` retires the refusal that blocked it and records the live
proof (768x768 output, mean |delta| 7.61 against a LANCZOS upscale, zero
differing pixels outside the permitted mask region). Still open: progress and
cancellation across a real Hires pass, and Hires together with Auto Detail in
one job.

**P0.8 Auto Detail** read "IN PROGRESS" with "adapter, detector catalogue,
pipeline order OPEN" and the ultralytics/weights prerequisites unmet. All four
are now demonstrably closed. A live job on 2026-08-19 resolved
`face_yolov8n.pt` from `/api/detectors`, reported `candidates=1, regions=1,
detailed=True` from the engine's own `SlotOutcome`, and produced a detailed
result; five detector weights are installed under `models/adetailer`. What
remains untested is THREE slots together and Auto Detail combined with Hires --
both AR1 work, not the open items this row lists.

## Friends Alpha 0.1 scope

Frozen 2026-08-19. This is a controlled, invite-only alpha, NOT Studio 1.0
parity and not a public release.

**Platform: Windows 10/11, local loopback, NVIDIA/CUDA.** macOS and Linux
remain mandatory PRODUCT requirements and their architecture and static
compatibility tests are preserved, but neither may be called alpha-ready until
Studio actually executes there. Docker stays UNVERIFIED rather than failed: no
Docker engine exists on this machine, and that does not block a Windows-native
friends alpha.

Included: launch with no resident model; Checkpoint/Text Encoder/VAE selection;
automatic load, reuse and switch at Generate; visible queue; progress,
cancellation, failure and cleanup; txt2img; Canvas img2img; Canvas inpaint;
Soft Inpainting; Hires for all three operations; Auto Detail after Hires with
three slots; aspect randomisation; Dynamic Prompts/Wildcards expansion; Canvas
painting and mask workflows; Send/Open Result in Canvas; output saving; Save
Defaults; Remember Last Session; result/gallery access sufficient to retrieve
work; High Precision/Develop ONLY if truthfully classified experimental and
proven safe.

Explicitly NOT required: full 1.0 parity, Regional/attention-couple, Inpaint
Sketch, ControlNet or Workshop where the service is absent, Live Painting,
AutoBridge, Hires checkpoint swapping, High Precision 2.0, durable queue
recovery across process termination, Docker execution, live macOS/Linux proof,
exhaustive combination coverage, broad performance work.

D8 applies to everything excluded: an absent service means the control is
HIDDEN; a present-but-unavailable capability means the control is DISABLED with
a truthful reason. A polished control that silently does nothing is an alpha
blocker, not a cosmetic issue.

## Session handoff — 2026-08-19, Friends Alpha packages AR0/AR2.1

**HEAD `e05aeea7`. Canonical 3868 OK, exit 0, 4 skipped. Tree clean. No GPU
held.**

### Completed this session

**AR0 — project truth reconciled (`9b9a4caa`).** Three documents were
commanding the next agent to redo finished work. AGENTS.md's "Current
Img2Img/Inpaint/Hires source gate" and CLAUDE.md's "Immediate WP1.4 gate" are
now records pointing at the four source-review records and the commits that
settled each point; the general Source Review Gate is untouched. PROJECT_STATE's
P0 board is marked HISTORICAL with corrections appended rather than edited over
-- P0.7 never contemplated Hires for image operations, and P0.8's "adapter,
detector catalogue, pipeline order OPEN" is closed by live evidence. New:
`docs/16_ALPHA_MATRIX.md` and `Evidence/source-review/README.md`.

**AR2.1 — Canvas document identity (`e05aeea7`).** Opaque `document_id` plus
monotonic `canvas_revision`, captured with the pixels and carried to the
immutable request. Studio-owned by necessity: the Extension has no equivalent
because its generate route consumes the payload in the same call.

### BLOCKED, and why

**AR1 (Hires + Auto Detail, three slots, overrides) is GPU work and the GPU is
NOT free.** `llama-server` and `LM Studio` have held ~6.5 GB since 16:09. The
harness is written and ready at `Evidence/ar1_full_chain_live.py`; it needs one
serial run of about five minutes on an idle card. Nothing was run against a
contended GPU.

### Next, in order

1. **AR1** the moment the GPU frees. The harness covers AR1.1/1.2/1.3 in one
   session: named upscaler from the live catalogue, AD after Hires on the
   Hires-sized frame, three slots each with an explicit outcome, a Hires
   sampler override proven to reach execution, exact preservation, one public
   job per submission, and a state-leak check either side.
2. **AR2.2/2.3/2.4** -- source/mask consistency refusals, retain/release wired
   to the job lifecycle, hash enforcement. All off-GPU.
3. ~~AR6.1 result-root correctness~~ **DONE, and the premise was wrong.**
   `launch.py:104-105` already anchors a relative `result_root` to
   `WORKSPACE_ROOT`, which is derived from `__file__` rather than the CWD.
   Measured from three working directories, it resolves identically. My earlier
   "result-root CWD sensitivity" claim was a plausible guess I never verified,
   and it is retired. The original `GENERATION_FAILED reason=OSError` remains
   UNDIAGNOSED -- relaunching from a different directory and seeing it work is
   not evidence about its cause, and it has not reproduced since.
4. **AR4.2/4.3** -- Dynamic Prompts restart gate, Save Defaults and Remember
   Last Session. Browser work, no GPU.

### Standing cautions for the next session

- Six alpha features still have NO evidence of any kind: Save Defaults,
  Remember Last Session, Send/Open Result in Canvas, tester docs, the support
  bundle, and the fresh package. The matrix marks them blockers; none has been
  started.
- The seam is where this programme keeps finding defects -- five times now.
  Test the join, never the two endpoints.

## Unresolved decisions

- final public repository name and URL;
- exact default stock UI path/launch mode;
- initial supported hardware/model matrix;
- whether the standalone extension remains supported after beta;
- ControlNet slot count: the extension README says three, the reviewed
  frontend references two numbered slots. Resolve as a product decision
  before implementing ControlNet;
- pinned ADetailer-Neo revision, namespace (`lib_adetailer` vs legacy
  `adetailer`) and its license/attribution obligations;
- Docker acceptance cannot run in the current development environment: no
  Docker engine is installed on the workstation. P0.5 code and its non-container
  tests can proceed; container acceptance needs an environment that has it.

## Last updated

2026-08-09 - P0.6(2). The exit-139 segfault is closed and attributed: VRAM
exhaustion, not a regression. The triplet load costs ~5.8 GB measured
(2428 -> 8495 MiB); a game holding ~7.3 GB of the 16 GB card left ~8.7 GB and
it did not fit. On an idle GPU the identical request completes.

The sampler/scheduler live leg is proven, with a DETERMINISM CONTROL: a re-run
of an identical configuration at seed 7 was byte-identical, and only against
that baseline does "two samplers, two images" mean the field reached dispatch.
The suspected scheduler name/label mismatch does not exist -- Neo's
`schedulers_map` is keyed by both forms and `get_sampler_and_scheduler` returns
the LABEL, which is what Studio already emitted.

Two defects were found by driving the product, both invisible to a green suite.
An unknown SCHEDULER was accepted, reported as a completed job, and silently
discarded ("Karrass" and "ZZZNonsense" produced one byte-identical image with
error null) -- now REFUSED by field name, wiring up `is_known_sampler` /
`is_known_scheduler`, which existed with zero production callers. A job that
fails inside the backend still reports `error: null` because
`_terminal_from_backend` reads the state and never the reason -- DOCUMENTED,
NOT FIXED.

Two runbook instructions were wrong and are corrected (`da84bb36`): `/api/models`
is the Neo backend catalogue and is empty until a load, and `error: null` has
two readings rather than one.

P0.8 stopped at the owner boundary as instructed, with a verified
recommendation: `modules_forge/config.py` and the Master Book name
Haoming02/ADetailer-Neo while `Reference/adetailer.zip` is Bing-su/adetailer
"26.2.0-studio.1". Different dependencies; the owner must choose.

canonical 2170 OK exit 0 (2165 + 5 new).
Evidence: `Evidence/studio-p0-6-live-sampler-proof/`.

2026-08-08 - P0.0 authority reset. The two Reference books become the master
plan; the narrow profile-removal plan is marked superseded for still modeling
an explicit Load step. Product contract restated: no owner-facing profile, no
mandatory Load, Generate owns model readiness, Forge Neo is the loading
oracle. The historical claims that "explicit load is the only payload
boundary" and "a generation request can no longer open a model" are annotated
as inverted; the ownership guarantee behind them survives via selection-on-job.

2026-08-10 - Owner-found defects A-D closed (registry refresh after engine
init; Live Preview carried and decoded; Jobs/Queue panel replacing the
internal-alpha Model/Session surface; per-job console logging). Queue gained
owned admission: `_pending` is both the displayed and the executed order, one
job admitted at a time, with reorder/remove/clear/cancel-all. Cancelling a
RUNNING job was unreachable -- `mark_running` had no caller -- and now works.
P0.8 gained the detector adapter, the offline ultralytics boundary (measured
with a control: unguarded import makes one socket attempt, guarded makes
none), the `adetailer` root, and the `SELECTION_ROLES` -> `RESIDENT_MODEL_ROLES`
rename done before the fourth role rather than after. The first live P0.7
re-run then found four defects a green suite could not see, all in the path
every generation takes: the stage label was never merged into `/api/jobs/<id>`;
`_in_flight_job_id` was never cleared so one job reported another's progress;
gateway progress was adopted only at terminal, so every job read `queued` for
its whole life; and Neo's zero-based step callbacks made `HIRES_PREPARING`
unreachable in production while forty-plus isolated tests passed by feeding a
value Neo never sends. P0.7 8/15 -> 10/15 live, with the remaining five
attributable to the fourth defect and unit-proven but not yet re-run live.
Studio 2240 -> 2470 OK.

2026-08-08 - Owner workflow repairs, all live-verified: Generate never worked
on a lifecycle host (client submitted seed -1 against a backend that refuses
negative seeds by design); job records discarded the backend's structured
failure reason; Load was unreachable in the UI because the text-encoder row
was gated on a legacy probe keyed by checkpoint title rather than opaque
catalogue id; Send to Canvas hung forever on a hidden document because
`img.decode()` was the only gate; and a restarted Studio overwrote earlier
results because the in-memory result counter was also the filename. Studio
1765 -> 1817 OK.

2026-07-25 - canonical Studio 4.17 presentation approved and integrated;
Opus-directed pre-adapter contract corrections, dimension capability audit,
63-test suite, and 21-check deterministic mock runtime pass recorded.

2026-07-27 - P0 source-cycle fix applied to `modules/sd_models.py` and found
insufficient: the cycle has three edges, not one. Import-order regression
suite added (13 tests) and Gradio runtime instrumentation expanded to 12
points. Stopped at the handoff stop condition; branch not integrated.

2026-07-27 - Fifth Tier-0 attempt consumed and SUCCEEDED: first real 768x768
standalone Studio-backend image, 12/12 steps, decode, and opaque publication,
with all twelve Gradio counters zero. Golden-anchor acceptance withheld on a
9,568,256-byte post-success teardown residual, whose owner was then identified as
the instance-level `latents_after_sampling` accumulator and released. Studio
643 -> 678 OK.

2026-07-27 - Fourth Tier-0 attempt consumed; passed the reload fast path,
completed conditioning and initial noise, and entered CFGDenoiser.forward before
stopping on a runtime-generated callback-priority option. Missing options are now
AttributeError-compatible while direct reads stay strict, and the class-level
conditioning caches behind the 13.1 MiB residual are released before VRAM is
measured. Studio 607 -> 643 OK.

2026-07-27 - Third Tier-0 attempt consumed; reached prompt setup and stopped at
`modules/processing.py:945`, the inner-loop `forge_model_reload()` call that
every prior document claimed the inner entry point bypassed. Direct-load reload
bookkeeping added so Forge's native early return is satisfied truthfully; call
inventory extended from `shared` field reads to reload/catalogue calls. Studio
579 -> 607 OK.

2026-07-27 - Second Tier-0 attempt consumed; reached the real inner
generation path and stopped before conditioning on uninitialized
`shared.prompt_styles`. Closure walk found three unsupplied startup globals, not
two: `prompt_styles`, `device`, `total_tqdm`. All supplied through an owned
compatibility context using production classes, restored in a `finally`. Studio
556 -> 579 OK.

2026-07-27 - First Tier-0 attempt consumed and failed at
`modules/processing.py:894` on missing model identity; no image. Identity
contract added at the headless loader seam, failure-path engine release and
Gradio guard serialisation made durable, and lifecycle boundaries corrected so
denoising is reported only on a real sampler step. Studio 533 -> 556 OK.

2026-07-27 - P0 completed. The two remaining edges in `modules_forge/
main_entry.py` and `modules/infotext_utils.py` deferred by the same narrow
local import; behavior-preservation tests added for both. Suite 13 -> 18, all
passing; Studio 515 -> 533 OK. Canonical preflight invocation established
(`bootstrap.py self-test`, 179 OK). Integrated fast-forward into
`docs/phase0-complete-baseline`.

---

## 2026-08-11 — R0 closed, R1 opened

Appended rather than rewritten: this file is append-only by convention and its
earlier sections are history. What follows supersedes the milestone board above
where they disagree.

### Milestone board, current

```text
P0.0-P0.3                                          DONE (unchanged)
P0.4  filesystem browser + model roots v2          CORE DONE; the broad
      "every Browse/Open works" claim is           CONTRADICTED -- Dynamic
      Prompts, Gallery, save, watermark and LoRA
      surfaces were dead and are now capability-gated
P0.5  standalone Docker foundation                 CONTRADICTED / REOPENED
      The commit added only a test, and the artifacts it reads live OUTSIDE
      the Git root, so a clean clone does not contain them. No image built,
      no container run.
P0.6  canonical generation request                 base txt2img slice DONE
      Not the full contract. `variation` landed 2026-08-11; img2img, inpaint,
      batch, LoRA, ControlNet and output settings remain absent.
P0.7  Hires Fix                                    txt2img slice CLOSED
      15/15 live, re-proven 2026-08-11. Does not extend to img2img Hires or
      temporary checkpoint override/restoration.
P0.8  Auto Detail                                  IN PROGRESS
      Schema, detector catalogue, offline boundary and pipeline order exist
      and are tested. Only slot 1 has ever executed live and its raw record
      was not preserved; the six-case matrix is absent.
R0    baseline recovery                            CLOSED  7177fefe
      All eight exit criteria. 40/40 live across two cold processes.
R1    truthfulness + capability gating             OPEN, 7 slices accepted
      Fourteen owner-visible lies removed. Not closed -- its gate is every
      visible control truthful with browser evidence.
```

### Claims in this file now superseded

- "Generation, result delivery and the Canvas path are proven on real payloads"
  remains true, and remains narrower than it reads: one Windows/RTX host, one
  tested triplet.
- The "Current phase" section describes the P0 convergence as what remains.
  P0.0-P0.7 are closed or narrowed as above; the live program is now R1, then
  the R2 owner decisions.
- "Next single task" is superseded by
  `Reference/FORGE_STUDIO_OVERNIGHT_RESULT_2026-08-11.md`, whose exact next
  action is: **answer the writable-state-directory decision (R2 packet D1)**.
  It blocks durable preferences, workflows, layouts, presets and the Gallery
  database -- the largest remaining owner-visible defect class, since settings
  silently reset on every launch.

### The framing correction that matters most

The interface is the shipping Forge Studio extension's frontend. Source
identity is not feature parity, and much of what that UI calls has no service
in this repository. Entry points for absent services are now capability-gated
rather than implemented, which makes the gap visible instead of confusing.

### Last updated

2026-08-11 — HEAD 502c10d7, tree clean, canonical 2742 OK exit 0.
Evidence: `Evidence/r0-r1-overnight-2026-08-11/`.

---

## 2026-08-17 — WP0.4, the Dynamic Prompts write

Appended, as this file's convention requires. Supersedes nothing above; it
closes one item the 2026-08-11 board did not yet name.

### What was wrong

`source_api_adapter.py` had TWO `POST /studio/dynamic_prompts/config` branches.
The first answered with a hardcoded body and won every time, so `set_enabled`
— the second — had no reachable caller and the owner's choice was never stored.

Wildcards themselves were never broken: expansion runs server-side on the live
path, and `enabled()` fell back to a capability default that said "on" wherever
a folder resolved. The missing capability was the OFF switch. The stub's own
comment, "there is no wildcard service to persist a preference to", was true
when written and became false when the service was built.

The service-level suites never caught it because every one of them calls
`set_enabled` directly. Only the route was broken, and only a route test could
have seen it.

### The capability-default question the bridge raised

Answered, and deliberately not acted on. Capability WAS compensating: with
nothing ever stored, the fallback was the whole setting rather than a default.
An AND term was implemented and reverted — `config()` feeds the toggle's `on`
class and `data-setting-depends` retires Browse from it, so refusing an owner
with no folder yet would remove the control that would have given them one.
Capability stays a default; `WildcardService.enabled` records why.

One behaviour changed on purpose: an explicit `true` with no folder is now
answered `true` rather than the stub's forced `false`. `status()` still reports
`available: false`, which is what the panel disables on.

### Evidence

`Evidence/wp0-dynamic-prompts-persistence/`. Canonical 3657 OK exit 0
(3656 + 1 net new test), 4 skipped. Mutation guard: restoring the stub fails
two tests by name.

### Not claimed

No browser or live evidence. WP0.4's executing-browser toggle-and-restart
journey has NOT been run; persistence is proven only to the level of a fresh
service instance reading the stored choice back. This closes one WP0 item and
does not close WP0.

---

## 2026-08-18 — WP0 worked through, and what it is still not

Appended per this file's convention.

### Closed

```text
WP0.4  Dynamic Prompts write reachable          0b698b48
       Img2Img-Hires known-defect markers       33095bde
WP0.3  route ledger                             91c04492
       response statuses                        9c1dfb3a
       control half                             219ab07a
       patch inventory corrected + enforced     bf4f93d8
WP0.1  baseline envelope                        d43b23b1
WP0.2  bundle collector                         25ad73ce
WP0.5  Docker, static layout only               14293fdf
```

Canonical 3699 OK exit 0, 4 skipped, at `219ab07a`.

### The pattern in what was found

Almost nothing was missing. Nearly every defect was a MEASUREMENT that had gone
wrong quietly, and a number nobody could act on:

- the patch inventory said 15 files; git said 21, and the doc's own summary
  ("only the import graph is patched") had been false since 2026-08-14;
- the bundle advertised 923 paths for 837 files, printed for anyone to read and
  enforced by nothing;
- the Docker test read its inputs at import time, so a clean checkout lost all
  37 tests to a collection error rather than a failure;
- the route scan silently skipped a file whose cache keys are NUL-separated;
- `render()` dropped the entire control half while its test still passed,
  because that test compares the generator to its own output.

Every one is now recomputed from source and enforced. The tooling built this
session is worth more than the fixes: `test_patch_inventory`,
`test_parity_ledger`, `test_parity_bundle` and the repaired
`test_docker_foundation` all fail on the drift that produced their own defects.

### Not closed, and not claimed

- **Gate A is NOT met.** WP0.4's executing-browser toggle/restart journey has
  not run, so Dynamic Prompts persistence is proven only to the level of a
  fresh service instance reading the stored choice back.
- **No Docker anything.** No engine on this workstation; all eleven gates in
  master book section 17 remain externally pending. The layout is static-only.
- **No live, GPU or browser evidence was produced this session at all.**
- Two owner decisions are surfaced and deliberately not taken: retiring the
  workspace-root Docker duplicates (a deletion), and whether upstream's
  `app/docker/` should eventually go (a Neo-owned edit needing an inventory
  entry).
- The eight unbound controls are dispositioned, not fixed. They belong to
  WP3/WP4B, WP6A and WP9.1.

### Next

WP1 — the single lifecycle collector. `_GENERATION_FIELDS` still refuses
`operation`, `source_image`, `mask` and `denoising_strength`, and `app.js`
still returns from the lifecycle branch before exporting Canvas pixels, so the
shipping Generate button cannot submit Img2Img. The ledger now records that as
data rather than prose.

---

## 2026-08-21 (second session) — the browser baseline, NG-1, NG-3, NG-2

```text
HEAD       f526a54f      tree CLEAN
canonical  4365 tests, OK (4 skipped), exit 0
GPU        no model was loaded at any point; every process stopped
```

Handoff:
`Reference/CONTINUATION_HANDOFF_2026-08-21_BROWSER_BASELINE_NG1_NG2_NG3.md`.
Reviews: `AR8.3` through `AR8.6` under `Evidence/source-review/`.

### What the §4.1 thesis predicted, and what it cost

A large batch of frontend work was server-proven and browser-unproven. One
browser session found five defects, none of which 4,318 passing tests could
see:

- a red "Model load failed" toast on EVERY launch, from two boot paths racing
  the lifecycle probe;
- the LoRA model root reading "Not configured" while serving 73 LoRAs -- and
  pressing Save DELETED it, because `configure()` replaces every role at once
  with no merge;
- Smoothing 0 dividing by a zero window and painting nothing, INHERITED from
  the shipping extension;
- the parity ledger crediting three files as callers of routes they never call,
  because a backticked route in a `//` comment looks like a template literal;
- "All three folders are saved together" over a card listing five.

A sixth instance -- the same role-set defect on `adetailer` -- was found by the
guard written for the fifth, within minutes. Guard the structure, not the
instance.

### NG-1, NG-3, NG-2 closed

NG-1: nothing registered the result root; measured `[]` on the owner's own
install. Studio now adopts it at `store()`, and `/scan-folders` stopped
returning absolute paths. The generation-trigger leg is UNPROVEN and needs GPU.

NG-3: the boundary moved to `app.js:3074`. There is no tenth dead generation
field. The first scan reported 12 and was WRONG -- three inpaint controls are
read through `_num(id, fallback)`, not `getElementById`.

NG-2: Neo conditions from `opts.CLIP_stop_at_last_layers` and writes the
infotext from `p.clip_skip`, so the obvious implementation would have made the
metadata lie. Applied through the existing per-job `job_scope` instead.

### The flaky canonical test — FOUND AND FIXED

`test_gallery_service.GenerationIsolationTests`
`.test_a_generated_image_finds_its_prompt_once_scanned` failed 3 pass / 7 fail
out of 10, and 3/10 at pristine `221bee89` with every change stashed --
pre-existing and old. Every "canonical OK" recorded in this project until now
had a real chance of being luck on that one test.

Cause: `/scan` returns and then hashes on a thread it does not join, so
`images.content_hash` is still NULL when the test reads metadata keyed by that
hash. The product is not racy the same way -- a real generated image carries
its parameters in the file and never needs the hash.

Fixed by moving the existing `wait_for_enrichment()` INTO `scan_now()`, so the
safe order is the default rather than something each test must remember. A
second test (`test_parameters_saved_by_hash_are_found_before_the_scan_links_them`)
had the identical latent race and was passing only by luck.

After: 20/20 on the test, 15/15 on the file. Every test file that mentions
threads or sleeps was then run five times each -- 19 files, 95 runs, all
stable. That does NOT prove the suite is deterministic; see
`Evidence/source-review/AR8.7-flaky-canonical-test.md` for what it does and
does not establish.

### AC1 browser journey — done, no GPU

All 18 steps of §5.6 except step 11. Two documents, four layers and two,
distinct colours, a mask, an F5, and a restart on a NEW ephemeral port (a
different browser origin, so nothing came from localStorage). Both returned
byte-identically every time: same fingerprint, same per-colour pixel counts,
same layer order, same revision, same mask length.

Three things the journey established that the unit tests could not:

- the unload flush is best-effort and the DEBOUNCE is the real safety net. A
  capture took ~34 s to reach disk in a hidden tab (the module's own note
  records 13.9 s), so an owner who edits and immediately reloads can lose that
  edit. `sendBeacon`/`keepalive` are not used. Owner decision.
- a corrupt manifest is skipped SILENTLY on the browser side. The server logs
  a warning; `recovery_list` returns the survivors with no notice, and the
  owner sees one document where they left two. The write side raises a toast
  for exactly this class of failure; the read side does not.
- step 11 asks for a stale-revision refusal that is not implemented anywhere.
  The refusals that DO exist are named and correct, including a traversal
  attempt refused on shape with no path in the reply.

Evidence: `Evidence/ac1-browser-journey/AC1-browser-acceptance.md`.

### Brush range defects — two fixed, one for the owner (AR8.9)

Same class as the Smoothing 0 divide-by-zero. Found by the sweep, confirmed in
a browser, fixed against the invariant.

- the "increase brush size" key was `Math.min(100, ...)` while the Size control
  offers 500, so a brush set to 300 jumped DOWN to 100 on the first press
  (brushPx 3991 -> 768). The bound is read from the control now and `Math.max`
  guarantees the direction.
- Clone Stamp painted NOTHING at minimum Size with Hardness under 100%: the
  1x1 stamp's only pixel sits 0.707 from a centre of radius 0.5, so the
  softness falloff was evaluated outside the dab and zeroed it. Guarded with
  `sz >= 2`; after, 212 px at hardness 0.5, identical to the 1.0 control.
- NOT fixed: the Size control offers 1..500 while the curve reaches full canvas
  at 100, so four fifths of its travel is "bigger than the document".
  Inherited from the Extension. Lowering the max removes range from a control,
  against the standing "not the AI Generation Police" ruling; rescaling the
  curve changes what every saved brush size means. Owner decision, with the
  measurements in AR8.9.

### A saved default Sampler does not survive a reload (AR8.10) — OPEN

Reproduced end to end: choose a non-default sampler, Save Current as Defaults,
reload -> the select shows the HTML default while `dataset.pendingValue` still
holds the owner's choice, with all 23 options present.

`_loadRegistries.fill()` restores from `select.value` and ignores the stash
that `populateDropdowns` documents and honours via `_pickPrev`. That was fixed
and the fix was REVERTED: it did not change the outcome, and an unconsumed
stash proves `fill()` never runs after the restore, so something else resets
the value. Shipping it would have claimed a repair it does not make.

Leading suspect and the measurement trap for the next session are in AR8.10.

### The frontend SHA pins are not reproducible through git

The pins in `test_s07_design_system.py` hash WORKING-TREE bytes; git preserves
only normalised content. With `core.autocrlf=true`, both `git stash pop` and
`git checkout <file>` produced LF-only frontend files this session and failed
the pins, while `git diff` reported no content change whatsoever.

So a fresh clone with a different `core.autocrlf` -- any Linux, Docker or mac
checkout -- fails these pins on content git considers identical. The pins
currently assert a property of one machine's checkout rather than of the
repository. Options: normalise before hashing, add a `.gitattributes` that
fixes the working-tree form, or accept them as Windows-only evidence. Not
decided.

### Owner decisions required

Two save paths write one all-or-nothing registry, and the app.js one deletes a
role whose input is empty -- which is the state the picker leaves after "Add
folder". An ordinary Add-then-Save sequence loses a configured model root.
Recorded with three options in
`Evidence/ar8.3-browser-baseline/sweep-triage.md`; not taken unilaterally.

Four controls reach nothing and each needs a service that does not exist:
Regions, "Save outputs", "Save tree", "Auto-save folder".

---

## 2026-08-24 — AR5.4, the Gallery metadata write

```text
HEAD       8c73801f      tree clean at start; this package is the only change
canonical  5034 tests, OK (4 skipped), exit 0   (4994 before, +40 new)
isolated   the new module is clean under OPERATIONS.md:326's `-I -S -B` runner
           (40 run, OK, 34 skipped). That runner is NOT green tree-wide, and
           one of the failures is by design -- test_gallery_capability.py:170
           asserts Pillow is a hard dependency of this checkout.
mutations  9 of 9 caught, both files restored by sha256
review     25-agent adversarial pass; 3 findings survived refutation, all fixed
GPU        no model was loaded at any point
```

Review: `Evidence/source-review/AR5.4-gallery-metadata-write.md`.
Mutations: `Evidence/ar54-gallery-metadata/mutate_ar54.py`, run recorded in
`mutation-run.txt`.

### The defect: no real generation has ever recorded its parameters

Owner: *"Embed Metadata should apply only to the saved image file. The Gallery
must record metadata locally and link it regardless."*

The owner is right and the toggle is not the cause. `record_generation` had
exactly one caller in the tree and it was dead twice over — guarded on
`metadata["image_sha256"]`, whose only writer is `mock_backend.py:441`, and
sitting inside `_canonical_result`, which only the blocking
`SourceFrontendAdapter.generate` reaches. AR5.3 routed the *autosync*
notification around that guard and left the metadata write inside it.

With Embed Metadata **on**, the scan parses the parameters back out of the PNG
chunk and nothing looks wrong. With it **off**, the file carries nothing, the
row that should have covered it was never written, and the parameters are gone.
**There is no back-fill.** Every image already generated with the toggle off has
lost them irrecoverably; the data was never written anywhere.

### The fix

`_StudioRequestHandler._record_generation_metadata`, on `GET /api/jobs/<id>`
beside AR5.3's notification and before it. It hashes the **delivered file's
bytes** with `gallery_index.content_hash` — the one function the scan also uses
— and calls `record_generation` when the infotext is non-empty. It consults
`embed_metadata` nowhere: that toggle governs the saved file and the database
records regardless of it, in the Extension (`req.embed_metadata` appears only
where `save_kwargs` is built, `studio_api.py:3227`, `:4542`, `:4558`, `:4568`,
and in no Gallery-write condition) and in Neo (`modules/images.py:589`, `:607`
gate the file, and Neo has no database).

**Said precisely, because the first draft of this package said "the database
ALWAYS records" and the Extension does not.** All three of its Gallery writes
are nested inside save branches (`:3215`, `:3279`, `:4513`) and its `else` at
`:3353` writes neither file nor row, so `save_outputs` gates both halves. An
adversarial review of this change found that, and the docstrings, the comment
and the source-review record were corrected rather than defended. Studio has no
`save_outputs` on any request, so the unconditional write matches the Extension
in every state Studio can reach; `ASaveOutputsToggleWouldChangeThisTests` is the
tripwire for the day the dead toggle is wired.

`gallery_index.content_hash` now accepts bytes as well as a path or a PIL image,
so there is still exactly one implementation. `source_api_adapter` stopped
reading `image_sha256` — a `sha256:`-prefixed digest of **SVG source text**,
wrong subject and wrong form, which could not have linked even on the mock.

### The mutation that matters

Replacing the content hash with a digest of the same file's **encoded bytes**
is a plausible edit that yields a valid 64-character hex string, a row that is
written, and a row that never links — with no symptom until an owner opens an
old picture months later. Only the guard that runs a **real scan** catches it.
A test comparing two function calls would not, and neither would any assertion
that the write happened.

### What the adversarial review changed

Three findings survived refutation and all three were acted on rather than
argued with:

- **the oracle was misquoted.** "The database always records" is not what the
  Extension does; `save_outputs` gates its Gallery writes as well. Every
  citation in the first draft started one line BELOW that gate, so the mistake
  looked evidenced. Corrected in three code comments, the test module and the
  review record, plus a tripwire test.
- **one test raised where its neighbours skip.** `content_hash` RAISES without
  Pillow rather than answering "no metadata" — `ImagingUnavailable` is a
  `RuntimeError` and `_require_imaging()` sits one line above the `try` — and
  one test in the new module lacked the guard the rest of it has. Surfaced by
  `docs/studio/OPERATIONS.md:326`'s `-I -S -B` runner, which has no
  site-packages and therefore no Pillow.

  **Stated carefully, because the first reading overclaimed it.** That runner
  is not a green gate today: eight gallery-module failures predate this
  package, and `test_gallery_capability.py:170` fails there deliberately,
  asserting Pillow is a hard dependency of this checkout. So the fix is
  consistency with the module's own `_Imaging` skip, not the restoration of a
  passing gate. Whether that runner is meant to be green at all is an open
  question this package did not answer.
- **"marked seen before the work" was documented and unpinned.** Moving
  `seen.add(job_id)` below the read left 432 tests green. Now one test and one
  mutation say no: a result that has been evicted must be attempted once, not
  on every poll for ever.

### Known limit, inherited from AR5.3 and worse here

The write rides on the browser taking delivery. A client that generates and
never polls gets no row — and here that means losing the parameters, not merely
refreshing late. Closing it means moving the write to publication behind an
injected sink; recorded as an owner decision in the review record §7.1, not
taken unilaterally.

### Not bundled in

- `content_hashes` still does not reach the page on the live path. The
  Extension returns per-image hashes for the Canvas → Gallery detail bridge and
  `app.js:3394` reads them — but that line sits below the lifecycle `return` at
  `app.js:3143`, so it is dead on every real install. Same family as AR5.2's
  seven findings.
- The Extension's `filepath=` pre-insertion of the `images` row, which bypasses
  its watcher debounce. Studio relies on the scan to link, and AR5.3's autosync
  closes that in ≤10 s. A latency difference, not a data-loss one.

---

## 2026-08-24 — V2-01, the shared Brush/Canvas contract schemas

```text
HEAD       449b1ad7      tree clean at start
canonical  5069 tests, OK (4 skipped), exit 0   (5034 before, +35 new)
isolated   the new module is clean under OPERATIONS.md:326's `-I -S -B` runner
           (35 run, OK, 0 skipped -- it needs no Pillow)
mutations  5 of 5 caught, both files restored by sha256
GPU        no model was loaded at any point
```

Review: `Evidence/source-review/V2-01-contract-schemas.md`.
Mutations: `Evidence/v2-01-contracts/mutate_v2_01.py`, run in `mutation-run.txt`.

**Owner decisions taken 2026-08-24:** start at V2 Phase 0 with the oracle closed
in parallel before Gate 2; V2 packages are named `V2-01 … V2-12`, resolving the
collision with the committed `BE1 … BE20`.

### What this is

`07_IMPLEMENTATION_SEQUENCE.md` §2 P0.2 — the versioned types from
`03_SHARED_BRUSH_CANVAS_CONTRACT.md`, as `forge_studio/v2_contracts.py`. An
extension of `contracts.py`, not a replacement, per the P0.1 preflight.

`TargetRef`, `InputAssetRef`, `GenerationSnapshot`, `GeneratedLayerInfo`, the
§10 refusal table with retryability, and the version discipline: one version per
contract, `schema_version` as a field rather than a wrapper, and unknown fields
kept and re-emitted so an intermediate layer cannot silently strip a newer
producer's addition.

### The finding worth carrying

**The asset-facts discipline the bundle asks for is already Studio's, and is
stronger than the bundle assumes.** `contracts.InputAsset:161-165` keeps the
browser's `width`/`height` as *"What the browser SAYS. Verified against the
decode, never believed"*, and `forge_headless/input_assets.py:155-168` performs
that check and refuses a mismatch by name. So V2-01 does not invent it; it names
it.

The real gap is narrower: those decoded dimensions are computed for the refusal
and then **discarded**, so every later consumer that needs an asset's true size
decodes it again. `InputAssetRef` is where they belong. Wiring the producer to
record them is V2-P4.1's, which owns the import path.

### Deferred, with the reason

`NormalizedSample`, `StrokeDescriptor`, `CoverageSink`, `DirtyRegion` and
`StrokeCommit` are **not** implemented. They are the brush kernel's seam and
nothing produces or consumes one until V2-P2.1 builds it; a schema written a
phase early is a schema written against a guess. Recorded as V2-02's first task,
along with a real piece of drift: `canvas-input.js:102 normalize()`, the
oracle's `penSample()` and the bundle's `NormalizedSample` are three spellings
of one sample shape.

### Still open from the preflight

BE17's disposition against the V2 kernel, and the BE0-E dispatch harness, which
needs a foreground browser and must not be run in a hidden pane.

---

## 2026-08-24 — V2-01b, the brush contracts that close BE1

```text
HEAD       7faca56b      tree clean at start
canonical  5122 tests, OK (4 skipped), exit 0   (5069 before, +53 new)
isolated   both V2 modules clean under OPERATIONS.md:326's `-I -S -B` runner
           (88 run, OK, 0 skipped)
mutations  6 of 6 caught, brush-contracts.js restored by sha256
GPU        no model was loaded at any point
```

Review: `Evidence/source-review/V2-01b-brush-contracts.md`.
Mutations: `Evidence/v2-01b-brush-contracts/mutate_v2_01b.py`.

### The authority changed mid-package

The owner supplied `Reference/STUDIO_BRUSH_ENGINE_V2_SPEC_2026-08-24.md`
(sha256 `d6d3c0ae`), which is dated after the 2026-08-23 bundle and supersedes
it for the brush engine. `V2-01-contract-schemas.md` §0 records the
reconciliation. Two consequences:

- **the work breakdown is different.** The bundle is brush+canvas, Phases 0-8,
  storage first. The spec's §22 is brush-only, BE0-BE12, dirty-canvas at BE8
  and recovery at BE11. `V2-01 … V2-12` maps one-to-one onto the spec's BE
  numbers; BE0 is already complete.
- **V2-01 built the wrong half of BE1.** The spec's BE1 wants the BRUSH types
  (§7); V2-01 delivered the Canvas/AI ones and deferred the brush types on the
  reasoning that nothing consumes them yet. That reasoning fits the bundle's
  order and not the spec's, which puts contracts before input deliberately so
  the kernel is built against a frozen contract. Nothing was withdrawn; this
  package pays the rest.

### What closed BE1

`forge_studio/frontend/v2/brush-contracts.js` — §7's `NormalizedSample`,
`StrokeDescriptor`, `TargetRef`, `CoverageSink`/`StrokeTransaction` and
`StrokeCommit` as frozen factories with guards, plus §13's tip support matrix
and §10/§14's preset resolution. **The page does not load it**: §19.7 builds V2
beside Legacy, so it ships as a static asset `index.html` never references.

### The two findings

**Three spellings of one sample already existed.** `canvas-input.js:102
normalize()` measures nineteen fields; `canvas-core.js:1213 noteSample()` keeps
five of them; the oracle's `penSample()` is a third, written this session
against the second. `twist` — the spec's `barrelRotationRad` — is normalised and
read by nothing. That is the cost of writing the contract after the kernel, and
it is why the spec puts BE1 first.

**BE14's preset fix is a convention; the spec asks for a mechanism.** BE14
closed the six leaking fields by making all sixteen presets declare all fifteen
— which fails the day a sixteenth setting is added and one preset forgets it.
§19.4 asks to *"prevent the six known preset-leaking fields by complete V2
preset resolution"*, so V2 resolves every registered setting from ordered
sources and **the previous preset is not one of them**. The leak is
unrepresentable rather than absent. The mutation that proves it restores
`applyBrushPreset`'s merge exactly: every test that checks a preset's own
values still passes, and only the poisoned-state guard sees it.

The one carry-over that is by design — §14's *"Keep Working Size When Switching
MUST default on"* — is declared per setting rather than assumed, because Size
surviving a switch and Ratio surviving it are observationally identical.

### Not closed, and said so

The tip matrix has two halves. *"A declared-live control must change the mark"*
is checkable by pixels; *"the renderer must read the declared value"* is
structural and is not. BE20 measured why that distinction matters: Rotation
Jitter is dead on every round tip yet moves 89.1% of Scatter Dust's pixels,
because it consumes `Math.random()` and shifts the stream feeding the stipple.
A pixel diff would certify it as live. `HONESTY_RULE` names V2-05 (spec BE5) as
the package that owes the second half.

### The comment trap, sixth time

Two word-ban guards in this session failed on their own documentation. The
Python side has `code_only()` over an AST; JS has no stdlib parser, so
`test_v2_brush_contracts._code_only` is a comment-stripping scanner — with its
own calibration tests, because a stripper that returned nothing would make every
guard it serves pass.

---

## 2026-08-25 — V2-02, normalized input and deterministic replay

```text
HEAD       c0c315fa      tree clean at start
canonical  5159 tests, OK (4 skipped), exit 0   (5122 before, +37 new)
isolated   all three V2 modules clean under the `-I -S -B` runner (125 run, OK)
mutations  6 of 6 caught, input.js restored by sha256
GPU        not used. Free at start (LM Studio released it); no V2 row required it
```

Authorization: `Reference/OVERNIGHT_HANDOFF_V2_02_AND_GPU_2026-08-25.md`
(sha256 `8841cdb0`). Review: `Evidence/source-review/V2-02-normalized-input.md`.
Mutations: `Evidence/v2-02-input/mutate_v2_02.py`.

### The verdict, stated as the handoff requires it

**Classification proven; routing deferred.** Nothing here demonstrates palm
rejection or touch navigation, because neither is implemented — whether a finger
paints or pans is BE8's, per §1.1.

### What it does

`forge_studio/frontend/v2/input.js` — one canonical `NormalizedSample` stream,
independent of how the browser grouped its dispatches. Pointer kind classified;
sequence per contact counting accepted samples; `timeUs` clamped non-decreasing
with sequence as the ordering authority; coalesced samples expanded in source
order; predicted events never requested; per-kind sensor fallbacks; and
deterministic record/replay. Not loaded by the page, per §19.7.

### The measurement that carries it

Twelve physical samples, cut into dispatches four ways — as recorded (4+5+3), as
one dispatch of twelve, as twelve of one, and as fours — produce **byte-identical
canonical streams**. That is §3.1's "independent of browser dispatch grouping",
and it is not inspectable: the mutation that resets the sequence counter per
dispatch passes every classification, ordering and sensor test in the suite and
fails only this one.

### Three defects in the inherited seam, repaired in V2 rather than in Legacy

- **A null entry in a coalesced array kills the stroke.** CT2's try/catch wraps
  only the `getCoalescedEvents()` call, so a null throws at `raw.clientX` and
  the rest of the contact is lost. V2 skips the entry.
- **`twist` is measured and read by nothing.** It reaches V2's contract as
  `barrelRotationRad`.
- **Mouse pressure is fixed, not configurable.** §8.2 asks for configurable;
  V2's is a normalizer option defaulting to CT2's 0.5, so no existing stroke
  moves.

### Legacy findings — recorded, NOT fixed (§1.2)

Filed as BE8/migration obligations, in the review record §5:

- **L1** finger input paints and browser pan/pinch is suppressed
  (`touchAction="none"` plus no `pointerType` branch).
- **L2** `isPrimary` is per pointer type, so a primary pen and a primary touch
  coexist — the current filter is not palm rejection.
- **L3** clone, liquify, lasso, magnetic lasso and region paint consume one
  event position rather than the coalesced path.
- **L4** transform, ellipse, crop, gradient and shape are final-position
  operations and correctly do not.

### The comment trap, seventh time — and the last

`_code_only` is now `tests/studio_alpha/_js_source.py`, imported by both V2
suites rather than copied. A second copy of a verified instrument is the same
mistake as a second content-hash function, which AR5.4 spent a package proving.

---

## 2026-08-25 — V2-03, arc-length sampling and exact endpoints

```text
HEAD       fc88247a      tree clean at start
canonical  5191 tests, OK (4 skipped), exit 0   (5159 before, +32 new)
mutations  5 of 5 caught, sampler.js restored by sha256
GPU        not used; no V2 row required it
```

Authorization: overnight handoff §4.3 (Unit B).
Review: `Evidence/source-review/V2-03-arc-length-sampling.md`.
Mutations: `Evidence/v2-03-sampler/mutate_v2_03.py`.

### What it does

`forge_studio/frontend/v2/sampler.js` places marks by distance travelled
through the document, never by how often the browser reported a position. The
model is Legacy's — BE3's debt, BE7's along-travel extent, BE9's endpoint — and
the code is not, per the BE17 precedent.

Same corner path at 30/60/120/240 Hz: **135 marks in identical positions at
every rate.** Same samples as one dispatch, as threes, and as one event each:
identical. Twenty-one 1 px moves and one 20 px move: same eight marks in the
same places.

### One deliberate strengthening over Legacy

**Endpoint exactness is unconditional.** `finishStroke` lets the spacing debt
veto the final mark — *"BE3's spacing debt still governs whether any dab is due
at all"* — while §5.1 requires pen-up represented **exactly** and §20 Input 5
requires a pen-up with backlog to reach it. V2 places the end whenever a mark is
not already there. The short final gap that results is correct: a stroke ends
where the pen lifted.

### Two defects found in this package's own first draft

**`gapPx` reported the debt, not the distance.** At emit time the debt is only
the *remainder* of the gap whenever the previous mark fell in an earlier
segment. A chisel whose gap should have doubled along its axis reported an
unchanged 5 while correctly placing half as many marks — the counts were right
and the field was wrong, which is the worst combination because the number looks
measured. Now measured from the previous mark. **Legacy has the same
construction** (`canvas-core.js:3053` passes `debt` to the stamp), so its value
carries the same under-statement into BE17's overlap divisor. Not repaired
there — Legacy is frozen — and not reproduced here.

**The frequency fixture could not see its own defect.** At a 5.0 px gap every
per-sample distance in the fixture (40, 20, 10, 5 px) is an exact multiple, so
there is never a remainder to carry and severing the residual carry changes
nothing. Measured: severed totals by rate were `[80, 80, 80, 80]` at gap 5.0 and
`[130, 120, 120, 80]` at 3.0. The gap is calibrated to 3.0 with that measurement
recorded beside it. **The mutation harness is what caught it** — the guard had
been reporting a pass over a fixture that could not fail.

### One mutation that was re-pointed rather than made to pass

Severing the stationary branch does **not** produce NaN: at zero distance the
emit loop's own condition is false, so the division is never evaluated. The NaN
property is guaranteed by the loop condition, not by that branch. The mutation
now names what the branch actually protects — the stationary clock §9.4's
Airbrush unit will read.

---

## 2026-08-25 — V2-04, smoothing modes and dynamics

```text
HEAD       7affd40c      tree clean at start
canonical  5228 tests, OK (4 skipped), exit 0   (5191 before, +37 new)
mutations  7 of 7 caught, filters.js and dynamics.js restored by sha256
GPU        not used; no V2 row required it
```

Authorization: overnight handoff §4.4 (Unit C).
Review: `Evidence/source-review/V2-04-smoothing-and-dynamics.md`.
Mutations: `Evidence/v2-04-filters/mutate_v2_04.py`.

### Two modules, and the split is the spec's

§6.1 puts causal filtering **before** arc-length resampling and dynamics
evaluation **after** it, so `v2/filters.js` and `v2/dynamics.js` are separate
rather than one file with two halves.

Raw is identity; Natural cuts a 3 px tremor to 0.63 mean deviation; Stabilized
to 0.15. Corner preservation defaults off and, turned on, passes 4.0 px from the
true vertex where off passes 6.6.

### The measurement worth keeping

**Two event rates over one path agree to exactly zero drift.** The window is an
arc length, so subdividing a segment changes how many terms the centroid sum has
and not what it adds up to. BE9 found the sample-count alternative was *larger*
than the placement defect BE3 fixed — at one event per segment a stroke covered
7,173 pixels where twenty events covered 14,096.

### Three things this package got wrong first, all caught by measurement

- **Strength 0 was an infinitesimal window, not off.** The independence guards
  compared filtered output to input and failed on floating-point rounding rather
  than on filtering. Independence has to include turning one component fully
  off.
- **The rate fixture varied the geometry, not the rate** — two zig-zags of 15 px
  and 3.75 px period rather than one path reported twice. The same trap the
  sampler hit one package earlier. Fixed vertices, subdivided, compared at
  corresponding vertices.
- **The angle mutation survived a fixture that could not fail.** At heading 0
  the factor is 0, and `0 + 0` is indistinguishable from `0 × 0`. Measured at a
  turned heading instead: one rule gives 45°, two give 90°.

### And one mutation that was a no-op

The first attempt at restoring BE9's sample-count window added a count budget
while leaving the arc-length `need` in place — but `take = min(seg, need)` is
already 0 once the arc is exhausted, so the extra iterations contributed
nothing and the mutated engine measured the same zero drift. **A mutation that
changes no behaviour reports as an uncaught defect**, which is the honest
outcome and the reason it was replaced with Legacy's actual `_sampleMean` shape.

### A stale `.pyc` cost a confusing minute

`test_s07_design_system` asserted a manifest hash that was no longer in its own
source. Memory's rule held: a test asserting a value absent from its file means
bytecode, not a lost edit.

---

## 2026-08-25 — V2-05, coverage semantics

```text
HEAD       0ca55d85      tree clean at start
canonical  5263 tests, OK (4 skipped), exit 0   (5228 before, +35 new)
mutations  7 of 7 caught, coverage.js restored by sha256
GPU        not used; no V2 row required it
```

Authorization: overnight handoff §4.5 (Unit D).
Review: `Evidence/source-review/V2-05-coverage-semantics.md`.
Mutations: `Evidence/v2-05-coverage/mutate_v2_05.py`.

### The boundary is the package

§7.4: *"The sink applies target selection/mask exactly once at merge. Brush
coverage is unselected intrinsic coverage."* `stamp` and `sweep` own coverage
and cannot see opacity or selection — asserted by slicing the module and
scanning the generators' code. `merge` owns Opacity once, selection once, and
the erase operation, and generates nothing.

**Selection per contribution is the sharpest failure this prevents.** Measured:
a uniform 50% selection applied once at merge peaks at 127; applied per
contribution it peaks at **24** and the summed mark collapses by 27×.

### BE17's model, implemented deliberately because §10 mandates it

All three subtleties carried with their measurements: the **peak floor** (no
pixel falls below what one pass would lay — the hard cross-section stays flat
127–132 where pure accumulation gives 62→116), the **Flow-100 short-circuit**,
and the **16-bit accumulator**. `profileMean` reproduces its closed form exactly
— 1.0 at hardness 1, 0.5 at hardness 0, which BE17 calls the sanity check for
the whole derivation.

`overlapK` at step 0.3, hardness 0.5 measures **5.0** and `fEff` **0.082549** —
the normalised root that makes five overlapping contributions arrive at 0.35
rather than five times it.

### The measurement worth keeping

**Total coverage is identical at 30/60/120/240 Hz — spread exactly 0**, painted
area identical. §10's *"ordinary non-Airbrush darkness MUST NOT increase merely
because the device reports more events"*, at the coverage layer rather than the
placement layer.

### Flow versus Opacity, and the guard that is a PASS

A worked area separates them decisively (247 against 89 merged). **A single pass
cannot, and must not** — BE17's sentence: *"on a stroke that never touches
itself those are the same number by construction, and a guard that expected a
straight line to separate them would be asserting its own misunderstanding."*
Measured 96 against 89 on one pass, and the suite asserts they are CLOSE.

### Renderer choice lives in the kernel

A sweep is offered only for a hard, round, untextured tip. DiVerdi §2.6.2 warns
a swept contour with constant fill *"loses the natural media quality"* — so
anything whose character comes from texture, scatter or anisotropy stamps. The
two renderers agree on painted area to within 5% on both the straight and
sharp-turn fixtures, which is what says they draw the same shape.

---

## 2026-08-25 — V2-06, the standalone kernel surface

```text
HEAD       8d191fa3      tree clean at start
canonical  5294 tests, OK (4 skipped), exit 0   (5263 before, +31 new)
mutations  5 of 5 caught, scratchpad.js restored by sha256
GPU        not used; no V2 row required it
```

Authorization: overnight handoff §4.6 (Unit E).
Review: `Evidence/source-review/V2-06-scratchpad-and-gate2.md`.
Mutations: `Evidence/v2-06-scratchpad/mutate_v2_06.py`.

### The kernel composes

`v2/scratchpad.js` runs §6.1's pipeline end to end — normalize → filter →
resample → dynamics → coverage → merge — with no compositor under it. Measured:
11 dispatches → 41 samples → 92 marks → 3,676 painted pixels, merged into a test
sink, replayed byte-identically.

Dispatch grouping (1, 3, 7, all-in-one) produces identical coverage **through
the whole kernel**, not just at the sample layer.

### The execution guard is the package

§20 Performance 2 requires a benchmark to refuse timings when work cannot be
proven, and this repository has the scar it is written from — BE0-E's header
records a programme that *"once reported a 0.0 ms dispatch for a stroke that
never reached the engine."*

`run()` returns **`timings: null`** — never zero — with one of four named
refusals, ordered from the earliest failure. All four fire under mutation. A
zero would read as "instant", which is the confident wrong number itself.

**A tap is deliberately not refused.** One contact, one legitimate mark, real
timings. A guard that refuses valid work teaches its reader to ignore refusals.

### What Gate 2 may NOT claim from this

Nothing about Canvas dispatch performance. §4.6 is explicit, and the arithmetic
agrees: the BE0-E baseline measures a real `pointermove` through the shipping
app **including compositing, which is 96–99% of that number** and is not present
here at all. The two are not comparable and this package does not compare them.

**BE0-E has still not been re-run.** It needs a foreground browser window.

---

## 2026-08-25 — U1, bounded V2 coverage

```text
HEAD       dc045fd0      tree clean at start
canonical  5350 tests, OK (4 skipped), exit 0   (5294 before, +56 new)
mutations  7 of 7 caught, coverage.js and scratchpad.js restored by sha256
GPU        not used
```

Authorization: `Reference/HANDOFF_BRUSH_V2_U1_U2_BOUNDED_COVERAGE_WEBGL_2026-08-25.md` §4.
Review: `Evidence/source-review/U1-bounded-coverage.md`.
Mutations: `Evidence/u1-bounded-coverage/mutate_u1.py`.

### V2 was less dirty-aware than the engine it replaces

The Canvas integration gate found it. BE8 gave Legacy's `alphaMapToImageData` a
rect and a retained `ImageData`; V2's `read()` allocated a document-sized array
per call, and `merge`, `totalCoverage` and `paintedPixels` each called it — four
full-document allocations to describe a stroke a few thousand pixels wide.

Integrating that would have been a measurable regression presented as an upgrade.

### `DirtyRegion` is defined here because nothing else defines it

The spec NAMES it (§7.4 `preview(): DirtyRegion`) and never gives it a shape;
§16.3 mandates the behaviour. Half-open, empty is a real state derived from the
numbers, bounds only — tiles derive later without changing the shape.

### Bounds follow the RESULT, not the attempt

`deposit` returns the resulting coverage and the renderers expand only on
non-zero. A Flow-0 dab attempts **208 deposits and dirties nothing**. Expanding
on the attempt would make the region the tip's bounding box — bounded-looking,
and full of pixels that never changed.

Verified against a brute-force non-zero bbox oracle on soft stamp, hard sweep,
soft sweep, Flow-100, all four edges, the corner, a partial stamp, disconnected
islands and a reused buffer.

### Measured in counts, not clocks

The same stroke on three documents:

```text
document      MP     dirty   painted   visited   scratch   base buffer
1024x1024      1.0    4,256    1,308    17,024     4,256      3,145,728
4096x4096     16.8    4,256    1,308    17,024     4,256     50,331,648
6000x4000     24.0    4,256    1,308    17,024     4,256     72,000,000
```

Only the base surface scales, which is inherent and is what `alphaMap` costs too.

### `read()` was removed, not redefined

The old array was document-indexed; the new view is region-local. A quiet
redefinition would have silently misread pixels in any caller that missed the
change, so removal turns that into a TypeError. `COVERAGE_API_VERSION = 2`.

The 35 existing V2-05 tests pass **unmodified** — the evidence that this is a
refactor and not a redesign.

---

## 2026-08-25 — U2, the dirty WebGL presentation path

```text
HEAD       fa8e3165      tree clean at start
canonical  5408 tests, OK (4 skipped), exit 0   (5350 before, +58 new)
mutations  10 attempted, 10 caught; 3 named as NOT exercised
GPU        not used; no AI model loaded
```

Authorization: `Reference/HANDOFF_BRUSH_V2_U1_U2_BOUNDED_COVERAGE_WEBGL_2026-08-25.md` §5.
Review: `Evidence/source-review/U2-dirty-presentation.md`.
Mutations: `Evidence/u2-dirty-presentation/mutate_u2.py`.

**Contains no V2 code.** Shipping files changed: `canvas-core.js`,
`canvas-webgl-preview.js`.

### NOT CLEARED FOR ALPHA

§5.11 lists eleven exit conditions. **Three are unmet and all three need a
browser**: the eleven pixel-oracle comparisons (§5.5), BE0-E and BE0-F (§5.7),
and exercising the rollback once. Every pixel claim in the review is derived
from source and gating conditions, **not from comparing images**.

The owner's approval was explicitly conditional on every gate passing. It is
implemented, unit-tested and mutation-tested; it is not shippable yet.

### A correction to my own gate record

I implied the full flatten and upload happened per FRAME during a stroke. It
does not: nothing in the per-move path bumps `_compositeVersion`, so there is
one upload at pointerdown and one at commit.

The real per-move cost was the wet-stroke conversion scoped to the ACCUMULATED
stroke rectangle, which grows with stroke length — precisely what BE8 §4.2
fixed in the Canvas2D fast path, which is unreachable on the shipping default.

### What was removed per pointer move

```text
_composite2D full layer loop + develop -> _compBuffer     DISCARDED, now skipped
alphaMapToImageData over the accumulated rect             now frameDirty
S.stroke.ctx.clearRect(0, 0, S.W, S.H)                    now bounded
c.drawImage(strokeDrawCanvas, 0, 0)                       now bounded
T.ctx.drawImage(S.stroke.canvas, 0, 0)  at commit         now bounded
```

`window.StudioCanvasImagePreview`, which justified building the discarded
composite, **does not exist** — one occurrence in the tree, in that comment.

### Context loss was not handled at all before this

No listeners, no `isContextLost`. Survivable while every present was a full
upload; fatal with bounded ones, because a restored context has an empty
texture and would receive one small sub-upload forever.

---

## 2026-08-25 — U2-V, browser verification (and one repair)

```text
HEAD       94de3e84      tree clean at start
canonical  5409 tests, OK (4 skipped), exit 0   (5408 before, +1 regression test)
mutations  11 of 11 caught (was 10 of 10; the repair added one)
browser    Chromium in-app pane, WebGL 2.0, DPR 1, 838x632, hidden
GPU        no model loaded, no CUDA context created
```

Authorization: `Reference/HANDOFF_U2V_BROWSER_VERIFICATION_2026-08-25.md`.
Record: `Evidence/source-review/U2V-browser-verification.md`.

### U2 is CORRECTNESS-CLEARED / PERFORMANCE-PENDING

Every pixel, WebGL, context-loss, rollback, bounded-work and synchronous
performance row passes with **zero mismatches**. The foreground scheduling/feel
leg is **NOT RUN — platform-blocked**: `visibilityState` is `hidden` and
`requestAnimationFrame` never fires (eight callbacks, nothing in 30 s). Per the
handoff §2.2 it is not folded into clearance.

### Bounded all the way down, measured

```text
document     dirty px   share     uploaded  flattened  biggest getImageData
512x512         5,850   2.232%       5,850      5,850                 5,850
2048x2048       9,856   0.235%       9,856      9,856                 9,856
4096x4096      18,125   0.108%      18,125     18,125                18,125
6000x4000      17,712   0.074%      17,712     17,712                17,712
```

Zero full uploads. `biggest getImageData` equalling the region is the proof
that no hidden full-document CPU flatten survives.

### BE0-E, like-for-like against fa8e3165 in an isolated worktree

```text
document      composite          full dispatch      p50/move     plotTo
4096x4096     417.1 -> 32.6      544.2 -> 162.0     9.0 -> 2.7   129.8 -> 127.7
6000x4000     458.4 -> 45.7      613.3 -> 199.9    10.6 -> 3.3   153.6 -> 153.3
```

`plotTo` is the control and did not move — which is what makes the composite
column credible rather than a machine difference. **No claim is made for the
commit column**: it is noisy at 24 MP (182.8 pre against 215.5 and 308.0).

### The repair

`invalidatePresentation` left a stale rectangle beside `full: true`. Hit twice
in the browser — a refused region flatten under a non-local Develop setting, and
a WebGL context restore — before being isolated.

Not a live bug: every consumer checks `full` first. Worse in another way: the
invariant was pinned for `markCompositeDirty` and not here, so it shipped
**half-closed**, and a half-closed invariant is more dangerous than an absent
one. The unit probe missed it by only ever calling the function from a clean
state; the browser reached that state because a real refusal path put it there.

### Recorded, not repaired

Publishing a region does not itself wake the preview — it learns from the
`onAfterRedraw` hook. Pre-existing, unchanged by U2, no live path affected.
Fixing it would invert a dependency `canvas-core.js` deliberately does not have.

---

## 2026-08-25 — E0, the eraser preview stops deleting its own layer

```text
HEAD       3383a4d3      tree clean at start
canonical  5427 tests, OK (4 skipped), exit 0   (5409 before, +18 new)
mutations  6 of 6 caught, canvas-core.js restored by sha256
found by   the owner, by hand, after the automated matrix had passed
```

Authorization: `Reference/HANDOFF_ERASER_PREVIEW_AND_U3_CANVAS_ADAPTER_2026-08-25.md` §§4-11.
Review: `Evidence/source-review/E0-eraser-preview.md`.

### Two halves, one unfinished migration

`_composite2D` substituted `S.stroke.canvas` for the active layer during an
eraser stroke -- correct when the eraser PRE-FILLED that canvas with a copy of
the layer. And the wet-stroke conversion was gated on `S.tool === "brush"`, so
the eraser's canvas stayed EMPTY: measured, 0 painted pixels.

`ade5fde7` (CT3d) made the eraser accumulate into the alpha map like the brush
and removed the pre-fill, leaving both halves behind. Present at `8c73801f` --
predates U1, U2 and U2-V.

Measured before the repair, far from the eraser: layer (255,0,0,255), display
(58,58,58,255) -- the checkerboard.

### Why the obvious fix is a worse bug

`_compBuffer` already holds every layer BENEATH the active one by the time the
loop reaches it, so `destination-out` there punches through the whole stack. It
looks perfect on a single layer over the checkerboard -- which is how it would
ship. The erase now goes onto a copy of the ACTIVE LAYER ALONE, in a
stroke-sized scratch, drawn back at the layer's own position with its opacity
and blend mode, in two non-overlapping pieces so a blend mode blends once.

### Browser evidence

```text
during drag, far from eraser     RED(active)     was checker
inside the erased path           BLUE(lower)     reveals the layer below
green stripe over the path       GREEN(upper)    not eaten
canonical lower / upper          alpha 255       never touched
preview vs canonical flatten     [13,0,242,255] both -- byte-identical
selection 100/50/25              removed 240/120/60 -- applied exactly once
one contact                      undo +1, revision +1
2048 square, per move            biggest getImageData 0 -- no readback
```

### A guard that had to be rewritten

The bounding mutation PASSED first time: the guard asserted `S.stroke.dirty`
appeared in the function, and the mutation left that read in place while
replacing the rectangle derived from it. Asserting a name is present is not
asserting it is used. Rewritten to pin the derivation.

### Recorded, not repaired

Cancel advances the document revision -- identically for brush and eraser, so
not E0's, and apparently deliberate (`undo()`: "A NEW revision, never an old one
reused"). Canonical pixels and undo depth are correct either way.

The eraser still ends with a full texture upload: it is a live-fallback tool and
`endLiveCanvasFallback` refreshes the whole texture on release. Pre-existing.

---

## 2026-08-25 — U3, V2 paints on a real Canvas raster layer

```text
HEAD       c9a44a3b      tree clean at start
canonical  5478 tests, OK (4 skipped), exit 0   (5427 before, +51 new)
mutations  11 of 11 behaved as required, including a CONTROL that must not fire
GPU        not used; no model loaded
```

Authorization: `Reference/HANDOFF_ERASER_PREVIEW_AND_U3_CANVAS_ADAPTER_2026-08-25.md` §§12-24.
Review: `Evidence/source-review/U3-canvas-adapter.md`.

**V2 is internally usable on raster layers. Not publicly complete.** The flag is
off at load, has no storage, and there is no public switch anywhere.

### The seam is small because Legacy's stroke slot already fit

`S.stroke.alphaMap` is document-sized coverage; `dirty`/`frameDirty` are its
bounds; `commitStroke` applies selection once, writes the layer, takes ONE undo,
bumps ONE revision and publishes to U2's contract. V2 produces coverage and the
Canvas owns the rest -- so selection-once, one-transaction, E0's eraser preview,
U2's bounded presentation and the Canvas2D fallback all keep holding WITHOUT
being reimplemented.

### Browser evidence, on a clean origin

```text
flag OFF   Legacy 7,115 px, V2 marks 0,   undo +1, revision +1
flag ON    V2 8,060 px, 48 marks, 4 transfers,  undo +1, revision +1
           lower and upper layers untouched
eraser     7,115 -> 1,471, both other layers untouched
undo/redo  8,060 -> 0 -> 8,060
cancel     canonical unchanged, undo +0
touch      0 marks, refused by name
```

V2 paints 8,060 where Legacy paints 7,115 for the same path. Expected: §19 says
not to assert identical aesthetic pixels between two engines.

### Three defects found in my own work

`StrokeFilter.push` returns ONE sample; treating it as a list placed nothing --
and the browser looked almost right because Legacy's opening dab was covering.
The opening contact point was never fed to V2, so strokes started at the first
MOVE -- masked the same way. And a guard asserted the ownership-clearing code
EXISTED; a mutation that removed only the CALL passed.

That is the second time this programme a mutation caught "asserting a name is
present is not asserting it is used" (E0's bounding guard was the first).

The thread: Legacy was quietly covering for V2's mistakes. `_takeOwnership`
removes the thing that was hiding them.

### The CSP guard presentation.py said could not exist

Its comment calls a wrong loader hash "the one failure the source tests cannot
see". It can: the hash is a pure function of the file. Method validated by
reproducing the shipped constant first -- hash the bytes between the tags with
CRLF normalised to LF.

### A methodology finding

Studio's loader appends a CONSTANT `?v=`, so a browser will not pick up an edited
module. Two browser results this session were contaminated and both looked like
product defects. Restart Studio on a fresh port after every source edit; gate
browser measurements behind a freshness assertion.


## 2026-09-13 — tester archive trimmed after owner review

Owner requested removal of development clutter shown in the candidate archive.
Tester packaging now uses a tracked runtime allowlist; a separate source ZIP
preserves the complete checkout. Plans, AGENTS/CLAUDE, root tests, Docker,
legacy WebUI launchers and release scripts stay out of the tester profile.
Keep launch.py: shared_cmd_options imports it, and an extracted startup check
caught empty registries without it. Retain the engine/extension/source trees.
The diagnostics scanner was extracted unchanged into a shared utility so
support reports work with no test suite installed. No inference/Canvas edit.

76 focused checks passed; extracted startup with isolated state/empty model roots
registered 23 samplers, 18 schedulers, 6 latent and 4 image upscalers and 5 detectors.
Evidence: package-readiness-2026-09-13/trim-tests-final.log, trim-smoke.json,
trim-startup.log, SOURCE_REVIEW.md. Final clean builds and hashes are recorded in
TRIM_HANDOFF.md in that evidence envelope. Previous 2007-file artifact is superseded.
Git push is still pending exact source/destination approval after auto-review
rejection; this cleanup does not supply that approval. Real generation and
fresh-machine dependency installation remain unverified.


## 2026-09-13 — empty generation-model folders in the tester ZIP

Owner requested checkpoint, VAE and text-encoder folders under app/models.
Builder now writes explicit empty Stable-diffusion/, VAE/ and text_encoder/
ZIP entries without inspecting local model folders or copying placeholders.
Manifest distinguishes file_count, directory_count and entry_count. Setup and
model-folder selection behavior are unchanged; tester guide explains Settings.
Both archive profiles are rebuilt together. Verification and final artifact
hashes are recorded in Evidence/package-readiness-2026-09-13/MODEL_FOLDERS_HANDOFF.md.
No runtime engine/Canvas changes and no new GPU-generation claim.


## 2026-09-13 — unsupported PATH Python selection repaired

Owner reported first-run rejection of Python 3.10. Launcher used python from
PATH without selecting a compatible installed runtime. NumPy 2.3.5's recorded
Requires-Python is >=3.11, so retain existing dependencies and 3.11–3.13 range.
Bootstrap now probes installed Windows py versions 3.13/3.12/3.11 and re-executes
setup when the initial interpreter is unsupported. Explicit PYTHON is respected;
existing app/venv is preserved, and child failures propagate without retry loops.
Batch uses py if python is absent, and setlocal avoids leaking its variables.
Global Python installations were not inspected during agent work; fixture tests
and an in-workspace subprocess handoff verify the selection path.
Evidence and final hashes: package-readiness-2026-09-13/PYTHON_SELECTION_HANDOFF.md.
Small two-file launcher patch and both archives are refreshed. No inference,
Canvas, config or dependency pin changes. GitHub push remains pending prior approval.


## 2026-09-13 — portable Python/runtime feasibility assessed

Owner requested investigation of bundling Python for testers. Proposal recorded
in docs/studio/PORTABLE_RUNTIME_FEASIBILITY.md before full assembly. Baseline
c2853bf50b56423cac59ce683c1789298127a699. No product/dependency/launcher edits,
no new tester archive and no change to existing release artifacts.

Official CPython 3.13.15 embedded x64 download matches published SHA-256 and starts
privately. Nine key packages import using its interpreter plus explicitly supplied
existing app/venv libraries; this is a compatibility probe, not a self-contained
install or GPU test. 94 resolved dependency artifacts total 2,202,427,243 bytes;
planning total with Python and current app ZIP is about 2.38 GB. Two dependencies
need build-time wheels. Public known-advisory metadata flags ten package versions;
applicability is untriaged. 21 transitive installed versions differ from the dry-run
resolution, so existing-environment success does not certify that fresh resolution.

Next: triage dependencies, approve the exact revised inventory if it changes,
then build and validate a private Windows/NVIDIA prototype. Existing feature
limitations remain. Source-review gate not applicable to this documentation-only
change; no runtime behavior changed. Evidence and reviewed source slices are in
Evidence/portable-runtime-feasibility-2026-09-13/HANDOFF.md. Git push remains
pending the previously requested exact source/destination approval.
