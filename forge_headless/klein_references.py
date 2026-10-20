"""KR1. FLUX.2 Klein reference images, encoded the way Neo's ImageStitch does.

The oracle is `extensions-builtin/sd_forge_image_stitch/scripts/image_stitch.py`
`ImageStitch.process`: clear the prompt cache and the engine's references,
set `dynamic_args.is_referencing`, run each picture through the VAE with
`images_tensor_to_samples(image, 0, sd_model)`, and clear the flag.
`Flux2.encode_first_stage` (`backend/diffusion_engine/flux2.py`) appends each
latent to `engine.ref_latents` while the flag is set, and
`get_learned_conditioning` hands them, after the operation's own source, to
the transformer as `dynamic_args.ref_latents`.

Studio installs no Neo scripts, so the script never runs here; this module is
its encode, called by the port before the pass conditions. Where Studio
differs, it is on purpose (`Evidence/source-review/KR1-klein-reference-images.md`):

- the flag is reset in a `finally` (Neo leaves it set if an encode raises,
  which would make the NEXT img2img source a reference);
- references never outlive the job: `clear` runs after every job that had
  any, on success, failure and cancel, where Neo keeps them encoded between
  runs behind a hash cache;
- transparency is flattened BEFORE the resize (Neo's resize drops alpha
  without flattening it) and the resize never calls an upscaler model;
- a side under 32 pixels rounds to 64, not to 0.

Torch, numpy and Neo are imported inside the functions, so this module can be
imported -- and `prepared_size` tested -- without an engine.
"""

from __future__ import annotations

from typing import Any

from .contracts import HeadlessError

#: ImageStitch's "Maximum Side Length" default (0..2048, step 256, 1024):
#: "reduce VRAM usage during encoding". Each reference adds its own latent
#: tokens to every sampling step, so the cap matters twice.
MAX_SIDE = 1024

#: ImageStitch rounds both sides to this when either is off it.
ROUND_TO = 64

#: Neo's `img2img_background_color` default (`modules/shared_options.py:299`).
DEFAULT_BACKGROUND = "#808080"


#: ImageStitch's slider range for "Maximum Side Length"; 0 is its "no limit".
MAX_SIDE_CEILING = 2048


def prepared_size(width: int, height: int, limit: int = MAX_SIDE) -> tuple[int, int]:
    """The size a reference is encoded at.

    `ImageStitch.preprocess`, arithmetic for arithmetic: cap the long side at
    `limit` (0: no cap), then, if either side is not a multiple of 64, round BOTH to the
    nearest 64. Then `_latent_aligned`, so the result is a whole number of the
    loaded VAE's latent cells whatever the rounding step -- FLUX.2 packs 2x2
    latent blocks and an odd latent side dies in einops.
    """

    from .live_generation_port import _latent_aligned

    w, h = int(width), int(height)
    if limit > 0 and max(w, h) > limit:
        ratio = limit / max(w, h)
        w, h = int(w * ratio), int(h * ratio)
    if w % ROUND_TO != 0 or h % ROUND_TO != 0:
        w = max(ROUND_TO, round(w / ROUND_TO) * ROUND_TO)
        h = max(ROUND_TO, round(h / ROUND_TO) * ROUND_TO)
    return _latent_aligned(w), _latent_aligned(h)


def require_klein(count: int) -> None:
    """Refuse references the loaded model would silently drop.

    ImageStitch acts only when `dynamic_args.klein` (or Kontext / Qwen-Edit /
    Wan) is set, and `Flux2.encode_first_stage` discards them under
    `opts.klein_no_reference`. Neo says nothing in either case; Studio names it.
    """

    from backend.args import dynamic_args

    if not bool(getattr(dynamic_args, "klein", False)):
        raise HeadlessError(
            "REFERENCE_IMAGES_UNSUPPORTED",
            f"{count} reference image(s) were supplied, but the loaded model "
            "is not FLUX.2 Klein. Remove them or load a Klein model.")
    from modules.shared import opts

    if bool(getattr(opts, "klein_no_reference", False)):
        raise HeadlessError(
            "REFERENCE_IMAGES_DISABLED",
            "Reference images were supplied, but the engine option "
            "\"[Klein] Disable Reference\" is on.")


def _flattened(image: Any) -> Any:
    from modules import images as neo_images
    from modules.shared import opts

    background = getattr(opts, "img2img_background_color", None) or DEFAULT_BACKGROUND
    return neo_images.flatten(image, background)


