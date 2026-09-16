"""Translate between the Studio job contracts and the headless generation seam.

`HeadlessBackendAdapter` speaks Studio's vocabulary; `GenerationGateway`,
`FirstImageRequest` and `HeadlessProgress` speak the headless one. The two do
not line up -- Studio has an 8-field request and a 5-state job, headless has an
18-field request and a 9-state lifecycle -- so something has to translate, and
this is that something.

It is a translator and a job table, not a second application. It creates no
request, job, progress, result, cancellation or error model of its own: every
value it returns is a `forge_studio.contracts` type, and every value it consumes
is a headless one.

Nothing here imports Torch, Gradio, or a Forge module, and constructing a
session performs no model, device, or filesystem work.

Two design points are load-bearing:

* **The port decides whether generation happens.** A session with no injected
  port keeps `PolicyGatedGenerator`, which refuses after full validation. There
  is no path from "session configured" to "real generation" that does not go
  through an explicitly injected port.
* **Work runs through an injected executor.** With the inline executor a submit
  is synchronous and deterministic; with a deferring executor a test can cancel
  between submission and execution. Neither sleeps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from forge_studio.contracts import (
    CancellationResult,
    GeneratedResult,
    GenerationJobIdentity,
    GenerationRequest,
    JobState as StudioJobState,
    ModelCapability,
    ProgressEvent,
    StructuredError,
)

from .contracts import HeadlessError
from .generation_port import GenerationGateway, GenerationOutcome
from .generation_request import (
    DEFAULT_CFG_SCALE,
    DEFAULT_DISTILLED_CFG_SCALE,
    DEFAULT_SAMPLER,
    DEFAULT_SCHEDULER,
    DIMENSION_ALIGNMENT,
    FirstImageRequest,
    ResidentModel,
)
from .headless_progress import (
    STAGE_LABELS,
    HeadlessProgress,
    JobState as HeadlessJobState,
)


# -------------------------------------------------------------- error codes

#: The stable Studio-facing codes. Headless codes are mapped onto these so the
#: wire contract does not change every time a runtime detail does.
BACKEND_NOT_READY = "BACKEND_NOT_READY"
MODEL_NOT_SELECTED = "MODEL_NOT_SELECTED"
MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
REQUEST_UNSUPPORTED = "REQUEST_UNSUPPORTED"
GENERATION_FAILED = "GENERATION_FAILED"
GENERATION_CANCELLED = "GENERATION_CANCELLED"
RESULT_PUBLICATION_FAILED = "RESULT_PUBLICATION_FAILED"
CLEANUP_FAILED = "CLEANUP_FAILED"
BACKEND_SHUTDOWN = "BACKEND_SHUTDOWN"
JOB_NOT_FOUND = "JOB_NOT_FOUND"

STUDIO_ERROR_CODES = (
    BACKEND_NOT_READY,
    MODEL_NOT_SELECTED,
    MODEL_LOAD_FAILED,
    REQUEST_UNSUPPORTED,
    GENERATION_FAILED,
    GENERATION_CANCELLED,
    RESULT_PUBLICATION_FAILED,
    CLEANUP_FAILED,
    BACKEND_SHUTDOWN,
    JOB_NOT_FOUND,
)

#: Headless code -> stable Studio code. Anything unlisted becomes
#: GENERATION_FAILED rather than leaking a runtime code onto the wire.
_HEADLESS_TO_STUDIO: dict[str, str] = {
    "GENERATION_CANCELLED": GENERATION_CANCELLED,
    "GENERATION_JOB_UNKNOWN": JOB_NOT_FOUND,
    "GENERATION_MODEL_NOT_RESIDENT": MODEL_NOT_SELECTED,
    "GENERATION_MODEL_MISMATCH": MODEL_NOT_SELECTED,
    "GENERATION_OPERATION_UNSUPPORTED": REQUEST_UNSUPPORTED,
    "GENERATION_DIMENSION_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    "GENERATION_DIMENSION_MISALIGNED": REQUEST_UNSUPPORTED,
    "GENERATION_BATCH_SIZE_UNSUPPORTED": REQUEST_UNSUPPORTED,
    "GENERATION_OUTPUT_COUNT_UNSUPPORTED": REQUEST_UNSUPPORTED,
    "GENERATION_STEPS_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    "GENERATION_SEED_NOT_FIXED": REQUEST_UNSUPPORTED,
    "GENERATION_SAMPLER_UNSUPPORTED": REQUEST_UNSUPPORTED,
    "GENERATION_SCHEDULER_UNSUPPORTED": REQUEST_UNSUPPORTED,
    "GENERATION_HIRES_NOT_PERMITTED": REQUEST_UNSUPPORTED,
    "GENERATION_HIRES_SCALE_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    "GENERATION_HIRES_STEPS_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    "GENERATION_HIRES_DENOISE_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    # Not a bad request: the owner asked for something reasonable and
    # the machine could not do it. Mapping this onto REQUEST_UNSUPPORTED
    # would tell them to change the request when the honest answer is
    # that the card ran out.
    "GENERATION_HIRES_OUT_OF_MEMORY": GENERATION_FAILED,
    "GENERATION_ADETAILER_NOT_PERMITTED": REQUEST_UNSUPPORTED,
    "GENERATION_ADETAILER_NO_SLOTS": REQUEST_UNSUPPORTED,
    "GENERATION_ADETAILER_DETECTOR_UNRESOLVED": REQUEST_UNSUPPORTED,
    "GENERATION_ADETAILER_CONFIDENCE_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    "GENERATION_ADETAILER_DENOISE_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    "GENERATION_ADETAILER_STEPS_OUT_OF_RANGE": REQUEST_UNSUPPORTED,
    # The orchestrator's own refusal, for a slot that reached it unresolved.
    "GENERATION_ADETAILER_DETECTOR_UNRESOLVED_AT_RUN": REQUEST_UNSUPPORTED,
    "GENERATION_EXTENSIONS_NOT_PERMITTED": REQUEST_UNSUPPORTED,
    "GENERATION_REFERENCE_NOT_PERMITTED": REQUEST_UNSUPPORTED,
    "GENERATION_OPTIONS_MISSING": BACKEND_NOT_READY,
    "GENERATION_PROGRESS_NOT_INSTALLED": BACKEND_NOT_READY,
    "GENERATION_RESULT_ROOT_UNAVAILABLE": BACKEND_NOT_READY,
    "GENERATION_REQUEST_INCOMPLETE": REQUEST_UNSUPPORTED,
    "GENERATION_NO_RESULT": RESULT_PUBLICATION_FAILED,
    # Refusing to overwrite is a publication failure, not a bad request: the
    # owner asked for something reasonable and the result could not be placed.
    "GENERATION_RESULT_IDENTIFIER_COLLISION": RESULT_PUBLICATION_FAILED,
    "GENERATION_BACKEND_FAILED": GENERATION_FAILED,
    "HEADLESS_GENERATION_NOT_AUTHORIZED": BACKEND_NOT_READY,
    "HEADLESS_BACKEND_NOT_READY": BACKEND_NOT_READY,
    "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED": MODEL_LOAD_FAILED,
    "HEADLESS_MODEL_UNKNOWN": MODEL_LOAD_FAILED,
    "HEADLESS_MODEL_ID_REQUIRED": MODEL_LOAD_FAILED,
    "HEADLESS_CATALOGUE_NOT_CONFIGURED": MODEL_LOAD_FAILED,
}

#: Which request field a translation problem belongs to, so an error can name it.
_CODE_FIELD: dict[str, str] = {
    "GENERATION_DIMENSION_OUT_OF_RANGE": "width",
    "GENERATION_DIMENSION_MISALIGNED": "width",
    "GENERATION_STEPS_OUT_OF_RANGE": "steps",
    "GENERATION_SEED_NOT_FIXED": "seed",
    "GENERATION_MODEL_MISMATCH": "model_id",
    "GENERATION_MODEL_NOT_RESIDENT": "model_id",
    "GENERATION_SAMPLER_UNSUPPORTED": "sampler",
    "GENERATION_SCHEDULER_UNSUPPORTED": "scheduler",
    "GENERATION_HIRES_SCALE_OUT_OF_RANGE": "hires.scale",
    "GENERATION_HIRES_STEPS_OUT_OF_RANGE": "hires.second_pass_steps",
    "GENERATION_HIRES_DENOISE_OUT_OF_RANGE": "hires.denoising_strength",
    "GENERATION_ADETAILER_DETECTOR_UNRESOLVED": "auto_detail.slots.detector",
    "GENERATION_ADETAILER_CONFIDENCE_OUT_OF_RANGE": "auto_detail.slots.confidence",
    "GENERATION_ADETAILER_DENOISE_OUT_OF_RANGE": (
        "auto_detail.slots.denoising_strength"
    ),
    "GENERATION_ADETAILER_STEPS_OUT_OF_RANGE": "auto_detail.slots.steps",
}



def _hires_metadata(translation: Any) -> dict[str, Any]:
    """The resolved second-pass recipe, or nothing at all.

    Nothing when Hires did not run, so a single-pass result does not carry a
    Hires section full of defaults that never touched an image. A reader can
    then use presence as the answer to "was this upscaled?" rather than having
    to compare `hires.enabled` against a value it might have inherited.
    """

    request = getattr(translation, "headless_request", None)
    if request is None or not getattr(request, "enable_hr", False):
        return {}
    return {
        "hires": {
            "scale": float(getattr(request, "hr_scale", 0.0)),
            # Empty means the engine default was used, and saying so is more
            # honest than naming whatever the engine happened to pick.
            "upscaler": str(getattr(request, "hr_upscaler", "") or ""),
            "second_pass_steps": int(getattr(request, "hr_second_pass_steps", 0)),
            "denoising_strength": float(
                getattr(request, "hr_denoising_strength", 0.0)
            ),
            "sampler": str(getattr(request, "hr_sampler_name", "") or ""),
            "scheduler": str(getattr(request, "hr_scheduler", "") or ""),
        }
    }

def _auto_detail_metadata(outcomes: Any) -> dict[str, Any]:
    """The RESOLVED Auto Detail outcome, or nothing at all.

    The request's shape is not the interesting fact -- it is already in the
    job. What a reader wants is what the detectors actually found, so this
    records the outcome: detector name, candidates, regions kept, and whether
    a detail pass ran.

    Nothing when no slot ran, exactly as the Hires recipe is absent when no
    second pass ran, so presence answers "was this detailed?" and a base-only
    result stays byte-identical to a pre-P0.8 one.
    """

    if not outcomes:
        return {}
    from .auto_detail import describe_outcomes

    return describe_outcomes(outcomes)


def studio_code_for(headless_code: str) -> str:
    """Map one headless code onto a stable Studio code."""

    return _HEADLESS_TO_STUDIO.get(headless_code, GENERATION_FAILED)


#: Prefix kept from the previous scheme so both generations of result names
#: share one recognisable shape. Only the suffix changed.
RESULT_IDENTIFIER_PREFIX = "headless-"


def default_result_identifier() -> str:
    """One request id, unique across processes, restarts and instances.

    This was `itertools.count(1)` on the instance, formatted `headless-%06d`.
    The identifier is also the result FILENAME STEM, and the counter lived in
    memory, so every Studio restart began reissuing `headless-000001` over
    whatever was already in the result root. A live verification restart
    destroyed the canonical anchor result that way; it survived only because
    an unrelated probe had copied it minutes earlier.

    `uuid4().hex` is restart-, process- and instance-safe with no result-root
    scan, no wall-clock dependency and no relation to the seed. It stays
    opaque, which the result handle layer already assumes.

    Deliberately NOT derived from: the seed (repeats by design), a process
    counter (the defect), a seconds-resolution timestamp (two jobs per second
    collide), or the number of entries in the result root (races, and wrong
    the moment anything is archived).
    """

    return f"{RESULT_IDENTIFIER_PREFIX}{uuid4().hex}"


# ------------------------------------------------------- request translation

#: Studio's request carries these fields and no others. (It said EIGHT for
#: two phases after it became ten, which is this codebase's most frequent
#: defect: a rule in prose outliving the thing it describes.)
STUDIO_REQUEST_FIELDS = (
    # Gate 2. Moved out of BACKEND_DEFAULTED_FIELDS, whose docstring says those
    # are fields "Studio's request cannot express" -- which stopped being true
    # the moment `Operation` had more than one member. `studio_adapter`
    # returns this tuple verbatim from `supported_generation_parameters()`, so
    # this line is also what tells the page it may choose.
    "operation",
    "source_image",
    # WP1.4. `supported_generation_parameters()` returns this tuple verbatim,
    # so a field translated but not listed here is a capability the page is
    # never told it has -- the exact defect this list exists to prevent.
    "mask",
    "inpaint",
    "denoising_strength",
    "model_id",
    "positive_prompt",
    "negative_prompt",
    "seed",
    "steps",
    "cfg_scale",
    "width",
    "height",
    # Carried from P0.6. They were in BACKEND_DEFAULTED_FIELDS while the UI
    # showed a sampler menu, which is the precise shape of "the control does
    # nothing and the report says it was defaulted".
    "sampler",
    "scheduler",
    # P0.7. Carried as ONE group, so the report says "the owner asked for a
    # Hires pass" rather than listing nine fields that only mean anything
    # together.
    "hires",
    # Live Preview. It sat in BACKEND_DEFAULTED_FIELDS below -- "Studio's
    # request cannot express this" -- while the page shipped a Live Preview
    # toggle in Settings and app.js gated its own preview rendering on it. Both
    # halves of that were true and they contradicted each other: the owner
    # turned previews on, the request said nothing, and the backend pinned the
    # flag off twice over. Third instance of the same defect after sampler and
    # Hires, and the last field in this file that claimed to be unexpressible
    # while a control for it existed.
    "preview_enabled",
    # P0.8. FOURTH instance, and the comment at the top of this tuple predicted
    # it: a rule in prose outliving the thing it describes. `translate_request`
    # has carried auto_detail since Auto Detail landed (`_auto_detail_fields`
    # below, called at the `**` expansion), the pipeline runs the slots, and
    # the job record reports them -- but this tuple omitted it, and
    # `supported_generation_parameters()` returns exactly this tuple. So the
    # backend told the UI it could not vary auto_detail while it was already
    # varying it. Found by audit, not by a test: `translation.carried` is
    # assigned FROM this constant, so the assertion comparing the two is a
    # tautology and pins nothing.
    "auto_detail",
    # Variation seed. Studio can vary it, so the backend must say so -- the
    # same correction `auto_detail` needed, for the same reason.
    "variation",
    # GPU tile compositing. The owner's Settings toggle, snapshotted by the
    # server at assembly. Reported as carried because it is: it crosses the
    # boundary and reaches the upscaler's preflight, which may still answer
    # with the host path when the frame will not fit.
    "gpu_tile_compositing_requested",
    # Clip Skip. Carried, because it crosses the boundary and reaches the
    # per-job option scope; whether it changes the image is then Neo's
    # conditioning, which reads the option this scope overrides.
    "clip_skip",
)

#: Headless fields Studio's request cannot express. They take the headless
#: default and are reported as such -- never presented as a Studio choice.
BACKEND_DEFAULTED_FIELDS = (
    "distilled_cfg_scale",
    "batch_size",
    "output_count",
)

#: Feature flags the Tier-0 profile does not permit. Studio has no field for any
#: of them, so they are pinned off rather than left to a default that might move.
PINNED_OFF_FLAGS = (
    # `enable_hr` LEFT this tuple in P0.7. It was pinned off because Studio had
    # no field for it and a default that might move is worse than an explicit
    # refusal -- the same reasoning that still applies to the two below. Now
    # the owner has a field, so the flag follows the request instead of a pin.
    #
    # `enable_adetailer` left it the same way, in P0.8: it now follows
    # `auto_detail.enabled`. It is still REFUSED downstream, because the slot
    # settings do not travel yet and the runtime is not wired -- but refused is
    # not pinned, and reporting it as pinned would misdescribe a flag that now
    # carries the owner's choice.
    "enable_extensions",
    "reference_image_enabled",
)


@dataclass(frozen=True)
class RequestTranslation:
    """One translated request plus a field-by-field account of how."""

    headless_request: FirstImageRequest
    carried: tuple[str, ...]
    backend_defaulted: tuple[str, ...]
    pinned_off: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Loggable. Prompt text is excluded, only lengths travel."""

        return {
            "carried": list(self.carried),
            "backend_defaulted": list(self.backend_defaulted),
            "pinned_off": list(self.pinned_off),
            "request": self.headless_request.to_dict(),
        }


