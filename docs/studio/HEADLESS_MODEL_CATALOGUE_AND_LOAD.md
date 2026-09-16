# Headless Model Catalogue and Load

```text
Phase 2A validates model selection and loader handoff.
It does not open or load a checkpoint.
```

How Forge Studio enumerates model candidates and validates a load request
through the owned headless boundary, with Gradio absent and no device touched.

---

## 1. Shape

```text
forge_studio                 forge_headless                    retained Forge
------------                 --------------                    --------------
StudioApplication
  .list_models()   -->  HeadlessBackendAdapter
  .load_model()           .list_models()  --> ForgeHeadlessRuntime
  .runtime_status()        .load_model()        .configure_catalogue_root()
                                               .list_models()
                                               .load_model()
                                                     |
                                               ModelCatalogue
                                                     |
                                               LoadRequest (frozen)
                                                     |
                                               LoaderPort
                                                     |
                                        PolicyGatedLoader -> refuse    [Phase 2B: real loader]
```

`HeadlessBackendAdapter` lives in `forge_headless/`, not `forge_studio/`, because
`forge_studio` is purity-locked against importing `modules`, `modules_forge`,
`backend`, `torch`, `torchvision`, `gradio`, and `numpy`. The dependency points
one way: `forge_headless` may import Studio contracts, never the reverse.

## 2. Configuration

```text
STUDIO_BACKEND        mock | forge-headless      default: mock
NEO_UI_COMPATIBILITY  enabled | disabled        default: disabled
STUDIO_MODEL_ROOT     one explicit directory    default: none
```

Three independent selectors. `STUDIO_MODEL_ROOT` has **no default**, so nothing
is ever discovered automatically, and `forge_studio.backend_selection` returns it
verbatim without any filesystem access at all. Every containment decision belongs
to `ModelCatalogue`.

During this milestone the root must be inside `Studio-Standalone\`. Widening that
is an explicit owner-approved configuration change, not a code edit — the
restriction is a single check with its own error code, deliberately not spread
through the enumeration logic.

## 3. What the catalogue does and does not do

Does: list one directory level (two with `recursive=True`), `stat` each entry,
classify its format from the retained loader's own dispatch, mint an opaque
root-scoped id, and sort deterministically.

Does **not**: open, read, mmap, hash, parse, or deserialize any file; follow a
symlink; recurse without being asked; consult any default or environment path;
or decide containment when case behaviour is inconclusive.

"Opens nothing" is enforced rather than asserted — `builtins.open`, `io.open`,
`os.open`, and `Path.open` are all wrapped during the probe and raise on any
access beneath the catalogue root.

Full detail: `Evidence\studio-headless-model-plumbing\CATALOGUE_CONTRACT.md`.

## 4. Formats, from source

| Format | Support | Evidence |
|---|---|---|
| `.safetensors`, `.ckpt`, `.gguf` | load-plumbed | `modules/sd_models.py:136` `ext_filter` **and** `backend/utils.py::load_torch_file` dispatch |
| `.sft` | recognized, not plumbed | the loader handles it; `sd_models` never enumerates it |
| `.vae.ckpt`, `.vae.safetensors` | unsupported | Forge's own `ext_blacklist` |
| anything else | unsupported | not a checkpoint |

Derived from the retained backend's actual loader entry points, not from familiar
Stable Diffusion filename conventions. A file is not called loadable merely
because its extension looks familiar.

## 5. The load path

```text
model_id -> catalogue lookup -> containment revalidation -> availability
         -> format/support -> immutable LoadRequest -> LoaderPort -> refusal
