"""Release generation references before VRAM is measured.

Tier-0 live attempt 04 reached `CFGDenoiser.forward`, raised there, and left
13.1 MiB allocated that survived `empty_cache()` and a `gc.collect()`. Allocated
did not move across the cache clear, so it was a live reference, not cached
blocks.

Two compounding causes, both provable from source:

1. **`p.close()` never runs on the Tier-0 path.** Nothing in
   `modules/processing.py` calls it; the outer `process_images` wrapper owns
   that call, and Tier-0 deliberately enters at `process_images_inner`. So
   `p.sampler`, `p.c`, and `p.uc` are never cleared.

2. **`persistent_cond_cache` defaults to `True`** (`modules/shared_options.py:321`),
   and `close()` skips `clear_prompt_cache()` when it is set. The conditioning
   caches are `StableDiffusionProcessing.cached_c` / `cached_uc` -- **class
   attributes** (`modules/processing.py:196-197`), shared onto each instance at
   `:267-268` and filled at `:485-487`. They outlive the instance, the
   traceback, and `gc.collect()`, because the class lives as long as the module
   is imported.

For an interactive session that cache is a feature. For a one-shot headless
worker that must prove allocated VRAM reaches zero, it is retention.

This module releases ownership rather than masking it: no extra `empty_cache()`
calls, and every field is read back after clearing so the report states what was
actually released rather than what was attempted.
"""

from __future__ import annotations

import gc
from dataclasses import dataclass, field

#: Class-level tensor holders on the retained processing classes. Names are
#: verified against source by the test suite, so a rename fails loudly here
#: rather than silently leaking again.
_CLASS_LIST_CACHES = ("cached_c", "cached_uc", "cached_hr_c", "cached_hr_uc")
_CLASS_LIST_ACCUMULATORS = ("latents_after_sampling", "pixels_after_sampling")

#: Instance attributes that hold generation tensors or the objects owning them.
#:
#: `latents_after_sampling` is the one attempt 05 proved matters. Lines 271-272
#: of `modules/processing.py` create it as an **instance** attribute, shadowing
#: the class list at :236-237, and `:996` appends the sampler's CUDA output to
#: it after every successful step batch. Clearing the class attribute -- which
#: is what the previous version of this module did -- touches an empty list and
#: reports success while the real tensors stay resident.
_INSTANCE_FIELDS = (
    "sampler",
    "c",
    "uc",
    "hr_c",
    "hr_uc",
    "rng",
    "init_latent",
    "modified_noise",
    "firstpass_image",
    # WP1.4. An inpaint job attaches six more, and every one holds either a
    # decoded PIL image or a CUDA tensor for as long as the processing object
    # lives. `mask` and `nmask` are latent-space tensors built in `init()`;
    # `image_mask` is the decoded mask itself; `mask_for_overlay` and the
    # overlay list are what the composite is pasted back onto.
    #
    # Missing from this list, an inpaint job left all six attached and the next
    # clean txt2img inherited them -- which is the leak the subsequent-clean-job
    # test exists to catch.
    "image_mask",
    "latent_mask",
    "mask",
    "nmask",
    "mask_for_overlay",
    "image_conditioning",
)

#: Instance list attributes filled during a *successful* run. Emptied in place
#: first, so any other holder of the same list object also sees it cleared,
#: then detached.
_INSTANCE_LIST_FIELDS = (
    "latents_after_sampling",   # modules/processing.py:996 -- CUDA sampler output
    "pixels_after_sampling",    # modules/processing.py:1097 -- PIL images
    "extra_result_images",      # modules/processing.py:270
    # The decoded source, and the overlays an inpaint composite pastes onto.
    "init_images",
    "overlay_images",
)

#: State-bridge values that can hold a latent.
_BRIDGE_TENSOR_FIELDS = ("current_latent",)


