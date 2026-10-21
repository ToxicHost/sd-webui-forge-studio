# Neo Core Patch Inventory

Every Neo-owned file this distribution modifies, why an adapter could not solve
it, and what would let the patch be removed.

**Reconstructed 2026-08-11**, **revised 2026-08-18**, against
`neo-baseline-2026-07-23` (upstream `Haoming02/sd-webui-forge-classic@neo`,
commit `97ff3a40`). The inventory had been empty since the fork; the changes
were already made and commented in place, and this file catalogues them rather
than introducing anything.

```text
scope     modules/ · modules_forge/ · backend/          39 files, +1840 -78
backend/  new model-family files, plus 2 registration points (PATCH-004),
          plus one upstream backport (PATCH-005), plus one sampler line
          (PATCH-006, a no-op for image latents)
untouched ldm_patched/ entirely; extensions-builtin/ except PATCH-005
tracking  neo...upstream/neo = 0 0
```

**The load-bearing claim, rewritten 2026-10-19.** Until then it read: no
diffusion inference, sampling, memory-management or model-loading FILE is
patched, because `backend/` was untouched. PATCH-004 (MOSS-Image) ended that:
a model family Neo does not have can only live in `backend/`. The claim is now
narrower and still checked rather than asserted
(`tests/studio_alpha/test_patch_inventory.py`): **no EXISTING family's
inference, sampling or memory-management file is patched.** `backend/` grows
by new files; the only existing backend files edited are the loader's dispatch
(`backend/loader.py`) and the bundled-tokenizer table
(`backend/huggingface/__init__.py`), where a family has to be registered.

**Amended 2026-10-20 (PATCH-006), and still checked.** One existing sampling
file is edited: the mask repeat in `get_area_and_mult`
(`backend/sampling/sampling_function.py`), so a video-shaped latent can take a
regional mask. For every image latent the edited line builds the SAME tensor
as before -- a test proves it -- so the claim now reads: **no existing
family's image-latent inference is changed**; Anima-shaped latents gain mask
support they did not have.

**Since 2026-10-11 it is a claim about files, not about behaviour.** One
Studio-owned runtime adapter, ADAPTER-001 (end of this document), changes
Anima's text processing and cross-attention **when a job runs in "Studio"
emphasis mode**. It edits no Neo file. Every wrapper calls Neo's original
unchanged otherwise, so "Neo-compatible" is Neo's code path. A second,
ADAPTER-002 (2026-10-12), only OBSERVES: it reads cross-attention on the
side during a job's main pass and changes nothing the model computes. A
third, ADAPTER-003 (2026-10-18), is installed only on Apple Silicon (MPS) and
changes load-time dtypes there; on CUDA it wraps nothing.

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
  byte-for-byte. macOS gets a plain PyPI build with no CUDA suffix or index;
  since MAC1 (2026-10-18, `839d184d`, +8 lines) Apple Silicon is pinned to the
  CUDA pin's release, `torch==2.11.0 torchvision==0.26.0`, verified on PyPI. An
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

# PATCH-004 — A model family Neo does not have: MOSS-Image

- **Neo file(s), modified (registration only):** `backend/loader.py` (the
  engine in `possible_models`, the `Qwen3_5Model` text encoder and
  `MOSSImageTransformer2DModel` dispatch, and the Qwen3.5 branch of
  `replace_state_dict`), `backend/huggingface/__init__.py` (the bundled
  tokenizer), `modules_forge/presets.py` (the `moss` preset),
  `modules_forge/packages/huggingface_guess/detection.py` (detection by keys),
  `modules_forge/packages/huggingface_guess/model_list.py` (`MOSSImage`).