def _variation_fields(variation: Any) -> dict[str, Any]:
    """Neo's four variation fields, or nothing at all.

    Returns an EMPTY dict when the owner did not ask, rather than a disabled
    settings object -- so `FirstImageRequest`'s own defaults apply and no
    subseed is set anywhere downstream. `_hires_fields` established that shape
    and the acceptance criterion behind it: absent must be byte-identical to
    absent.
    """

    if variation is None:
        return {}
    return {
        "subseed": int(getattr(variation, "subseed", -1)),
        "subseed_strength": float(getattr(variation, "subseed_strength", 0.0)),
        "seed_resize_from_w": int(getattr(variation, "seed_resize_from_w", -1)),
        "seed_resize_from_h": int(getattr(variation, "seed_resize_from_h", -1)),
    }


def _auto_detail_fields(
    auto_detail: Any,
    *,
    resolve_detector: Any = None,
) -> dict[str, Any]:
    """Studio's Auto Detail vocabulary onto the runtime's slots, once.

    Absent means absent, exactly as for Hires: no group leaves
    `enable_adetailer` False and `ad_slots` empty, so nothing downstream can
    tell the field exists.

    Only ENABLED slots travel. A disabled slot is a true no-op and carrying an
    inert one would make the runtime responsible for a decision the owner
    already made, and would put a detector name in the dispatch log for a pass
    that never runs.

    Each slot's detector NAME is resolved to a path here, at the one seam that
    can: the resolver is bound to the model-root registry, which lives on the
    server and does not exist inside the generation runtime. A name that does
    not resolve travels with an EMPTY path rather than being dropped -- the
    orchestrator refuses it by index, which is a better error than a slot that
    silently disappears.
    """

    if auto_detail is None or not getattr(auto_detail, "enabled", False):
        return {"enable_adetailer": False, "ad_slots": ()}

    from .auto_detail import SlotSpec

    slots: list[Any] = []
    for index, slot in enumerate(getattr(auto_detail, "slots", ()) or (), start=1):
        if not getattr(slot, "enabled", False):
            continue
        name = str(getattr(slot, "detector", "") or "")
        path = ""
        if resolve_detector is not None and name:
            try:
                resolved = resolve_detector(name)
            except Exception:  # noqa: BLE001 - an unresolved name is refused, not raised here
                resolved = None
            path = "" if resolved is None else str(resolved)
        slots.append(
            SlotSpec(
                index=index,
                enabled=True,
                detector=name,
                detector_path=path,
                confidence=float(getattr(slot, "confidence", 0.3)),
                top_k=int(getattr(slot, "top_k", 0)),
                min_ratio=float(getattr(slot, "min_ratio", 0.0)),
                max_ratio=float(getattr(slot, "max_ratio", 1.0)),
                dilate_erode=int(getattr(slot, "dilate_erode", 4)),
                mask_blur=int(getattr(slot, "mask_blur", 6)),
                denoising_strength=float(getattr(slot, "denoising_strength", 0.30)),
                prompt=str(getattr(slot, "prompt", "") or ""),
                negative_prompt=str(getattr(slot, "negative_prompt", "") or ""),
                inpaint_padding=int(getattr(slot, "inpaint_padding", 32)),
                steps=int(getattr(slot, "steps", 0)),
                cfg=float(getattr(slot, "cfg", 0.0)),
            )
        )
    # Enabled with no enabled slot is OFF. Otherwise the runtime would advance
    # into an Auto Detail stage, run nothing, and report that it detailed.
    return {"enable_adetailer": bool(slots), "ad_slots": tuple(slots)}