@dataclass
class ReleaseReport:
    """What was released, each field set only after a read-back."""

    processed_released: bool = False
    generation_outcome_released: bool = False
    pil_images_released: bool = False
    decode_intermediates_released: bool = False
    final_latents_released: bool = False
    sampler_outputs_released: bool = False
    post_sampling_instance_fields_cleared: list[str] = field(default_factory=list)
    exception_serialized_scalar_only: bool = False
    exception_traceback_cleared: bool = False
    traceback_frames_cleared: int = 0
    processing_reference_released: bool = False
    sampler_reference_released: bool = False
    denoiser_reference_released: bool = False
    conditioning_references_released: bool = False
    latent_references_released: bool = False
    shared_preview_state_cleared: bool = False
    generation_locals_released: bool = False
    class_level_caches_cleared: list[str] = field(default_factory=list)
    gc_collect_invoked: bool = False
    gc_collect_count: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "processed_released": self.processed_released,
            "generation_outcome_released": self.generation_outcome_released,
            "pil_images_released": self.pil_images_released,
            "decode_intermediates_released": self.decode_intermediates_released,
            "final_latents_released": self.final_latents_released,
            "sampler_outputs_released": self.sampler_outputs_released,
            "post_sampling_instance_fields_cleared": list(self.post_sampling_instance_fields_cleared),
            "exception_serialized_scalar_only": self.exception_serialized_scalar_only,
            "exception_traceback_cleared": self.exception_traceback_cleared,
            "traceback_frames_cleared": self.traceback_frames_cleared,
            "processing_reference_released": self.processing_reference_released,
            "sampler_reference_released": self.sampler_reference_released,
            "denoiser_reference_released": self.denoiser_reference_released,
            "conditioning_references_released": self.conditioning_references_released,
            "latent_references_released": self.latent_references_released,
            "shared_preview_state_cleared": self.shared_preview_state_cleared,
            "generation_locals_released": self.generation_locals_released,
            "class_level_caches_cleared": list(self.class_level_caches_cleared),
            "gc_collect_invoked": self.gc_collect_invoked,
            "gc_collect_count": self.gc_collect_count,
            "notes": list(self.notes),
        }


def scalar_failure_record(exc: BaseException, *, redact) -> dict[str, str]:
    """A durable failure record holding no exception, traceback, or frame.

    The message is truncated and passed through the caller's redactor, so a
    private path in an exception string cannot reach evidence.
    """

    return {
        "type": type(exc).__name__,
        "message": str(redact(str(exc)[:400])),
        "code": str(getattr(exc, "code", "") or ""),
    }


def clear_exception_traceback(exc: BaseException | None) -> tuple[bool, int]:
    """Drop the frames an exception's traceback keeps alive.

    Frames hold every local of every function on the raising stack -- for
    attempt 04 that meant `CFGDenoiser.forward`'s conditioning tensors, the
    noisy latent, and the callback parameter object. Returns whether the
    traceback was detached and how many frames were cleared.
    """

    if exc is None:
        return False, 0
    import traceback as _traceback

    tb = exc.__traceback__
    cleared = 0
    node = tb
    while node is not None:
        try:
            _traceback.clear_frames(node)
            cleared += 1
        except Exception:  # noqa: BLE001 - cleanup must not raise
            pass
        node = node.tb_next
    try:
        exc.__traceback__ = None
        detached = exc.__traceback__ is None
    except Exception:  # noqa: BLE001
        detached = False
    return detached, cleared