- **Neo file(s), added:** `backend/nn/moss.py`, `backend/nn/llm/qwen35_text.py`,
  `backend/text_processing/moss_engine.py`, `backend/diffusion_engine/moss.py`,
  `backend/huggingface/moss.tokenizer.json.xz`, and the config folder
  `backend/huggingface/OpenMOSS-Team/MOSS-Image-Base/`:
  `backend/huggingface/OpenMOSS-Team/MOSS-Image-Base/model_index.json`,
  `backend/huggingface/OpenMOSS-Team/MOSS-Image-Base/scheduler/scheduler_config.json`,
  `backend/huggingface/OpenMOSS-Team/MOSS-Image-Base/text_encoder/config.json`,
  `backend/huggingface/OpenMOSS-Team/MOSS-Image-Base/tokenizer/tokenizer_config.json`,
  `backend/huggingface/OpenMOSS-Team/MOSS-Image-Base/transformer/config.json`,
  `backend/huggingface/OpenMOSS-Team/MOSS-Image-Base/vae/config.json`.
- **Studio owner module:** `forge_headless/family_shift` (shift 5) and
  `forge_headless/backend_bootstrap` (runs Neo's tokenizer unpack, which Studio
  had never run).
- **Introduced in:** 2026-10-19 (MS1). Source review:
  `Evidence/source-review/MS1-moss-image.md`.
- **This is the family that ends `backend/` being untouched.** Neo had no
  MOSS-Image when it was released (2026-10-16); the owner asked for it. A model
  family is a network, a text encoder, an engine and a loader entry, and Neo
  keeps all four in `backend/`. Every file of the family is NEW; the only
  existing backend files edited are the two registration points, and
  `test_patch_inventory.py` now pins exactly that.

### Why an adapter could not solve it

An adapter wraps a callable. There is nothing to wrap: the transformer, the
Qwen3.5 language model (Neo has only its vision half) and the engine did not
exist. Registering them from Studio-owned code would mean replacing
`possible_models`, `load_huggingface_component` and `replace_state_dict` at
runtime -- three monkeypatches on the model loader, invisible to `git diff`,
to avoid two visible lines of dispatch. Neo itself adds a family the way this
does (compare Krea 2, `2d5fca41`, and ERNIE-Image, `037db03b`).

### Behavioral impact

**None for any existing family.** The loader branches are keyed on MOSS's
class names and on keys only MOSS's files have (`shared_block_modulation`,
`video_head_modulation`; `linear_attn.A_log` for the Qwen3.5 file).
Detection runs before ERNIE's, which no MOSS key matches either way. MOSS's
Qwen3.5 is stored in bf16 when its file is (Neo's text-encoder default is
fp16); that branch is MOSS's own, so no other encoder's dtype moves.

The tokenizer unpack is Neo's own startup step. It gives ERNIE-Image, Wan,
Neta Lumina and PiD the `tokenizer.json` they could not load without; FLUX.2
Klein's fast tokenizer now reads the unpacked file instead of building itself
from vocab/merges, with identical ids (checked, MS1). Anima's slow tokenizer
never reads it.

- **Feature flag:** no; a MOSS checkpoint is the switch.
- **Upstream conflict likelihood:** **Medium.** The added files cannot
  conflict. `loader.py`'s dispatch lists and `model_list.models` are edited
  by every upstream family addition, so a future upstream merge will conflict
  there mechanically -- and if upstream adds MOSS-Image itself, Studio's copy
  should be dropped in favour of upstream's.