def _hires_fields(hires: Any, *, base_cfg: float = 0.0) -> dict[str, Any]:
    """Studio's Hires vocabulary onto Neo's `hr_*` names, once.

    Absent means absent: no `hires` group leaves `enable_hr` False and every
    `hr_*` field at its dataclass default, so the base pass is byte-identical
    to what it was before the field existed. That is the phase's first
    acceptance criterion, and it is cheaper to guarantee here -- by not writing
    anything -- than to prove downstream.
    """

    if hires is None:
        return {"enable_hr": False}
    return {
        "enable_hr": bool(getattr(hires, "enabled", False)),
        "hr_scale": float(getattr(hires, "scale", 2.0)),
        "hr_upscaler": str(getattr(hires, "upscaler", "") or ""),
        "hr_second_pass_steps": int(getattr(hires, "second_pass_steps", 0)),
        "hr_denoising_strength": float(getattr(hires, "denoising_strength", 0.7)),
        "hr_sampler_name": str(getattr(hires, "sampler", "") or ""),
        "hr_scheduler": str(getattr(hires, "scheduler", "") or ""),
        "hr_prompt": str(getattr(hires, "prompt", "") or ""),
        "hr_negative_prompt": str(getattr(hires, "negative_prompt", "") or ""),
        # 0 INHERITS, resolved here rather than passed through: Neo has no
        # "inherit" value for this field, so leaving a 0 to reach it would
        # silently mean something else entirely.
        "hr_cfg": (float(getattr(hires, "cfg", 0.0)) or float(base_cfg)),
    }


