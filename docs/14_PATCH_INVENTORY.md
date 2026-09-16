# Neo Core Patch Inventory

Every Neo-owned file this distribution modifies, why an adapter could not solve
it, and what would let the patch be removed.

**Reconstructed 2026-08-11**, **revised 2026-08-18**, against
`neo-baseline-2026-07-23` (upstream `Haoming02/sd-webui-forge-classic@neo`,
commit `97ff3a40`). The inventory had been empty since the fork; the changes
were already made and commented in place, and this file catalogues them rather
than introducing anything.

```text
scope     modules/ · modules_forge/          21 files, +868 -74
untouched backend/ · ldm_patched/ · extensions-builtin/   entirely
tracking  neo...upstream/neo = 0 0
```

That `backend/`, `ldm_patched/` and `extensions-builtin/` are untouched remains
the load-bearing fact: **no diffusion inference, sampling, memory-management or
model-loading behaviour is patched.** That claim is checked, not asserted — see
`tests/studio_alpha/test_patch_inventory.py`.

### What the 2026-08-18 revision corrected

The 2026-08-11 figures were 15 files, +570 -59, and they went stale within
three days. Two corrections, one of them substantive:

- **Six files were missing.** Four (`profiling.py`, `scripts.py`,
  `scripts_postprocessing.py`, `sd_samplers_common.py`) are more of the same
  deferred-import work and fold into PATCH-001; they were a cataloguing gap
  only.
- **The other two are a THIRD patch family, and it falsifies what this file
  used to claim.** The sentence here read: *"Every change below is about import
  graph or install-time platform choice."* That is no longer true.
  `modules/esrgan_model.py` and `modules/upscaler_utils.py` change **upscale
  behaviour** — see PATCH-003 — and Neo-owned code now imports
  `forge_headless`, which no other patch does.

Upscale is a pixel path, not a diffusion path, so the load-bearing claim above
survives intact. But "Studio patches only the import graph" was the reassuring
one-line summary of this document, and it stopped being accurate on
2026-08-14 without anything noticing.

Nothing in the repository referenced this file or `UPSTREAM_BASE`, so no test
could catch the drift, even though `AGENTS.md` requires updating the inventory
before editing a Neo-owned core file. That gap is now closed by
`test_patch_inventory.py`, which recomputes every number here from git.

---

# PATCH-001 — Gradio and the UI stack leave the inference import path

- **Neo file(s):** `modules/extensions.py`, `modules/script_callbacks.py`,
  `modules/options.py`, `modules/shared.py`, `modules/shared_items.py`,
  `modules/shared_gradio_themes.py`, `modules/ui.py`,
  `modules_forge/main_entry.py`, `modules/infotext_utils.py`,
  `modules/processing.py`, `modules/profiling.py`, `modules/scripts.py`,
  `modules/scripts_postprocessing.py`, `modules/sd_samplers_common.py`;
  new files `modules/infotext_core.py`, `modules/resolution.py`
  (the last four were catalogued 2026-08-18; they are the same deferred-import
  change reaching four more modules, not a new kind of edit)
- **Studio owner module:** `forge_headless/` — the whole headless boundary
- **Reason:** an adapter cannot solve an import graph. Studio imports Neo's
  generation code without standing up Neo's UI, but `modules.processing`
  transitively reached Gradio through `modules.ui` (for `sRound`),
  `modules.infotext_utils` (for `quote`), `modules.script_callbacks` (for a
  `Blocks` type annotation), `modules.options` (for component factories) and
  `modules.shared` (for a theme object). Importing Gradio on that path is not
  merely slow: it is the boundary the product is built around, and
  `test_import_boundaries` exists to keep it.
- **Minimal hook/change:** four kinds, all additive —
  1. **deferred imports** — `scripts` inside `Extension.list_files`,
     `processing` inside `forge_model_reload` and `main_entry`;
  2. **`TYPE_CHECKING`** — `Blocks` is only ever an annotation and the module
     already has `from __future__ import annotations`, so the name is never
     evaluated;
  3. **lazy component factories** — `options.py` wraps `gr.HTML` and `FormRow`
     in `_gradio_html` / `_form_row`, called only by the settings UI;
  4. **extraction with compatibility re-export** — `quote`/`unquote` move to
     `infotext_core.py` and `sRound` to `resolution.py`; `infotext_utils` and
     `ui` re-export them, so every upstream caller keeps working.