- **Upstream PR candidate:** **Yes**, except the l2norm choice may be
  argued: it follows transformers (what OpenMOSS's pipeline runs), not
  ComfyUI.
- **Removal criteria:** upstream Neo ships MOSS-Image; Studio then keeps only
  `family_shift`'s entry.

---

# PATCH-005 — Upstream backport: Anima Control-LLLite on 40- and 52-block models (Neo `bd1d015`)

- **Neo file(s), modified:** `backend/nn/anima.py` (the `BLOCK_MAPPINGS`
  table, `Final` import),
  `extensions-builtin/sd_forge_controlllite/lib_controllllite/lib_controllllite_anima.py`
  (`re`, the `BLOCK_MAPPINGS`/`Anima` import, `_SAVED_BLOCK_RE`,
  `MappedModule`, `map_blocks`),
  `extensions-builtin/sd_forge_controlllite/scripts/forge_controllllite.py`
  (the LLLite net is built on `map_blocks(dit, state_dict)`).
- **Neo file(s), added:** none.
- **Studio owner module:** none; `forge_headless/controlnet_port.py` (CN1)
  drives Neo's ControlNet and registers Control-LLLite unchanged.
- **Introduced in:** 2026-10-20, at the owner's request ("Why not just bring
  the fix now?"). Source: upstream
  `Haoming02/sd-webui-forge-classic@bd1d015` (2026-10-06), after the pinned
  `97ff3a40`. Review: `Evidence/source-review/CN1-controlnet.md`.
- **Not applied verbatim, because upstream moved underneath it.** The commit
  does not apply to the pinned files (their imports changed between
  2026-07-23 and 2026-10-06). Its additions are copied exactly; the type hints
  and docstring edits it also made are not, and its fourth file
  (`extensions-builtin/sd_forge_lora/networks.py`, moving the LoRA mapping
  tables into `BLOCK_MAPPINGS`) is not, because the pinned LoRA loader has no
  block mapping at all -- that arrived upstream separately.

### Why an adapter could not solve it

The mapping has to sit between Neo's Anima DiT and `ControlNetLLLiteDiT`'s
constructor inside `ControlLLLiteAnimaPatcher.process_before_every_sampling`.
Doing that from Studio code means replacing the patcher's method at runtime,
a monkeypatch on Neo's ControlNet invisible to `git diff`, to avoid three
visible files that are upstream's own code and leave on the next sync.

### Behavioral impact

**None on a 28-block Anima model** (every Anima-LLLite release, and every
Anima checkpoint on the development machine, is 28 blocks): `map_blocks`
returns the model unchanged when the counts match. On a 40- or 52-block
Anima model a 28- or 40-block LLLite file now attaches to the blocks
upstream's tables map it to; before, the LLLite modules were built on the
model's own block count, which the file's weights do not match (not
reproduced here: no 40- or 52-block Anima model is available).

- **Feature flag:** no.
- **Upstream conflict likelihood:** **None expected.** The code is upstream's;
  a sync brings the same lines (plus the hints and the LoRA change).
- **Upstream PR candidate:** not applicable -- it is upstream's.
- **Removal criteria:** the next upstream sync past `bd1d015`; drop this
  entry then.

---

# PATCH-006 — Masks on video-shaped latents (regional prompting on Anima)

- **Neo file(s), modified:** `backend/sampling/sampling_function.py`
  (`get_area_and_mult`: the mask's repeat counts).
- **Neo file(s), added:** none.
- **Studio owner module:** `forge_headless/regional.py` (RG1), whose masked
  regions are Neo conds with a `mask`.
- **Introduced in:** 2026-10-20, Regional prompting on every family (owner's
  decision "Both"). Review: `Evidence/source-review/RG0-regional-prompting.md`.

### Why an adapter could not solve it

Neo repeats a cond's mask with four counts,
`mask.unsqueeze(1).repeat(B, C, 1, 1)`, which fails on a five-dimensional
latent (`[B, C, T, h, w]`: Anima, and other video-shaped families). The repeat
happens inside `get_area_and_mult`, called per cond per step from
`calc_cond_uncond_batch`; replacing that function from Studio code would be a
monkeypatch on Neo's sampler invisible to `git diff`.

### Behavioral impact

The counts are now `(B, C, *[1] * (mask.ndim - 2))`. For a `[B?, h, w]` mask on
an image latent that is `(B, C, 1, 1)`, the same four counts, so the tensor
and every image are unchanged (`tests/studio_alpha/test_rg1_regional.py`).
A `[1, T, h, w]` mask on a five-dimensional latent now works. Nothing in Neo's
own prompt path sets `mask`; only Studio's regional runner does.

- **Feature flag:** no.
- **Upstream conflict likelihood:** **Low.** One line, in a function upstream
  rarely touches.
- **Upstream PR candidate:** yes.
- **Removal criteria:** upstream generalises the repeat; drop this entry then.

---

# Runtime adapters: Neo behaviour changed without editing a Neo file

A runtime adapter wraps a Neo callable from Studio-owned code. It never shows
up in `git diff`, so it is catalogued here by hand, and its test pins the
signatures it wraps.

# ADAPTER-001 — Anima's "Studio" emphasis mode (EM1)

- **Studio owner module:** `forge_headless/anima_emphasis.py`, installed once
  by `bootstrap_backend` after the samplers.
- **Wrapped (Neo `97ff3a40`, unchanged files):**
  - `backend/text_processing/anima_engine.py`
    `AnimaTextProcessingEngine.tokenize_line(self, line)`;
  - `backend/nn/anima.py` `SelfCrossAttention.forward(self, x, context,
    rope_emb, transformer_options)`, cross-attention instances only;
  - `modules/sd_samplers_common.py` `Sampler.launch_sampling(self, steps,
    func)`.
- **Registered:** an `EmphasisStudio` option ("Studio", a subclass of
  `EmphasisOriginal`) in `backend/text_processing/emphasis.options`.
- **Why an adapter, not a file edit:** `AGENTS.md`'s core edit rule asks why
  an adapter cannot solve it, and here it can. Neo has no seam for an
  attention bias (`torch_attention_op` passes no mask), but wrapping the
  three callables carries the whole behaviour. The research ran it exactly
  this way (`Evidence/anima-weights-2026-10-11/`).
- **Behavioural impact:**
  - **Only when a job's scope sets `emphasis="Studio"`** (EM1b sets it on
    Anima jobs).
  - The prompt is tokenized once (a majority rule at weight boundaries),
    BREAK is dropped, and B adds log(w) to the word's attention logits as a
    float SDPA mask.
  - Unweighted prompts are pixel-identical (measured in the research).
  - Otherwise every wrapper calls Neo's original.
- **Feature flag:** the mode itself. "Neo-compatible" is no override at all.
- **Failure mode:** `install()` checks the three signatures first. A
  mismatch (upstream changed) refuses installation, wraps nothing, and makes
  Studio mode unavailable, so jobs run Neo-compatible. It is never a guess.
- **Tests:** `tests/studio_alpha/test_anima_emphasis.py`, which includes the
  signature pins against Neo's source.
- **Source review:** `Evidence/source-review/EM1-anima-emphasis.md`.
- **Upstream conflict likelihood:** **Low for merges** (no file is edited),
  **medium for drift**. An upstream rename or signature change to any of the
  three callables disables Studio mode, loudly, until the adapter is
  updated.
- **Removal criteria:** Neo gains a supported attention-bias seam (for
  example a mask or override in `transformer_options`) and an Anima
  tokenization option, or Studio drops the mode.

# ADAPTER-002 — the attention capture behind heatmaps and word masks (HM1)

- **Studio owner module:** `forge_headless/attention_capture.py`, installed
  once by `bootstrap_backend` AFTER ADAPTER-001, so its Anima `forward`
  wrapper sits outside that one and sees the same per-pass bias.
- **Wrapped (Neo `97ff3a40`, unchanged files):**
  - `modules/sd_samplers_common.py` `Sampler.launch_sampling(self, steps,
    func)` (opens a session for a pass the port marked) and
    `Sampler.callback_state(self, d)` (counts steps);
  - `backend/nn/anima.py` `SelfCrossAttention.compute_qkv(self, x, context,
    rope_emb)` and `SelfCrossAttention.forward(self, x, context, rope_emb,
    transformer_options)`, cross-attention instances only;
  - `backend/nn/unet.py` `CrossAttention.forward(self, x, context, value,
    mask, transformer_options)`, cross-attention calls only.
- **Why an adapter, not a file edit:** Neo has no seam that exposes attention
  probabilities. The wrappers compute them on the side from q and k, which is
  how the research measured every number the capture keeps.
- **Behavioural impact:**
  - **None on the picture.** Probabilities are computed beside the model;
    nothing feeds back. Pixel identity with the capture on and off was
    measured in the research and is re-checked by the GPU acceptance.
  - Only a pass the port MARKS (`p._studio_attention_capture`, the job's
    main pass) is captured; Neo's Hires pass (`is_hr_pass`), Studio's
    img2img Hires and Auto Detail are not. Cost measured at about +0.34 s
    (Anima) and +0.48 s (SDXL) per image.
  - The result is a sidecar file: pending under the request id
    (`<state>/sidecars/pending/`), then adopted by presentation under the
    picture's content hash (`<state>/sidecars/attention/<h[:2]>/<h>.attn.npz`)
    and named in `image_metadata.attention_path` (`forge_studio/attention_store.py`).
- **Feature flag:** the owner's Settings toggle "Keep word heatmap data"
  (preference `attention_capture`, on by default; HM2), snapshotted
  server-side into `attention_capture_requested`. The port also captures
  only when it has a sidecar root (`LoadConfiguration.sidecar_root`, set by
  `launch.py`).
- **Failure mode:** `install()` checks the five signatures first; a mismatch
  wraps nothing and the capture is unavailable. Inside a pass, any failure
  drops that capture and logs it; it never reaches generation.
- **Tests:** `tests/studio_alpha/test_attention_capture.py`, which includes
  the signature pins against Neo's source.
- **Source review:** `Evidence/source-review/HM1-attention-capture.md`.
- **Upstream conflict likelihood:** **Low for merges** (no file is edited),
  **medium for drift**. A signature change disables the capture, loudly; a
  change to how Anima pads its context or orders its image tokens would
  change the maps silently, which the GPU acceptance's map check exists for.
- **Removal criteria:** Neo exposes attention probabilities through a
  supported hook, or Studio drops heatmaps and word masks.

# ADAPTER-003 — safe dtypes and honest failures on Apple Silicon (MAC1)

- **Studio owner module:** `forge_headless/mps_adapter.py`, installed once by
  `bootstrap_backend` after ADAPTER-002, and **only when Neo's memory manager
  is in MPS mode** (`memory_management.mps_mode()`). On CUDA it wraps nothing.
- **Wrapped (Neo `97ff3a40`, unchanged files):**
  - `backend/loader.py` module global `load_torch_file` (imported from
    `backend/utils.py`; called by `_load_unet`, `_load_diffuser`,
    `split_state_dict`);
  - `backend/memory_management.py` `vae_dtype(device=None,
    allowed_dtypes=None)`;
  - `backend/memory_management.py` `discard_cuda_async_error()` (replaced on
    MPS by a Metal synchronize that never raises).
- **Why an adapter, not a file edit:** each gap is reached through a module
  attribute Neo looks up at call time, so a wrapper carries the whole fix.
- **Behavioural impact (MPS only):**
  - plain fp8 tensors are upcast as files load, to bf16 where Neo computes in
    bf16 (macOS 14+) else fp16. Neo would keep them fp8 (`loader.py:440`) and
    fail at the first move to Metal, which has no float8;
  - scaled fp8 files (`scaled_fp8`, `comfy_quant`, `_quantization_metadata`,
    `scale_weight`) are refused with `MPS_SCALED_FP8_UNSUPPORTED` and a
    sentence, instead of being upcast without their scales;
  - `vae_dtype` never returns fp16 (only reachable through a flag Studio never
    passes); fp16 becomes bf16 or fp32;
  - an OOM recognised by message no longer calls `torch.cuda.synchronize()`,
    so Neo's tiled-VAE retry runs and the real error survives.
- **Feature flag:** the device. There is no other switch.
- **Failure mode:** `install()` never raises; a missing name (upstream rename)
  leaves the backend unwrapped and logs "Apple GPU adapter not installed".
- **Tests:** `tests/studio_alpha/test_mac1_mps_device.py` (faked engine,
  real safetensors fp8 file, and source pins for every Neo line relied on).
- **Source review:** `Evidence/source-review/MAC1-mps-device.md`.
- **Upstream conflict likelihood:** **Low for merges** (no file is edited),
  **medium for drift** (a rename disables it, loudly).
- **Removal criteria:** Neo upcasts fp8 for devices without fp8 and guards
  `discard_cuda_async_error` itself.

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