def _translated_soft(settings: Any) -> Any:
    """The soft-inpainting group, in Studio's names, or None.

    A field-by-field copy rather than passing the contract object through,
    because the two sides of this boundary are deliberately different types --
    but a copy is exactly what silently loses a field, so
    `test_soft_inpainting` asserts every name on the contract arrives here.
    """

    if settings is None:
        return None
    from .generation_request import SoftInpaintOptions

    return SoftInpaintOptions(
        schedule_bias=float(getattr(settings, "schedule_bias", 1.0)),
        preservation=float(getattr(settings, "preservation", 0.5)),
        transition_contrast=float(getattr(settings, "transition_contrast", 4.0)),
        mask_influence=float(getattr(settings, "mask_influence", 0.0)),
        diff_threshold=float(getattr(settings, "diff_threshold", 0.5)),
        diff_contrast=float(getattr(settings, "diff_contrast", 2.0)),
    )


def translate_request(
    request: GenerationRequest,
    *,
    request_id: str,
    resolve_detector: Any = None,
) -> RequestTranslation:
    """Map a Studio request onto the headless one, losing and inventing nothing.

    Every Studio field is carried across verbatim. Fields the headless profile
    has and Studio does not are reported as backend-defaulted rather than
    silently chosen, and the four Tier-0 feature flags are pinned off.

    A seed of -1 is Studio's "pick one for me". The Tier-0 profile requires an
    explicit seed (`GENERATION_SEED_NOT_FIXED`), so it is refused here with a
    named field instead of being quietly replaced -- substituting a seed would
    make the reported seed a lie.
    """

    if int(request.seed) < 0:
        raise HeadlessError(
            "GENERATION_SEED_NOT_FIXED",
            "This backend requires an explicit seed; random seed is not supported.",
        )

    from .generation_request import Operation
    from .input_assets import decode_source

    # The operation decides whether a source is REQUIRED, and the decode is
    # what turns the browser's claim into something the sampler can hold. Both
    # happen here, at the boundary, so a refusal lands before job admission
    # rather than inside `process_images_inner`.
    raw_operation = str(getattr(request, "operation", "") or "txt2img").lower()
    try:
        operation = Operation(raw_operation)
    except ValueError:
        raise HeadlessError(
            "GENERATION_OPERATION_UNSUPPORTED",
            f"Studio does not know the operation {raw_operation!r}.") from None
    source = decode_source(
        getattr(request, "source_image", None),
        required=operation is not Operation.TXT2IMG,
    )
    # A source on a txt2img request is a contradiction, not a spare field: the
    # owner asked for one thing and supplied the input for another, and
    # dropping it silently is how a control comes to reach nothing.
    if operation is Operation.TXT2IMG and source is not None:
        raise HeadlessError(
            "UNSUPPORTED_OPERATION_COMBINATION",
            "A source image was supplied for a text-to-image job.")

    # The mask, for INPAINT only. Admission has already refused inpaint without
    # one and img2img with one, so this is a decode rather than a second
    # invariant check -- but it verifies the bytes and the geometry, because
    # admission saw only what the page CLAIMED.
    #
    # Carried on the source, which is where `SourceImage` has always had a slot
    # for it: the two arrive together, are verified against each other, and die
    # together. Studio names, not the engine's; the port does that mapping.
    inpaint = None
    if operation is Operation.INPAINT:
        from .generation_request import InpaintOptions, SoftInpaintOptions
        from .input_assets import decode_mask

        mask_image = decode_mask(getattr(request, "mask", None), source)
        source = replace(source, mask=mask_image, mask_present=True)
        settings = getattr(request, "inpaint", None)
        inpaint = InpaintOptions(
            mask_blur=int(getattr(settings, "mask_blur", 4) or 0),
            fill=int(getattr(settings, "fill", 1) or 0),
            full_resolution=bool(getattr(settings, "full_resolution", False)),
            padding=int(getattr(settings, "padding", 32) or 0),
            invert=bool(getattr(settings, "invert", False)),
            # Absent stays absent: no group means the port installs no bridge
            # and the engine keeps its own edge behaviour.
            soft=_translated_soft(getattr(settings, "soft", None)),
        )

    # AR4.9. The owner's output format, carried across the boundary. Every
    # layer above this had it and the writer still produced PNG, because the
    # translation dropped it here -- which is the seam this program keeps
    # finding: both endpoints correct, the join silent.
    #
    # Absent stays absent, like `variation` and `hires` below.
    _output_settings = getattr(request, "output", None)
    _output = None
    if _output_settings is not None:
        from .generation_request import OutputOptions

        _output = OutputOptions(
            format=str(getattr(_output_settings, "format", "png") or "png"),
            quality=int(getattr(_output_settings, "quality", 92) or 92),
            lossless=bool(getattr(_output_settings, "lossless", False)),
            # The same seam, the second time through. AR4.9's entire cost was
            # this function not carrying `output` across; carrying the group
            # but dropping one of its fields would be the identical defect at
            # a smaller scale, so this field has its own guard.
            embed_metadata=bool(
                getattr(_output_settings, "embed_metadata", True)),
        )

    headless = FirstImageRequest(
        request_id=request_id,
        operation=operation,
        source=source,
        inpaint=inpaint,
        output=_output,
        denoising_strength=float(getattr(request, "denoising_strength", 0.75)),
        model_id=str(request.model_id),
        positive_prompt=str(request.positive_prompt),
        negative_prompt=str(request.negative_prompt),
        seed=int(request.seed),
        # Carried as ONE group, like hires and auto_detail: absent stays
        # indistinguishable from absent, so a request without `variation`
        # produces the byte-identical image it did before the field existed.
        **_variation_fields(getattr(request, "variation", None)),
        steps=int(request.steps),
        width=int(request.width),
        height=int(request.height),
        cfg_scale=float(request.cfg_scale),
        distilled_cfg_scale=DEFAULT_DISTILLED_CFG_SCALE,
        # The owner's choice when they made one, the engine default when they
        # did not. Until P0.6 this was ALWAYS the default while the page
        # offered a sampler menu -- the control existed and reached nothing.
        sampler=str(getattr(request, "sampler", "") or "") or DEFAULT_SAMPLER,
        scheduler=str(getattr(request, "scheduler", "") or "") or DEFAULT_SCHEDULER,
        batch_size=1,
        output_count=1,
        **_hires_fields(
            getattr(request, "hires", None),
            base_cfg=float(request.cfg_scale),
        ),
        # The owner's toggle and the resolved slots, carried as ONE group --
        # the same shape as `_hires_fields`, so absent stays indistinguishable
        # from absent.
        **_auto_detail_fields(
            getattr(request, "auto_detail", None),
            resolve_detector=resolve_detector,
        ),
        enable_extensions=False,
        reference_image_enabled=False,
        # The owner's toggle, not a pin. Everything downstream of here already
        # accepted the flag -- `generation_port` passes it to `HeadlessProgress`
        # and the snapshot reports `preview_available` -- so this constant was
        # the single line that made the whole path dead.
        preview_enabled=bool(getattr(request, "preview_enabled", False)),
        # FIFTH instance of the defect the comments above this tuple describe
        # four times over -- sampler, hires, preview_enabled, auto_detail --
        # and it was caught the same way the fourth was: not by a test, but by
        # running the thing and watching an owner-facing control do nothing.
        #
        # The Settings toggle wrote to the store, the store was read at
        # assembly, and the field reached `forge_studio` GenerationRequest.
        # Then it stopped here. The port reads the TRANSLATED request, so a
        # field that never crossed this boundary is a field the upscaler
        # cannot see, and every job silently took the default.
        gpu_tile_compositing_requested=bool(
            getattr(request, "gpu_tile_compositing_requested", True)),
        # Passed through as-is, INCLUDING None. None is the job saying nothing
        # about Clip Skip, and coercing it to a number here would silently
        # start overriding an engine option on every job that never asked.
        clip_skip=getattr(request, "clip_skip", None),
    )
    return RequestTranslation(
        headless_request=headless,
        carried=STUDIO_REQUEST_FIELDS,
        backend_defaulted=BACKEND_DEFAULTED_FIELDS,
        pinned_off=PINNED_OFF_FLAGS,
    )



