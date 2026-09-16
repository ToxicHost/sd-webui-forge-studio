"""Drive Neo's Soft Inpainting from a headless job. WP1.6.

THE DEFECT THIS CLOSES

The page has carried the whole Soft Inpainting group since before the
standalone existed -- `checkSoftInpaint` and six sliders whose defaults already
match the engine's exactly -- and only the LEGACY collector ever read it. The
lifecycle body that actually submits sent four inpaint fields and nothing else,
and `rg soft_inpaint` across the Studio backend returned zero matches. An owner
switching it on changed nothing, which is why inpaint seams came back visibly
rough. Review: `Evidence/source-review/WP1.6-soft-inpainting.md`.

WHAT ROUGH EDGES ACTUALLY ARE

`processing.py:1892` rounds the LATENT mask to 0 or 1 when `p.mask_round` is
set, and it is set by default. A latent cell covers an 8x8 pixel block, so every
block ends up wholly original or wholly regenerated: the seam is a staircase at
8px pitch that `mask_blur` can only soften afterwards, in pixel space, after the
damage is done. Soft Inpainting clears that flag and blends the latent across
the transition instead.

WHY AN ADAPTER RATHER THAN A SCRIPT RUNNER -- deliberate divergence

The Extension arms this by finding the script in `runner.alwayson_scripts` by
title and writing positional args into `script_args`
(`studio_generation.py:1494-1511`). Studio's live port runs with
`processing.scripts = None` and has no ScriptRunner, so that route does not
exist here. Building a Gradio-shaped runner to hold exactly one script would be
ceremony around a seven-argument tuple.

THE ALGORITHM IS NOT REIMPLEMENTED. `latent_blend`, `get_modified_nmask`,
`apply_masks` and `apply_adaptive_masks` are module-level functions in Neo's
own `soft_inpainting.py` and are called directly. This file is transport: it
decides WHEN they run and with WHICH numbers, and Neo decides what they do. A
reimplementation here would be a second copy of someone else's algorithm,
drifting on their next release.

EVERYTHING ELSE IS A NO-OP, AND THAT IS AUDITED

Assigning any object to `p.scripts` makes 36 currently-skipped branches in
`processing.py` and `sd_samplers_cfg_denoiser.py` execute. Each was checked
against the pinned engine before this file was written:

  - every read-back site builds its argument object from the current value --
    `PostSampleArgs(samples_ddim)`, `PostprocessImageArgs(image, ...)`,
    `PostProcessMaskOverlayArgs(i, mask_for_overlay, overlay_image)`,
    `PostprocessBatchListArgs(list(x_samples_ddim))` -- so a hook that does
    nothing hands back exactly what it was given;
  - `p.prompts` / `p.negative_prompts` at :1040-1041 newly execute, but :959
    already assigns `p.prompts` with the identical expression outside any
    guard;
  - `x_samples_ddim = batch_params.images` turns a tensor into a list of its
    rows, and its only consumer is `enumerate(...)`, which yields the same row
    tensors either way.

So a no-op default is behaviourally identical to `None`. `__getattr__` provides
it for every hook not implemented below -- including hooks a future engine
release adds, which is the case a hand-written list of stubs would get wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def _noop(*_args: Any, **_kwargs: Any) -> None:
    """What "no scripts installed" means, spelled out."""

    return None


#: Neo's module, loaded once, by path.
#:
#: `extensions-builtin/soft-inpainting/scripts/` is not a package -- both
#: directory names contain hyphens -- and nothing in the headless path puts it
#: on `sys.path`, so a plain import cannot reach it. Loading by file location
#: is the honest way in rather than mutating `sys.path` for the whole process.
#:
#: It is loaded LAZILY, from inside the hooks, because its own imports pull in
#: gradio, torch and `modules.scripts`. Routing tests decide whether this
#: bridge is armed at all, and none of them should need the engine present to
#: answer that.
_MODULE: Any = None


def _soft_inpainting() -> Any:
    global _MODULE
    if _MODULE is None:
        import importlib.util

        source = (Path(__file__).resolve().parents[1] / "extensions-builtin"
                  / "soft-inpainting" / "scripts" / "soft_inpainting.py")
        if not source.is_file():
            raise SoftInpaintingUnavailable(
                "Soft Inpainting is not installed in this build.")
        spec = importlib.util.spec_from_file_location(
            "studio_soft_inpainting", source)
        if spec is None or spec.loader is None:
            raise SoftInpaintingUnavailable(
                "Soft Inpainting could not be loaded.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULE = module
    return _MODULE


class SoftInpaintingUnavailable(RuntimeError):
    """The engine's module is missing or will not import.

    Named rather than swallowed. An owner who switched the feature on and got
    ordinary hard-edged inpainting back, silently, would have no way to tell
    that from the feature simply not working -- and that is the exact defect
    class this work package exists to remove.
    """


class SoftInpaintingBridge:
    """Studio's stand-in for a ScriptRunner carrying one alwayson script.

    Constructed only when the owner switched Soft Inpainting on AND the job is
    an inpaint -- see `live_generation_port`. A job without it keeps
    `scripts = None` and is byte-identical to a pre-WP1.6 one.
    """

    def __init__(self, settings: Any) -> None:
        #: Studio's contract object. Translated to the engine's own settings
        #: type lazily, in `_engine_settings`, because importing Neo's module
        #: at construction would drag the engine into unit tests that only
        #: check routing.
        self._studio_settings = settings
        self._engine: Any = None
        #: Rebuilt by `post_sample` and read by `postprocess_maskoverlay`, in
        #: that order, exactly as the script does it.
        self.masks_for_overlay: Any = None
        self.overlay_images: Any = None

    # -- the four hooks Soft Inpainting actually implements ----------------

    def _engine_settings(self) -> Any:
        if self._engine is None:
            settings = self._studio_settings
            # Positional, and in the engine's own field order:
            # power, scale, detail_preservation, mask_inf, dif_thresh,
            # dif_contr. Studio's names are the engine's UI LABELS, which is
            # why the mapping below is not an identity.
            self._engine = _soft_inpainting().SoftInpaintingSettings(
                float(settings.schedule_bias),
                float(settings.preservation),
                float(settings.transition_contrast),
                float(settings.mask_influence),
                float(settings.diff_threshold),
                float(settings.diff_contrast),
            )
        return self._engine

    @staticmethod
    def _uses_inpainting(processing: Any) -> bool:
        return bool(_soft_inpainting().processing_uses_inpainting(processing))

    def process(self, processing: Any, *_args: Any, **_kwargs: Any) -> None:
        """Clear the latent mask rounding. `soft_inpainting.py:604-617`.

        The gate is the engine's own `processing_uses_inpainting`, not
        Studio's operation enum, because by this point the only thing that
        matters is whether a mask actually reached the processing object. A
        txt2img job never has one.
        """

        if not self._uses_inpainting(processing):
            return
        processing.mask_round = False
        self._engine_settings().add_generation_params(
            processing.extra_generation_params)

    def on_mask_blend(self, processing: Any, mba: Any,
                      *_args: Any, **_kwargs: Any) -> None:
        """The blend itself. `soft_inpainting.py:619-634`."""

        if not self._uses_inpainting(processing):
            return
        if mba.is_final_blend:
            mba.blended_latent = mba.current_latent
            return

        engine = _soft_inpainting()
        settings = self._engine_settings()
        mba.blended_latent = engine.latent_blend(
            settings, mba.init_latent, mba.current_latent,
            # `sigma` is 2D with both values equal; the script takes [0] and
            # leaves a `todo` asking why. Copied rather than corrected -- this
            # module does not get to decide the engine was wrong.
            engine.get_modified_nmask(settings, mba.nmask, mba.sigma[0]))

    def post_sample(self, processing: Any, ps: Any,
                    *_args: Any, **_kwargs: Any) -> None:
        """Rebuild the overlays the composite will use.
        `soft_inpainting.py:636-671`."""

        if not self._uses_inpainting(processing):
            return
        nmask = getattr(processing, "nmask", None)
        if nmask is None:
            return

        engine = _soft_inpainting()
        from modules import images
        from modules.shared import opts

        settings = self._engine_settings()

        # The engine's normal path punches holes in the existing overlays, so
        # they are rebuilt from the init images rather than patched.
        self.overlay_images = []
        for source in processing.init_images:
            image = images.flatten(source, opts.img2img_background_color)
            if processing.paste_to is None and processing.resize_mode != 3:
                image = images.resize_image(
                    processing.resize_mode, image,
                    processing.width, processing.height)
            self.overlay_images.append(image.convert("RGBA"))

        if len(processing.init_images) == 1:
            self.overlay_images = self.overlay_images * processing.batch_size

        if getattr(ps.samples, "already_decoded", False):
            self.masks_for_overlay = engine.apply_masks(
                settings=settings, nmask=nmask,
                overlay_images=self.overlay_images,
                width=processing.width, height=processing.height,
                paste_to=processing.paste_to)
        else:
            self.masks_for_overlay = engine.apply_adaptive_masks(
                settings=settings, nmask=nmask,
                latent_orig=processing.init_latent, latent_processed=ps.samples,
                overlay_images=self.overlay_images,
                width=processing.width, height=processing.height,
                paste_to=processing.paste_to)

    def postprocess_maskoverlay(self, processing: Any, ppmo: Any,
                                *_args: Any, **_kwargs: Any) -> None:
        """Hand the rebuilt overlay back. `soft_inpainting.py:674-684`."""

        if not self._uses_inpainting(processing):
            return
        if self.masks_for_overlay is None or self.overlay_images is None:
            return
        ppmo.mask_for_overlay = self.masks_for_overlay[ppmo.index]
        ppmo.overlay_image = self.overlay_images[ppmo.index]

    # -- everything else -----------------------------------------------------

    def __getattr__(self, name: str) -> Callable[..., None]:
        """Any other hook does nothing, which is what `None` did.

        Deliberately not a fixed list of stubs: the engine calls sixteen
        distinct methods on `p.scripts` today and a release that adds a
        seventeenth would raise `AttributeError` mid-generation against a list,
        where the previous `scripts = None` would simply have skipped it.

        Dunder names are excluded so this cannot accidentally satisfy a
        protocol check -- `copy`, `pickle` and `hasattr(x, "__iter__")` all
        probe for dunders, and answering "yes, and it returns None" to those is
        how an object starts lying about what it is.
        """

        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _noop


__all__ = ("SoftInpaintingBridge", "SoftInpaintingUnavailable")