def encode(engine: Any, references: Any, *, processing: Any = None,
           limit: int = MAX_SIDE) -> int:
    """Encode every reference onto `engine.ref_latents`. Returns the count.

    The ImageStitch sequence: reset (prompt cache and the engine's
    references), flag on, one VAE encode per picture, flag off. `limit` is
    ImageStitch's "Maximum Side Length" (0: no limit).
    """

    import numpy as np
    import torch
    from backend.args import dynamic_args
    from modules import images as neo_images
    from modules.sd_samplers_common import images_tensor_to_samples

    clear(engine, processing=processing)
    count = 0
    dynamic_args.is_referencing = True
    try:
        for reference in tuple(references or ()):
            flat = _flattened(reference)
            width, height = prepared_size(*flat.size, limit=int(limit))
            if (width, height) != flat.size:
                # Mode 1, ImageStitch's: fill and crop the excess. "None" keeps
                # the resize a plain LANCZOS rather than an upscaler model.
                flat = neo_images.resize_image(1, flat, width, height,
                                               upscaler_name="None")
            array = np.array(flat, dtype=np.float32) / 255.0
            array = np.moveaxis(array, 2, 0)
            tensor = torch.from_numpy(array).unsqueeze(0)
            # Moved to the engine's device and VAE dtype inside.
            images_tensor_to_samples(tensor, 0, engine)
            count += 1
    finally:
        dynamic_args.is_referencing = False
    return count


def clear(engine: Any, *, processing: Any = None) -> None:
    """Drop every reference this job encoded. NEVER RAISES.

    `ImageStitch.reset_references` (`p.clear_prompt_cache()` and
    `sd_model.clear_references()`) plus the two things it leaves set:
    `dynamic_args.ref_latents`, which `get_learned_conditioning` filled, and
    the referencing flag.
    """

    try:
        clear_references = getattr(engine, "clear_references", None)
        if callable(clear_references):
            clear_references()
    except Exception:  # noqa: BLE001 - cleanup reports nothing and never raises
        pass
    try:
        from backend.args import dynamic_args

        dynamic_args.is_referencing = False
        latents = getattr(dynamic_args, "ref_latents", None)
        if hasattr(latents, "clear"):
            latents.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        if processing is not None and callable(getattr(processing, "clear_prompt_cache", None)):
            processing.clear_prompt_cache()
        else:
            from modules.processing import StableDiffusionProcessing

            StableDiffusionProcessing.cached_c = [None, None, None]
            StableDiffusionProcessing.cached_uc = [None, None, None]
    except Exception:  # noqa: BLE001
        pass


def forget_job_state(engine: Any) -> None:
    """Drop the edit state Neo leaves on the engine between passes. NEVER RAISES.

    Run at the start and end of EVERY job, references or not. An edit model
    (`Flux2`, Kontext, Qwen-Edit) stores each VAE-encoded source as
    `engine.ini_latent` and only consumes it when the positive prompt is next
    conditioned (`flux2.py:47-51`). `process_images_inner` encodes the source
    in `p.init()` (`processing.py:934`) and checks for an interrupt at `:951`,
    BEFORE `setup_conds` at `:988` -- so an interrupted edit, or a Studio
    Hires or Auto Detail pass constructed after the interrupt, left its
    picture behind, and the next Klein job quietly took it as a reference:
    the "ghost" of the last image. Neo clears it after its own Hires encode
    (`processing.py:1543`, "Edit Model"); this is the same reset at the job
    boundary. `dynamic_args.ref_latents` is rebuilt by every conditioning, so
    emptying it at the boundary changes no picture and frees the tensors.
    """

    try:
        if getattr(engine, "ini_latent", None) is not None:
            engine.ini_latent = None
    except Exception:  # noqa: BLE001
        pass
    try:
        from backend.args import dynamic_args

        dynamic_args.is_referencing = False
        latents = getattr(dynamic_args, "ref_latents", None)
        if hasattr(latents, "clear"):
            latents.clear()
    except Exception:  # noqa: BLE001 - no backend, nothing to forget
        pass


__all__ = (
    "DEFAULT_BACKGROUND",
    "MAX_SIDE",
    "MAX_SIDE_CEILING",
    "clear",
    "encode",
    "forget_job_state",
    "prepared_size",
    "require_klein",
)