def _log_auto_detail_result(record: Any) -> None:
    """What the detectors FOUND, once the passes are done.

    The pair to the AUTODETAIL line at dispatch, and the more useful of the
    two: "found four, kept one" is the only evidence the owner's confidence
    and ratio settings did anything, and it cannot be read off the image.

    Imported inside the function for the reason `_log_dispatch` records --
    this module is headless and must not depend on the Studio package at
    import time.
    """

    try:
        outcomes = getattr(record.outcome, "auto_detail", ()) or ()
        if not outcomes:
            return
        from forge_studio.job_log import JobLog

        JobLog().auto_detail_result(record.job_id, outcomes=outcomes)
    except Exception:  # noqa: BLE001 - a log line never breaks a generation
        return


def _log_dispatch(job_id: str, translation: RequestTranslation) -> None:
    """The RESOLVED recipe, once, at the moment it is fixed.

    Here rather than in the coordinator because this is where defaults are
    APPLIED. The whole of P0.6 was about the gap between what a request asked
    for and what the engine ran; a line printed one layer up would report the
    request and hide exactly that gap.

    `forge_studio.job_log` is imported inside the function. This module is
    headless and must not acquire a dependency on the Studio package at import
    time -- the purity tests exist to keep that boundary, and a logger is not
    a reason to cross it.
    """

    try:
        from forge_studio.job_log import JobLog

        headless = translation.headless_request
        log = JobLog()
        log.dispatch(job_id, resolved={
            "sampler": headless.sampler,
            "scheduler": headless.scheduler,
            "steps": headless.steps,
            "width": headless.width,
            "height": headless.height,
            "seed": headless.seed,
            "preview_enabled": headless.preview_enabled,
        })
        if getattr(headless, "enable_hr", False):
            log.hires(job_id, recipe={
                "scale": getattr(headless, "hr_scale", None),
                "upscaler": getattr(headless, "hr_upscaler", None),
                "second_pass_steps": getattr(headless, "hr_second_pass_steps", None),
                "denoising_strength": getattr(headless, "hr_denoising_strength", None),
                "target": _hires_target(headless),
            })
        # The Auto Detail slots, beside the Hires recipe and for the same
        # reason: a job that runs three detector passes said nothing about
        # them, and the owner was left inferring it from a step count that
        # looks exactly like a second Hires pass.
        if getattr(headless, "enable_adetailer", False):
            log.auto_detail(job_id, slots=getattr(headless, "ad_slots", ()))
    except Exception:  # noqa: BLE001 - a log line never breaks a generation
        return


def _hires_target(headless: Any) -> str | None:
    """The size the second pass is aiming at, which is what an owner wants to
    read while a 4x pass sits with a frozen step counter."""

    width = getattr(headless, "hr_resize_x", 0) or 0
    height = getattr(headless, "hr_resize_y", 0) or 0
    if width and height:
        return f"{int(width)}x{int(height)}"
    scale = getattr(headless, "hr_scale", 0) or 0
    if scale:
        return f"{int(headless.width * scale)}x{int(headless.height * scale)}"
    return None


# ------------------------------------------------------ progress translation

#: Headless has nine lifecycle states; Studio's enum has five. Every non-terminal
#: headless stage is truthfully "running" to Studio, and the stage itself travels
#: in the event message so the distinction is reported rather than discarded.
STATE_PROJECTION: dict[HeadlessJobState, StudioJobState] = {
    HeadlessJobState.QUEUED: StudioJobState.QUEUED,
    HeadlessJobState.LOADING: StudioJobState.RUNNING,
    HeadlessJobState.CONDITIONING: StudioJobState.RUNNING,
    HeadlessJobState.SAMPLING: StudioJobState.RUNNING,
    # Hires is NOT a second public job. The book is explicit about it, and
    # the projection is where that is actually enforced: both second-pass
    # states collapse onto RUNNING, so the owner sees one job with a
    # changing stage label rather than a job that finishes and another
    # that starts.
    HeadlessJobState.HIRES_PREPARING: StudioJobState.RUNNING,
    HeadlessJobState.HIRES_SAMPLING: StudioJobState.RUNNING,
    # Auto Detail is not three more public jobs, for exactly the reason Hires
    # is not a second one. The book fixes the pipeline as ONE public job with
    # a changing stage, and this projection is where that is enforced: all
    # three slots collapse onto RUNNING, so the owner never sees a job finish
    # and another begin because a detector ran.
    HeadlessJobState.AUTO_DETAIL_1: StudioJobState.RUNNING,
    HeadlessJobState.AUTO_DETAIL_2: StudioJobState.RUNNING,
    HeadlessJobState.AUTO_DETAIL_3: StudioJobState.RUNNING,
    HeadlessJobState.DECODING: StudioJobState.RUNNING,
    HeadlessJobState.PUBLISHING: StudioJobState.RUNNING,
    HeadlessJobState.COMPLETED: StudioJobState.COMPLETED,
    HeadlessJobState.CANCELLED: StudioJobState.CANCELLED,
    HeadlessJobState.FAILED: StudioJobState.FAILED,
}