```

The refusal is last, so `HEADLESS_MODEL_UNKNOWN`,
`HEADLESS_MODEL_FORMAT_NOT_PLUMBED`, `HEADLESS_MODEL_UNAVAILABLE` and
`HEADLESS_MODEL_LOAD_NOT_AUTHORIZED` are distinguishable rather than one generic
"not implemented".

`HEADLESS_MODEL_OUTSIDE_ROOT` is the exception, and this document previously
listed it as though it were reachable alongside the others. It is not, in the
normal path: an entry whose containment cannot be proven is dropped during
description, and the caller sees `HEADLESS_MODEL_UNKNOWN`. That is deliberate --
"unknown" tells a caller strictly less about what exists outside the root than
"outside the root" does -- but it means the code is only reachable in a race
between listing and load.

Studio supplies a `model_id`, never a path. The contained source is constructed
only by the catalogue, and containment is re-verified at load time so a root swap
or a reparse point introduced after enumeration is caught. That revalidation
closes the stale-catalogue and root-swap windows; it does not close the
check-to-open window inside a downstream loader that reopens by filename.

Full detail: `Evidence\studio-headless-model-plumbing\LOAD_REQUEST_BOUNDARY.md`.

## 6. States

```text
READY_NO_MODEL -> CATALOGUE_READY_NO_MODEL -> MODEL_LOAD_VALIDATING -> back
```

`MODEL_LOADING`, `READY`, and `BUSY` stay unreachable, asserted by test.
`MODEL_LOADING` must mean a loader has begun opening a checkpoint, and none does.
Residency is unconditionally false; a refused load restores the previous state
and never fails the runtime.

## 7. Status route

`GET /studio/runtime_status` gained five additive fields:

```text
catalogue_configured        catalogue_ready         catalogue_count
model_load_plumbing_ready   real_model_load_authorized = false
```

Counts only. No root, no path, no filename beyond the existing sanitized display
contract, no traceback, no user name, no environment value.

## 8. What Phase 2B needs

One source-level step needing no permission: `modules/shared.py:7` imports
`backend.memory_management` solely for `xformers_available` (line 30, one reader
at `modules/errors.py:111`). Making it lazy is what stands between
`modules.shared` and a fully headless import.

Then, from the owner: one exact model root or checkpoint path, permission to
read/open that one checkpoint, permission to import Torch, permission to
initialize the selected device, a timeout, a memory/VRAM ceiling, and defined
rollback and cleanup behaviour.

---

## 9. Phase 2B — one controlled Anima load, attempted

```text
Phase 2B crossed the device and checkpoint boundary once.
The Anima-family triplet passed preflight and then FAILED AFTER COMPATIBLE
PREFLIGHT. No image was generated and no model remained resident.
```

**Preflight: compatible.** The owner-supplied triplet is a real Anima DiT
(`net.` prefix, in_channels 16, model_channels 2048), a Qwen3-0.6B text encoder
(hidden 1024, matching `Anima.clip_target`), and a Wan-layout 16-channel VAE.
The VAE is named for Qwen-Image and *is* Wan-family -- `loader.py:527`,
`anima.py:22` (`is_wan=True`), and Anima's bundled `model_index.json`
(`AutoencoderKLQwenImage`) all agree.

**Load: failed after the weights loaded.** All three files were read through
`safetensors.safe_open`, the UNet, CLIP, and VAE patchers were constructed, and
`Anima.__init__` then failed at
`backend/text_processing/anima_engine.py:24` because `modules.shared.opts` is
`None`. The probe never ran Forge's option initialization.

**The finding that matters for this document:** `backend.loader` is statically
Gradio-free (0 module-level paths), but `modules.shared_init.initialize()` --
which the retained engine requires -- imports `modules.shared_options`, which
has 12. So a fully Gradio-free real model load is not currently possible.
Splitting the option definitions is Excision Phase 3.

`shared.xformers_available` is now lazy, so `modules.shared` has zero
module-level paths to both Gradio and Torch and imports for real with both
blocked. The Phase 2A device boundary is gone.

Detail: `Evidence\studio-controlled-model-load\`.

---

## 10. Phase 2B retry — the Anima triplet loaded

```text
CONTROLLED_MODEL_LOAD_SUCCEEDED. One controlled CUDA load, Gradio-free,
inside the 14 GiB ceiling, unloaded before the worker exited.
No image was generated.
```

**Why `shared_init.initialize()` was rejected.** It is Forge's own option
bootstrap and it works, but it imports `modules.shared_options`, which carries 12
module-level paths to Gradio because every option declares the settings-page
component that edits it. `backend.loader` carries 0. Calling it would have made
the first successful headless load import Gradio.

**What the minimal boundary owns.** `forge_headless/headless_options.py` parses
`modules/shared_options.py` as source -- never importing it -- and answers the
option reads the retained loader actually makes. It records every read, raises a
named error rather than returning `None` for anything it cannot supply, and
restores the previous `opts` on success and on exception.

The live load read **exactly one option**: `emphasis = "Original"`, from
`shared_options.py:223`. Nothing was missing.

Ordering is load-bearing: `backend/text_processing/anima_engine.py:11` binds
`opts` by value at import time, so options must be installed before
`backend.loader` is imported.

**Result.** `Anima` engine constructed with unet, clip, and vae all resident,
`is_wan` true; peak 3.665 GiB allocated against a 14 GiB ceiling; allocated back
to 0 before the cache clear and reserved to 0 after; worker exited 0 in 12.0 s of
a 600 s budget. Torch 2.11.0+cu130 on an RTX 5060 Ti, compute capability 12.0.

Detail: `Evidence\studio-controlled-model-load\`.

---

## 11. First-image readiness

```text
Studio is ready for one separately authorized controlled test image.
No real generation occurred in this readiness phase.
```

The options boundary introduced for the load now also covers generation: 31
declared generation-path options, all resolving from Forge source, after
extending the parser to `modules_forge/shared_options.py`,
`modules/processing_scripts/*.py`, and the `OptionInfo(default=...)` keyword
form. Parsed defaults: 281.

Two new owned pieces sit alongside it -- `forge_headless/headless_progress.py`
(nine lifecycle states, cooperative cancellation, a fourteen-attribute
`shared.state` bridge) and `forge_headless/generation_port.py` (validate
everything, refuse last). Neither imports Gradio or Torch.

`FIRST_IMAGE_READINESS_PROVEN`, 10/10, with denoise and decode never called.

Detail: `Evidence\studio-first-image-readiness\`.