- **Behavioral impact:** none intended on any Neo path. The UI still builds; the
  same functions are still reachable under their original names. What changes is
  *when* Gradio is imported.
- **Feature flag:** none. A flag would mean maintaining both graphs.
- **Tests:** `tests/studio_alpha/test_import_boundaries.py` (asserts a fresh
  `forge_studio` import stays clean of `gradio`/`modules`/`modules_forge`/`webui`),
  `test_device_capability.py` (AST ban on torch/gradio/numpy in `forge_studio`),
  `test_generation_import_order.py` (the sd_models ↔ processing ordering).
- **Upstream conflict likelihood:** **Medium.** The changes are small and mostly
  additive, but they touch nine files upstream edits often. `processing.py` and
  `shared.py` are the likely conflict sites.
- **Upstream PR candidate:** **Yes**, and this is the strongest candidate in the
  inventory. "Do not import the UI to quote a string" is a defensible upstream
  improvement independent of Studio, and the extraction pattern keeps every
  existing import working.
- **Introduced in:** pre-dates this session; catalogued 2026-08-11.
- **Last reviewed against upstream:** 2026-08-11 (`neo...upstream/neo = 0 0`).
- **Removal criteria:** upstream accepts the extraction, **or** Studio stops
  importing `modules.processing` at all — which would mean a Studio-owned
  sampler path, explicitly not the plan (Neo remains the behaviour oracle).

### Sub-note: the `sd_models` ↔ `processing` cycle

`modules/sd_models.py` imported `processing` at module scope while
`processing.py` imported `apply_token_merging`/`forge_model_reload` back from
`sd_models`. The cycle was latent upstream because Neo's own load order hid it;
Studio reaches `sd_models` first and exposed it. The fix is a function-local
import in `forge_model_reload`, with the assignment
`processing.opt_f = ...` left where it is.

**This is a legacy compatibility assignment and both call sites say so.** A
`RuntimeContext` should own `opt_f` rather than a module global. Until then,
`test_generation_import_order.py` pins the ordering, including
`OptFPreservationTests`, which asserts the value still reaches processing and
falls back to 8 for a non-integer ratio.

---

# PATCH-002 — Platform-aware install-command selection

- **Neo file(s):** `modules/launch_utils.py`; new file
  `modules/platform_selection.py` (300 lines, stdlib-only)
- **Studio owner module:** none — this runs at install time, before Studio exists
- **Reason:** Neo's launcher assumed a CUDA wheel. The book makes Windows, macOS
  and Linux first-class targets *during development*, not at release cleanup, and
  an installer that silently selects a CUDA build on an Apple Silicon machine is
  not a cross-platform product. This cannot be an adapter: the decision happens
  before any Studio code is importable.
- **Minimal hook/change:** the selection is extracted into a **pure** module that
  imports nothing beyond the standard library, performs no side effects, invokes
  no pip, spawns no subprocess, touches no network, and mutates no environment.
  Every input is injected, so the whole platform matrix is testable on one
  machine. `launch_utils.py` calls it and raises `SystemError` on an
  unrecognized platform rather than falling back to CUDA.
- **Behavioral impact:** Windows and Linux keep the retained CUDA default
  byte-for-byte. macOS gets a plain PyPI build with no CUDA suffix or index. An
  unrecognized platform now **fails loudly instead of installing the wrong
  thing**. Owner overrides via environment are trusted verbatim, unchanged.
- **Feature flag:** environment overrides are the escape hatch and pre-date this.
- **Tests:** the module's own suite in `tests/studio_alpha/` covering the
  platform/machine matrix. Note what it does NOT claim: selecting a command is
  not a compatibility assertion — the module's own docstring says it does not
  assert the result runs, that a wheel exists, or that a device is present.
- **Upstream conflict likelihood:** **Low.** `platform_selection.py` is a new
  file; the `launch_utils.py` change is a contained call site.
- **Upstream PR candidate:** **Maybe.** Useful upstream, but it encodes this
  distribution's supported-platform policy, and upstream may want a different one.
- **Introduced in:** pre-dates this session; catalogued 2026-08-11.
- **Last reviewed against upstream:** 2026-08-11.
- **Removal criteria:** upstream adopts platform-aware selection, or the
  distribution ships prebuilt per-platform environments and stops resolving
  Torch at launch.

---

# PATCH-003 — GPU tile compositing, and the memory it was costing

- **Neo file(s):** `modules/esrgan_model.py` (+148),
  `modules/upscaler_utils.py` (+58)