def project_state(state: HeadlessJobState) -> StudioJobState:
    return STATE_PROJECTION[state]


def project_progress(
    progress: HeadlessProgress, *, job_id: str, sequence: int
) -> ProgressEvent:
    """One Studio observation from one headless snapshot. Never advances it."""

    snapshot = progress.snapshot()
    fraction = snapshot.fraction
    percent = 0 if fraction is None else int(round(float(fraction) * 100))
    if snapshot.state is HeadlessJobState.COMPLETED:
        percent = 100
    percent = max(0, min(100, percent))

    error: StructuredError | None = None
    if snapshot.state is HeadlessJobState.FAILED:
        error = StructuredError(
            code=GENERATION_FAILED,
            message=str(snapshot.error or "Generation failed."),
        )

    return ProgressEvent(
        job_id=job_id,
        state=project_state(snapshot.state),
        sequence=sequence,
        progress=percent,
        # The headless stage label is the only place the nine-state lifecycle
        # survives the five-state projection, so it is carried deliberately.
        message=STAGE_LABELS.get(snapshot.state, snapshot.stage_label),
        error=error,
        step=snapshot.step or None,
        total_steps=snapshot.total_steps,
    )


# ------------------------------------------------------------------ session


@dataclass
class _JobRecord:
    """Everything one Studio job holds. Scalars, progress objects, an outcome.

    There are two progress objects, and that is not redundancy.
    `GenerationGateway.submit` constructs its **own** `HeadlessProgress` for the
    request id and hands that one to the port -- so a progress object created
    here is never written to by the run. `local_progress` covers the window
    before the gateway exists (queued, and cancel-before-start);
    `gateway_progress` is adopted the moment the gateway has one, and
    `progress_view` always answers from whichever is authoritative.

    Reading the wrong one is exactly the bug this shape prevents: the job would
    report `queued` forever while the real run completed beside it.
    """

    job_id: str
    headless_request: FirstImageRequest
    local_progress: HeadlessProgress
    translation: RequestTranslation
    sequence: int = 0
    gateway_progress: HeadlessProgress | None = None
    outcome: GenerationOutcome | None = None
    failure: StructuredError | None = None
    started: bool = False
    finished: bool = False

    @property
    def progress_view(self) -> HeadlessProgress:
        return self.gateway_progress or self.local_progress


def _attach_detector_resolver(session: Any, resolver: Any) -> None:
    """Give a session the name -> path resolver, if it can hold one.

    A module-level function rather than a method, so a host can attach the
    resolver to whatever it has -- including the doubles that predate the
    field -- without every one of them growing a setter.
    """

    try:
        session._detector_resolver = resolver  # noqa: SLF001 - documented seam
    except Exception:  # noqa: BLE001 - a session that cannot hold one resolves nothing
        return