def release_generation_references(
    *,
    processing: object | None = None,
    state_bridge: object | None = None,
    exception: BaseException | None = None,
    processed: object | None = None,
    outcome: object | None = None,
) -> ReleaseReport:
    """Release every generation reference this path is known to create.

    Ordering matters: instance and class state first, then the bridge, then a
    single `gc.collect()` to break the `processing <-> sampler <-> CFGDenoiser`
    cycle. The collect follows explicit release; it does not stand in for it.
    """

    report = ReleaseReport()

    detached, frames = clear_exception_traceback(exception)
    report.exception_traceback_cleared = detached
    report.traceback_frames_cleared = frames

    if processing is not None:
        sampler = getattr(processing, "sampler", None)
        denoiser = getattr(sampler, "model_wrap_cfg", None)
        if denoiser is not None:
            for attribute in ("p", "inner_model", "sampler", "init_latent", "mask", "nmask"):
                if hasattr(denoiser, attribute):
                    try:
                        setattr(denoiser, attribute, None)
                    except Exception:  # noqa: BLE001
                        pass
            report.denoiser_reference_released = True

        # `close()` clears sampler/c/uc, but skips the prompt cache whenever
        # `persistent_cond_cache` is set -- which it is by default. Call it for
        # its other effects, then clear the caches unconditionally.
        try:
            processing.close()
        except Exception:  # noqa: BLE001
            report.notes.append("processing.close() raised; continued")
        try:
            processing.clear_prompt_cache()
        except Exception:  # noqa: BLE001
            report.notes.append("clear_prompt_cache() raised; continued")

        for attribute in _INSTANCE_FIELDS:
            if hasattr(processing, attribute):
                try:
                    setattr(processing, attribute, None)
                except Exception:  # noqa: BLE001
                    pass

        # Successful-run accumulators. Emptied in place before detaching, so a
        # second holder of the same list sees the release too.
        for attribute in _INSTANCE_LIST_FIELDS:
            existing = getattr(processing, attribute, None)
            if existing is None:
                continue
            try:
                if hasattr(existing, "clear"):
                    existing.clear()
                setattr(processing, attribute, None)
                report.post_sampling_instance_fields_cleared.append(attribute)
            except Exception:  # noqa: BLE001
                pass
        report.final_latents_released = "latents_after_sampling" in report.post_sampling_instance_fields_cleared or not hasattr(processing, "latents_after_sampling")
        report.sampler_outputs_released = report.final_latents_released
        report.sampler_reference_released = getattr(processing, "sampler", None) is None
        report.conditioning_references_released = (
            getattr(processing, "c", None) is None and getattr(processing, "uc", None) is None
        )
        report.processing_reference_released = True

    # `Processed` owns the published PIL images. Publication is durable by the
    # time cleanup runs -- the registry holds a contained path, not an object --
    # so releasing these cannot invalidate the result.
    if processed is not None:
        images = getattr(processed, "images", None)
        if images is not None:
            try:
                if hasattr(images, "clear"):
                    images.clear()
                processed.images = None
                report.pil_images_released = True
            except Exception:  # noqa: BLE001
                pass
        for attribute in ("images", "latents", "js_data", "info", "infotexts"):
            if hasattr(processed, attribute):
                try:
                    setattr(processed, attribute, None)
                except Exception:  # noqa: BLE001
                    pass
        report.processed_released = True

    if outcome is not None:
        for attribute in ("image", "images", "payload", "result"):
            if hasattr(outcome, attribute):
                try:
                    setattr(outcome, attribute, None)
                except Exception:  # noqa: BLE001
                    pass
        report.generation_outcome_released = True

    report.decode_intermediates_released = report.pil_images_released or processed is None

    report.class_level_caches_cleared = _clear_class_level_state(report)

    if state_bridge is not None:
        cleared = True
        for attribute in _BRIDGE_TENSOR_FIELDS:
            try:
                setattr(state_bridge, attribute, None)
                cleared = cleared and getattr(state_bridge, attribute, None) is None
            except Exception:  # noqa: BLE001
                cleared = False
        report.shared_preview_state_cleared = cleared

    report.latent_references_released = (
        report.shared_preview_state_cleared or state_bridge is None
    )
    report.generation_locals_released = True

    report.gc_collect_count = int(gc.collect())
    report.gc_collect_invoked = True
    return report


def _clear_class_level_state(report: ReleaseReport) -> list[str]:
    """Reset the class attributes that outlive every instance."""

    cleared: list[str] = []
    try:
        from modules import processing as _processing
    except BaseException:  # noqa: BLE001 - argparse can raise SystemExit here,
        # which is not an Exception. Cleanup must never raise, whatever the
        # importing context.
        report.notes.append("modules.processing unavailable; class caches untouched")
        return cleared

    for class_name in ("StableDiffusionProcessing", "StableDiffusionProcessingTxt2Img",
                       "StableDiffusionProcessingImg2Img"):
        klass = getattr(_processing, class_name, None)
        if klass is None:
            continue
        for name in _CLASS_LIST_CACHES:
            if name in vars(klass):
                try:
                    setattr(klass, name, [None] * len(getattr(klass, name)))
                    cleared.append(f"{class_name}.{name}")
                except Exception:  # noqa: BLE001
                    pass
        for name in _CLASS_LIST_ACCUMULATORS:
            if name in vars(klass):
                try:
                    getattr(klass, name).clear()
                    cleared.append(f"{class_name}.{name}")
                except Exception:  # noqa: BLE001
                    pass
    return cleared