- **Studio owner module:** `forge_headless/upscale_preflight`,
  `forge_headless/job_options`
- **Introduced in:** `57d55a93`, `85c8b905`, `46838537`, `d5e5c861`,
  `18b53ac8` — all 2026-08-14. **Catalogued 2026-08-18**, four days late.
- **This is the family that breaks the old summary.** PATCH-001 and PATCH-002
  change *when* something is imported and *what* gets installed. This one
  changes what the upscaler does to the owner's pixels, and it is the only
  patch where **Neo-owned code imports Studio-owned code**.

### Why an adapter could not solve it

The tile loop is three Neo frames below anything Studio owns, and the
conversion being fixed is a private helper called from inside it
(`upscale_with_model_gpu`). There is no seam to wrap: an adapter can choose
*whether* to call the upscaler, not how the upscaler converts a frame it
already holds.

The decision also cannot be returned upward for the same reason, which is why
`_record_decision` (`esrgan_model.py:35`) writes into the job scope and the
port reads it back out after the job finishes.

### The three changes

1. **A float copy the size of the frame, removed.** `tensor_bgr_to_pil_rgb`
   did its channel swap first, and `tensor[:, [2, 1, 0]]` is advanced indexing,
   so it copied the whole frame in float *while the accumulator it viewed was
   still alive*. At a 2560 base that is 1200 MiB, and it made the conversion —
   not the tile loop — the peak of the entire upscale. The swap now happens
   last, on the host. Scaling, rounding and clamping are elementwise, so they
   commute exactly with a permutation of the channel axis: same operations,
   same operands, same order per element, only the destination index moves.
2. **A mode conversion the GPU path was missing.** The CPU path converts before
   building its array; without the same conversion a grayscale, palette or
   16-bit image reached `permute()` with two dimensions and the upscale died.
   `np.array` rather than `np.asarray`, because PIL hands out a read-only
   buffer and torch warns on the owner's console about writing to it.
3. **A preflight, and an effective-versus-requested decision.**
   `upscale_with_model` now takes `composite_on_gpu` as the *effective*
   choice. The owner's preference alone cannot know whether a given frame
   fits, so the caller preflights the device against the working set and
   passes the answer; `None` preserves the old option-decides behaviour.

### Behavioral impact

**Deliberate and owner-visible.** Peak memory during upscale drops by roughly
the frame size in float. Output is intended to be identical — the reordering is
algebraically neutral — but this is a pixel path, so that is a claim requiring
image comparison rather than reasoning, and the commits carry it.

Non-RGB inputs that previously **failed** now succeed. That is a fix, and it is
also a behaviour change against upstream.

- **Feature flag:** yes — the composite toggle, with the launch flag as a
  ceiling (`18b53ac8`). `composite_on_gpu=None` restores upstream's behaviour.
- **Upstream conflict likelihood:** **Medium-high.** `upscaler_utils.py` is
  small and actively developed upstream, and both edited functions are ones a
  refactor would touch. The `forge_headless` import is the part upstream can
  never take.
- **Upstream PR candidate:** **Partly.** Changes 1 and 2 are defensible
  upstream on their own merits and carry no Studio dependency. Change 3 is
  Studio policy and is not.
- **Removal criteria:** upstream adopts the conversion order and the mode
  guard, and Studio moves the preflight decision to a seam that does not
  require Neo-owned code to import `forge_headless`. The import is the part of
  this patch worth being uncomfortable about.

---

## What this inventory does NOT yet satisfy

Recorded so the gap is not mistaken for completeness:

- **No upstream-merge rehearsal has been performed.** Conflict likelihoods above
  are read from the diffs, not from an attempted merge. The release gates call
  for at least two rehearsals.
- **`neo...upstream/neo = 0 0` proves the tracking branch matches upstream. It
  does not mean Studio HEAD is Neo-identical** — `neo...HEAD` was 0/324 at `91c04492`. The two
  comparisons have been confused before and the distinction is load-bearing.
  (That figure read 249 here and in `UPSTREAM_BASE` until 2026-08-18; it is now
  recomputed by a test rather than transcribed.)
- **Rollback is inventoried, not exercised.** Nothing here has been reverted and
  re-tested; removal criteria are stated, not demonstrated.
- The deferred-import fixes are pinned by tests that assert import ORDER. They
  do not prove a future contributor cannot reintroduce a module-scope import in a
  file no current test imports.