class HeadlessGenerationSession:
    """The injected, non-live generation collaborator for the Studio adapter.

    Everything beneath the adapter arrives here by injection: the generation
    port (through the gateway), the resident-model description, the executor,
    the clock, the identifier source, the result root, an optional result
    materialiser, and a cleanup hook. Constructing one performs no model,
    device, or filesystem work.
    """

    def __init__(
        self,
        *,
        gateway: GenerationGateway | None = None,
        resident_model: ResidentModel | None = None,
        result_root: Path | None = None,
        result_writer: Callable[[GenerationOutcome, Path], None] | None = None,
        executor: object | None = None,
        clock: Callable[[], float] | None = None,
        identifiers: Callable[[], str] | None = None,
        cleanup: Callable[[], None] | None = None,
        options_available: bool = True,
        progress_installed: bool = True,
        generation_authorized: bool = True,
    ) -> None:
        self._gateway = gateway if gateway is not None else GenerationGateway()
        self._resident_model = resident_model
        self._result_root = Path(result_root) if result_root is not None else None
        self._result_writer = result_writer
        self._executor = executor
        self._clock = clock or time.monotonic
        self._identifiers = identifiers or default_result_identifier
        self._cleanup = cleanup
        self._options_available = bool(options_available)
        self._progress_installed = bool(progress_installed)
        # Validation-level authorization only. The port still decides, and the
        # default port refuses, so this cannot by itself enable generation.
        self._generation_authorized = bool(generation_authorized)
        self._jobs: dict[str, _JobRecord] = {}
        self._lock = Lock()
        self._closed = False
        self.cleanup_calls = 0

    # -- reporting ---------------------------------------------------------

    @property
    def resident_model(self) -> ResidentModel | None:
        return self._resident_model

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def describe(self) -> dict[str, Any]:
        """Scalars only. No path, no object, no repr."""

        model = self._resident_model
        return {
            "port": type(self._gateway.port).__name__,
            "resident_model_configured": model is not None,
            "resident": bool(getattr(model, "resident", False)),
            "result_root_configured": self._result_root is not None,
            "result_writer_injected": self._result_writer is not None,
            "executor_injected": self._executor is not None,
            "options_available": self._options_available,
            "progress_installed": self._progress_installed,
            "generation_authorized": self._generation_authorized,
            "closed": self.closed,
            "job_count": len(self._jobs),
        }

    def capability_for(self, model_id: str, operation: str) -> ModelCapability:
        model = self._require_model(model_id)
        return ModelCapability(
            model_id=model.model_id,
            operation=operation,
            dimension_alignment=DIMENSION_ALIGNMENT,
            # None means UNBOUNDED, which the contract already models. These
            # were literals repeating a validator limit that no longer exists;
            # leaving them would keep the page's controls capped at 1536 while
            # the server happily accepted 2304.
            minimum_dimension=1,
            maximum_dimension=None,
            maximum_pixels=None,
            normalizes_dimensions=False,
            is_mock=False,
        )

    # -- job lifecycle -----------------------------------------------------

    def submit(self, request: GenerationRequest) -> GenerationJobIdentity:
        self._require_open()
        model = self._require_model(str(request.model_id))
        job_id = self._identifiers()

        try:
            translation = translate_request(
                request,
                request_id=job_id,
                resolve_detector=getattr(self, "_detector_resolver", None),
            )
        except HeadlessError as exc:
            raise self._studio_error(exc) from None

        record = _JobRecord(
            job_id=job_id,
            headless_request=translation.headless_request,
            local_progress=HeadlessProgress(
                job_id,
                preview_enabled=translation.headless_request.preview_enabled,
            ),
            translation=translation,
        )
        with self._lock:
            self._jobs[job_id] = record
            # Published BEFORE the work runs, which is the whole point. The
            # identity below is only returned once `work()` has finished --
            # this submit is blocking -- so a caller that waits for the return
            # value learns the job's id at the moment it stops needing it.
            #
            # Progress was unobservable for the entire duration of every job
            # because of that: the coordinator had no backend id to poll with
            # until the job was already over, so `/api/jobs/<id>` reported
            # nothing and then everything at once.
            self._in_flight_job_id = job_id

        _log_dispatch(job_id, translation)

        work = self._make_work(record, model)
        executor = self._executor
        if executor is not None and hasattr(executor, "submit"):
            executor.submit(work)
        else:
            work()

        return GenerationJobIdentity(
            job_id=job_id, state=project_state(record.progress_view.state)
        )

    def _adopt_gateway_progress(self, record: _JobRecord) -> None:
        """Take over the progress object the gateway actually drove."""

        try:
            record.gateway_progress = self._gateway.progress_for(record.job_id)
        except HeadlessError:
            # Validation failed before the gateway created one. The local
            # object stays authoritative, which is the truthful answer.
            record.gateway_progress = None

    def _adopt_if_available(self, record: _JobRecord) -> None:
        """Adopt the gateway's progress the moment there IS one.

        `_JobRecord`'s own docstring has always said the gateway object is
        adopted "the moment the gateway has one", and that "reading the wrong
        one is exactly the bug this shape prevents: the job would report
        `queued` forever while the real run completed beside it."

        Adoption only ever happened AFTER `gateway.submit()` returned, which
        is at terminal. So every job did report `queued` for its entire life
        while the real run completed beside it -- the bug the shape was built
        to prevent, described accurately in the docstring of the thing that
        had it. Found by the live P0.7 re-run; no unit test could see it,
        because they all drove `HeadlessProgress` directly.

        Called from the observation paths rather than pushed from the gateway:
        an observer that needs the authoritative object asks for it, and
        `progress_for` is a dict read. Once adopted it sticks, so this is one
        failed lookup per poll until the gateway has one and none afterwards.
        """

        if record.gateway_progress is None and record.started:
            self._adopt_gateway_progress(record)

    def _make_work(self, record: _JobRecord, model: ResidentModel):
        def run() -> None:
            if record.local_progress.terminal:
                # Cancelled before this ever ran. Executing now would drive a
                # job the caller has already been told is finished.
                record.finished = True
                return
            record.started = True
            try:
                record.outcome = self._gateway.submit(
                    record.headless_request,
                    model,
                    options_available=self._options_available,
                    progress_installed=self._progress_installed,
                    result_root_writable=self._result_root is not None,
                    generation_authorized=self._generation_authorized,
                )
                self._adopt_gateway_progress(record)
                _log_auto_detail_result(record)
            except HeadlessError as exc:
                self._adopt_gateway_progress(record)
                # Scalar only. The exception is not retained anywhere.
                record.failure = StructuredError(
                    code=studio_code_for(exc.code),
                    message=str(exc.message),
                    field=_CODE_FIELD.get(exc.code),
                )
                progress = record.progress_view
                if not progress.terminal:
                    if record.failure.code == GENERATION_CANCELLED:
                        progress.mark_cancelled()
                    else:
                        progress.mark_failed(str(exc.message))
            except BaseException as exc:  # noqa: BLE001 - the job must terminate
                self._adopt_gateway_progress(record)
                record.failure = StructuredError(
                    code=GENERATION_FAILED,
                    message=f"{type(exc).__name__}",
                )
                progress = record.progress_view
                if not progress.terminal:
                    progress.mark_failed(type(exc).__name__)
            finally:
                record.finished = True
                # "In flight" has to mean in flight. This was SET at submit and
                # never cleared, so it named the last job ever submitted, for
                # the rest of the process.
                #
                # `JobCoordinator.describe` borrows it to merge progress before
                # the blocking submit returns a backend id -- so a job asked
                # about after this one finished was answered with THIS one's
                # terminal reading. Live, a fresh 30-step job reported
                # `state=running, message="Completed", step=15/15`: the
                # previous Hires job's second pass, under the new job's id.
                with self._lock:
                    if self._in_flight_job_id == record.job_id:
                        self._in_flight_job_id = None

        return run

    def preview_frame(self, job_id: str) -> tuple[int, str | None]:
        """The latest decoded preview frame for one job, and its id.

        Reads through `progress_view`, so it answers from the gateway progress
        object once one has been adopted -- the same authority `poll` uses. An
        unknown job is not an error here: the socket asks about whatever it
        last saw active, and that job may have been reaped between two frames.
        """

        try:
            record = self._require_job(job_id)
        except HeadlessError:
            return (0, None)
        # The same adoption the poll path needs, for the same reason: the
        # local placeholder never has a frame, because nothing drives it.
        self._adopt_if_available(record)
        return record.progress_view.preview_frame()

    def poll(self, job_id: str) -> ProgressEvent:
        record = self._require_job(job_id)
        self._adopt_if_available(record)
        with self._lock:
            record.sequence += 1
            sequence = record.sequence
        event = project_progress(record.progress_view, job_id=job_id, sequence=sequence)
        # `record.failure` WINS. It was built from the raised code at :726 --
        # mapped through `studio_code_for` and carrying `_CODE_FIELD`'s field
        # name -- while `project_progress` can only see a HeadlessProgress,
        # whose `error` is the bare sanitized string `mark_failed` stored. It
        # therefore hardcodes GENERATION_FAILED.
        #
        # This condition used to include `event.error is None`, meaning "only
        # when the projection had nothing better". The projection ALWAYS has
        # something for a failed job, so the branch was unreachable for every
        # failure there has ever been, and the good error was computed and
        # discarded on every poll.
        #
        # Live: a request for 60 steps against the old MAX_STEPS=40 cap
        # (removed 2026-08-20) reached the owner
        # as `{"code": "GENERATION_FAILED", "message": "60"}`. The right
        # answer -- REQUEST_UNSUPPORTED, field "steps" -- existed in the same
        # object at the same moment.
        if record.failure is not None and event.state in (
            StudioJobState.FAILED,
            StudioJobState.CANCELLED,
        ):
            event = ProgressEvent(
                job_id=event.job_id,
                state=event.state,
                sequence=event.sequence,
                progress=event.progress,
                message=event.message,
                error=record.failure if event.state is StudioJobState.FAILED else None,
                step=event.step,
                total_steps=event.total_steps,
            )
        return event

    def cancel(self, job_id: str) -> CancellationResult:
        record = self._require_job(job_id)
        if record.progress_view.terminal:
            return CancellationResult(
                job_id=job_id,
                cancelled=False,
                state=project_state(record.progress_view.state),
                message="Job is already terminal.",
            )
        record.progress_view.request_cancellation()
        if not record.started:
            # Nothing has run yet, so nothing has to observe the flag: the job
            # can be taken to its terminal state directly and truthfully.
            record.progress_view.mark_cancelled()
            record.finished = True
        return CancellationResult(
            job_id=job_id,
            cancelled=True,
            state=project_state(record.progress_view.state),
            message="Cancellation requested.",
        )

    def result(self, job_id: str) -> GeneratedResult:
        record = self._require_job(job_id)
        if record.failure is not None:
            raise self._raise_studio(record.failure)
        outcome = record.outcome
        if outcome is None or not record.progress_view.terminal:
            raise self._raise_studio(
                StructuredError(
                    code=RESULT_PUBLICATION_FAILED,
                    message="That job has produced no result.",
                )
            )
        root = self._result_root
        if root is None:
            raise self._raise_studio(
                StructuredError(
                    code=RESULT_PUBLICATION_FAILED,
                    message="No result root is configured.",
                )
            )
        path = root / outcome.result_relative_location
        if self._result_writer is not None and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self._result_writer(outcome, path)
        if not path.is_file():
            raise self._raise_studio(
                StructuredError(
                    code=RESULT_PUBLICATION_FAILED,
                    message="The result is not available.",
                )
            )
        return GeneratedResult(
            job_id=job_id,
            state=StudioJobState.COMPLETED,
            mime_type=outcome.media_type,
            # Studio mints the opaque handle from this; the path never reaches
            # a response, and the presentation layer strips it regardless.
            output_path=str(path),
            metadata={
                "schema_version": "studio-headless-result/v1",
                "width": outcome.width,
                "height": outcome.height,
                "seed": outcome.seed,
                "request_id": outcome.request_id,
                # The engine's parameter string, carried back so the page can
                # say what it made. Four owner surfaces read this and every one
                # of them was permanently blank without it: the output info
                # bar, Copy Seed, and both Recycle buttons.
                #
                # The OUTBOUND half of the same seam the field above guards.
                # This is the return leg, and it is dropped just as silently.
                "infotext": str(getattr(outcome, "infotext", "") or ""),
                # Section 9: requested and effective are different facts, and
                # a re-run on another card may legitimately answer
                # differently. Recording only the preference would make the
                # result claim something it did not do. Flat scalars, because
                # every metadata value is asserted to be one.
                "gpu_tile_composite_requested": bool(
                    outcome.gpu_tile_composite_requested),
                "upscale_composite_effective": str(
                    outcome.upscale_composite_effective),
                "upscale_composite_reason": str(
                    outcome.upscale_composite_reason),
                # The RESOLVED Hires recipe, present only when a second pass
                # actually ran. Absent means the result is a single-pass image,
                # which is a different claim from "Hires ran with defaults" and
                # has to stay distinguishable -- `width`/`height` alone cannot
                # tell a 768 base from a 512 base doubled to... no, from a 512
                # base at 1.5. The recipe is how a result explains itself.
                #
                # Scalars only. No upscaler PATH, no checkpoint path, no model
                # filename: the book requires the recipe "without private
                # paths", and an upscaler is identified by the name the
                # registry offers, which is already what the owner chose from.
                **_hires_metadata(self._require_job(job_id).translation),
                # The RESOLVED Auto Detail outcome, present only when a slot
                # actually ran -- and reporting what the detectors FOUND, not
                # what the request asked for. The request is already in the
                # job; "four faces detected, one kept" is the fact a reader
                # cannot reconstruct.
                **_auto_detail_metadata(
                    getattr(outcome, "auto_detail", ()),
                ),
            },
        )

    def translation_for(self, job_id: str) -> RequestTranslation:
        return self._require_job(job_id).translation

    @property
    def in_flight_job_id(self) -> str | None:
        """The most recently submitted backend job id, or None.

        Deliberately a READ rather than a callback threaded down from the
        coordinator. A callback would have changed `submit`'s signature, and
        every test double implements it -- the same trap that broke 25 tests
        when `icc_profile=` was added to an encoder call whose docstring said
        its shape was load-bearing. A new optional property is invisible to a
        double that does not have it.
        """

        return getattr(self, "_in_flight_job_id", None)

    # -- teardown ----------------------------------------------------------

    def close(self) -> None:
        """Idempotent. A raising cleanup hook is reported, not swallowed."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for record in jobs:
            progress = record.progress_view
            if not progress.terminal:
                progress.request_cancellation()
                progress.mark_cancelled()
        if self._cleanup is None:
            return
        self.cleanup_calls += 1
        try:
            self._cleanup()
        except BaseException as exc:  # noqa: BLE001 - reported as a scalar
            raise self._raise_studio(
                StructuredError(
                    code=CLEANUP_FAILED,
                    message=f"Backend cleanup failed: {type(exc).__name__}",
                )
            ) from None

    # -- internals ---------------------------------------------------------

    def _require_open(self) -> None:
        if self.closed:
            raise self._raise_studio(
                StructuredError(
                    code=BACKEND_SHUTDOWN,
                    message="The Studio backend has shut down.",
                )
            )

    def _require_model(self, model_id: str) -> ResidentModel:
        model = self._resident_model
        if model is None or not model.resident:
            raise self._raise_studio(
                StructuredError(
                    code=MODEL_NOT_SELECTED,
                    message="No model session is resident.",
                    field="model_id",
                )
            )
        if model_id and model.model_id != model_id:
            raise self._raise_studio(
                StructuredError(
                    code=MODEL_NOT_SELECTED,
                    message="That model is not the resident session.",
                    field="model_id",
                )
            )
        return model

    def _require_job(self, job_id: str) -> _JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise self._raise_studio(
                StructuredError(code=JOB_NOT_FOUND, message="That job is not known.")
            )
        return record

    @staticmethod
    def _studio_error(exc: HeadlessError):
        from .studio_adapter import HeadlessBackendError

        error = StructuredError(
            code=studio_code_for(exc.code),
            message=str(exc.message),
            field=_CODE_FIELD.get(exc.code),
        )
        failure = HeadlessBackendError(error, http_status=400)
        # The originating runtime code stays in-process for logs and tests. It
        # is never part of the StructuredError, so it cannot reach the wire.
        failure.origin_code = exc.code
        return failure

    @staticmethod
    def _raise_studio(error: StructuredError):
        from .studio_adapter import HeadlessBackendError

        status = 409 if error.code in (BACKEND_NOT_READY, BACKEND_SHUTDOWN) else 400
        if error.code == JOB_NOT_FOUND:
            status = 404
        return HeadlessBackendError(error, http_status=status)


__all__ = (
    "BACKEND_DEFAULTED_FIELDS",
    "BACKEND_NOT_READY",
    "BACKEND_SHUTDOWN",
    "CLEANUP_FAILED",
    "GENERATION_CANCELLED",
    "GENERATION_FAILED",
    "JOB_NOT_FOUND",
    "MODEL_LOAD_FAILED",
    "MODEL_NOT_SELECTED",
    "PINNED_OFF_FLAGS",
    "REQUEST_UNSUPPORTED",
    "RESULT_IDENTIFIER_PREFIX",
    "RESULT_PUBLICATION_FAILED",
    "STATE_PROJECTION",
    "STUDIO_ERROR_CODES",
    "STUDIO_REQUEST_FIELDS",
    "HeadlessGenerationSession",
    "RequestTranslation",
    "default_result_identifier",
    "project_progress",
    "project_state",
    "studio_code_for",
    "translate_request",
)
