"""The bounded first-image request profile, and everything checked before it runs.

Nothing here is chosen by taste. Every default is traced to a line of retained
Forge source, recorded in `DERIVATIONS`, and asserted by test against that
source -- so if Forge changes a default, the test fails rather than the profile
silently drifting.

The request is frozen after construction. Validation happens once, before the
generation port, and returns a list of every problem rather than the first one,
because discovering blockers one failure at a time is what this milestone exists
to stop.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .contracts import HeadlessError


class Operation(str, Enum):
    """What kind of generation this is.

    One member until Gate 2. `INPAINT` is a distinct operation rather than a
    flag on IMG2IMG because it has a REQUIRED input the other does not -- a
    request without a mask is not an inpaint with a default, it is a different
    job -- and because refusing early needs something to refuse ON.

    Soft inpainting is deliberately NOT a member. It is a settings mode under
    INPAINT: it changes how the mask is applied, not what the operation is, and
    a separate member would have made every consumer branch on a distinction
    the engine does not draw.
    """

    TXT2IMG = "txt2img"
    IMG2IMG = "img2img"
    INPAINT = "inpaint"


#: Where each profile value comes from. Quoted in evidence and asserted by test.
DERIVATIONS: dict[str, str] = {
    "sampler": (
        "modules/sd_samplers_kdiffusion.py:21 -- ('Euler', 'sample_euler', "
        "['k_euler'], {}). Deterministic: non-ancestral, no brownian noise, "
        "and no second-order flag."
    ),
    "scheduler": (
        "modules/sd_schedulers.py:268 -- Scheduler('automatic', 'Automatic', "
        "None). Euler declares no {'scheduler': ...} override, so Automatic is "
        "the scheduler Forge itself would apply."
    ),
    "cfg_scale": (
        "modules/ui.py:242 -- the txt2img CFG slider ships value=6.0. "
        "(StableDiffusionProcessing.cfg_scale defaults to 7.0 in code; 6.0 is "
        "what a Forge user actually gets, so it is the conservative choice.)"
    ),
    "distilled_cfg_scale": (
        "modules/ui.py:241 ships value=3.0, and "
        "huggingface_guess/model_list.py:473 declares Anima "
        "sampling_settings['shift'] = 3.0. Two independent sources agree, and "
        "for Anima this value *is* the shift: modules/processing.py:1342 calls "
        "sd_model.set_shift(shift=self.distilled_cfg_scale) because "
        "backend/diffusion_engine/anima.py:39 sets use_shift = True."
    ),
    "dimensions": (
        "768x768 is a multiple of the VAE factor of 8, which is the only "
        "alignment the engine actually imposes. patch_spatial=2 imposes "
        "NOTHING: backend/nn/anima.py:478 pads the latent up to the patch "
        "size and line 510 crops the result back to the requested shape."
    ),
    "steps": "Bounded by this profile, not by Forge: 12 keeps the first run short.",
    "batch_size": "Fixed at 1 by this profile.",
}

DEFAULT_SAMPLER = "Euler"
DEFAULT_SCHEDULER = "Automatic"
DEFAULT_CFG_SCALE = 6.0
DEFAULT_DISTILLED_CFG_SCALE = 3.0

#: The VAE downscale factor, and nothing else.
#:
#: This was 16 -- VAE factor 8 multiplied by `patch_spatial` 2 -- and that
#: multiplication was a mistake. The patch size is not a constraint the caller
#: has to satisfy, because the model satisfies it itself:
#:
#:     backend/nn/anima.py:470   orig_shape = list(x.shape)
#:     backend/nn/anima.py:478   pad_to_patch_size(x, (..., 2, 2))
#:     backend/nn/anima.py:510   unpatchify(...)[..., :orig_shape[-2], :orig_shape[-1]]
#:
#: The latent is padded up to the patch multiple and the result is cropped
#: back to exactly what was asked for. `pad_to_patch_size` (backend/utils.py:300)
#: exists precisely to remove the requirement that was inferred from it.
#:
#: Reported by an owner who could generate 1024x1368 in the Forge Studio
#: extension and could not here. 1368 is 8 x 171: valid for the VAE, odd for
#: the patch grid, and therefore padded -- which is why the extension, running
#: the same engine without this precheck, simply produced the image.
#:
#: The cost of the error was not a warning. The request was accepted by the
#: page, queued, and failed as REQUEST_UNSUPPORTED, so a size the engine
#: handles natively was refused by Studio alone.
DIMENSION_ALIGNMENT = 8


def _snap_to_alignment(value: float) -> int:
    """The size Neo will use, given a scale that does not divide evenly.

    Mirrors `sRound` (modules/resolution.py:33) rather than calling it:
    `resolution_step()` reads `opts`, which imports `modules.shared` and
    therefore Torch, and this module is imported to VALIDATE a request long
    before any of that is wanted.

    Studio's constant rather than Neo's configurable step, deliberately -- the
    base-dimension refusal above already commits this contract to 8, and two
    different alignments inside one validator would be worse than one that is
    occasionally conservative.
    """

    return int(math.floor(value / DIMENSION_ALIGNMENT + 0.5)) * DIMENSION_ALIGNMENT
#: NOT limits. Studio refuses no size in either direction -- owner decision,
#: 2026-08-16. These remain only as the sensible numbers a fresh control is
#: seeded with, and nothing validates against them.
DEFAULT_MIN_DIMENSION = 256
DEFAULT_MAX_DIMENSION = 1536

#: `MAX_STEPS = 40` used to sit here, with no comment and no source, gating
#: base steps, Auto Detail steps and Hires steps from one place. Removed
#: 2026-08-20 by owner ruling: "we should have no restrictions like that."
#:
#: Nothing supported the number. Studio's own core offers 1-150 on its sampling
#: slider (`modules/processing_scripts/sampler.py:27`) and 0-150 for Hires
#: steps (`modules/ui.py:254`), and `modules/processing.py` asserts nothing
#: about steps at all. It was refused at EXECUTION while the control and
#: admission both advertised 150, so a 60-step job was accepted, queued, shown
#: as running, and only then failed.
#:
#: It also sat directly beneath the dimension note above, which had already
#: made this exact argument in 2026-08-16 and was never applied to it.
#:
#: There is no replacement constant. A larger arbitrary ceiling is the same
#: defect with a longer leash.

#: The PRODUCT range for a Hires target, which is Forge's own. Not this
#: machine's range.
#:
#: This was briefly 1.5-2.0, derived from what fits on one 16 GB card running
#: Anima. That was the wrong place for it: a constant here caps every install,
#: so a larger card would have been refused a pass it could comfortably run,
#: forever, because of hardware it has never met.
#:
#: What is genuinely fixed lives elsewhere and stays: alignment to the VAE's
#: factor of 8 is refused unconditionally.
#:
#: What varies with the machine is deliberately NOT answered by a second
#: constant here. There is no validated model of what a Hires pass costs on
#: an arbitrary card, and inventing one would replace a bad limit with a
#: confident-looking guess. Instead the failure is made LEGIBLE at dispatch:
#: an out-of-memory during the second pass becomes a named error carrying the
#: measured free and total VRAM, so the owner is told what happened and what
#: it would have taken -- rather than meeting the exit-139 crash this project
#: has already spent a session disambiguating once.
HIRES_SCALE_MIN = 1.0
#: No ceiling. This was 4.0 -- Forge's own slider stop, adopted as "the PRODUCT
#: range" by the note above, which in the same breath explains why a constant
#: here "caps every install... because of hardware it has never met". That
#: argument applies to 4.0 exactly as it applied to the 2.0 it replaced; the
#: only difference is whose slider the number came from.
#:
#: A scale multiplies output resolution, so the real bound is VRAM, and this
#: file already says where that is answered honestly: an out-of-memory becomes
#: a named error carrying the measured free and total.
HIRES_SCALE_MAX = float("inf")

#: Retired with the base ceiling. Nothing reads it as a limit any more.
MAX_HIRES_DIMENSION = None


@dataclass(frozen=True)
class OutputOptions:
    """How the finished picture is encoded on its way to disk.

    STUDIO'S NAMES, all the way to the port, for the same reason
    `InpaintOptions` gives: `test_product_truthfulness` keeps the encoder's
    spellings out of the translator, and the one module allowed to map them is
    the port -- `live_generation_port.py::OUTPUT_FORMATS`.

    Absent is not the same as `format="png"`. A request with no group at all
    makes the port take its default path, which is what keeps a default
    install byte-identical to before this existed.
    """

    #: `png`, `jpeg` or `webp`. Admission has already refused anything else.
    format: str = "png"
    #: 1-100. Ignored for PNG, and for WebP when `lossless` is set.
    quality: int = 92
    #: WebP only.
    lossless: bool = False
    #: Whether the saved file carries the generation parameters. True by
    #: default -- see the note on Studio's `OutputSettings`, which is where the
    #: reasoning lives; repeating it here would let the two drift.
    embed_metadata: bool = True


@dataclass(frozen=True)
class InpaintOptions:
    """How an inpaint job treats the edges of the mask.

    STUDIO'S NAMES, deliberately, all the way to the port. `test_product_
    truthfulness` forbids the engine's spellings in the translator, and that
    guard is right: the one place the two vocabularies are allowed to meet is
    the port that builds the processing object. Mapping here instead would put
    engine identifiers in a module whose whole job is to be independent of
    them.

    The defaults mirror the inpaint bar's, not the engine's class defaults,
    which differ on fill, area and padding. See
    `forge_studio.contracts.InpaintSettings` for why, and note that the
    collector sends all four, so this only affects a caller who omits them.
    """

    mask_blur: int = 4
    #: 0 flat fill, 1 what is already there, 2 latent noise, 3 latent nothing.
    fill: int = 1
    #: Crop to the mask, sample it at full resolution, composite back.
    full_resolution: bool = False
    padding: int = 32
    invert: bool = False
    #: Gradual boundary blending, or None when the owner did not ask for it.
    #:
    #: CARRIED HERE, and that is the whole point of this field existing. WP1.6
    #: added the group to the Studio contract and to the port, and this
    #: translation copied the five fields above and dropped it -- so the port
    #: read None, never installed the bridge, and two live jobs that differed
    #: only in this setting came back byte-identical. Admission was right and
    #: the port was right; the seam between them was not.
    soft: "SoftInpaintOptions | None" = None


@dataclass(frozen=True)
class SoftInpaintOptions:
    """Blend the mask boundary gradually rather than at full strength.

    STUDIO'S NAMES all the way to the port, like `InpaintOptions` above. These
    happen to be the engine's UI LABELS -- "Schedule bias" and the rest -- and
    not its field names, which the port maps positionally.

    The defaults are the engine's own, `SoftInpaintingSettings(1, 0.5, 4, 0,
    0.5, 2)`, and match what the page's sliders have always shown.
    """

    schedule_bias: float = 1.0
    preservation: float = 0.5
    transition_contrast: float = 4.0
    mask_influence: float = 0.0
    diff_threshold: float = 0.5
    diff_contrast: float = 2.0


@dataclass(frozen=True)
class SourceImage:
    """The decoded, VERIFIED source for an img2img or inpaint job.

    Studio's `InputAsset` carries what the browser SAID. This carries what the
    bytes actually are, established by decoding them here where PIL is allowed.
    The two are separate types on purpose: believing a declared width is how a
    request ends up allocating for one geometry and sampling another.

    `image` and `mask` are PIL images by the time this exists. Nothing above
    the headless boundary ever holds one.
    """

    image: Any = None
    mask: Any = None
    width: int = 0
    height: int = 0
    media_type: str = ""
    #: The mask, once decoded, as the engine reads it: white regenerates.
    mask_present: bool = False


@dataclass(frozen=True)
class FirstImageRequest:
    """One immutable txt2img request. Frozen, so it cannot drift after checks."""

    request_id: str
    model_id: str
    operation: Operation = Operation.TXT2IMG
    #: The decoded source, or None for txt2img. Verified before it gets here.
    source: "SourceImage | None" = None
    #: How to write the result, or None for "as it has always been written".
    #: Absent stays absent: the port takes its PNG path and the file is
    #: byte-identical to one produced before this field existed.
    output: "OutputOptions | None" = None
    #: Only read when a source exists. Forge's own default is 0.75.
    denoising_strength: float = 0.75
    #: Present only for INPAINT, in STUDIO's vocabulary -- the port maps it to
    #: the engine's. Absent means absent: nothing downstream sets a single mask
    #: field, so an img2img request is byte-identical to one made before this
    #: existed.
    inpaint: "InpaintOptions | None" = None
    positive_prompt: str = ""
    negative_prompt: str = ""
    seed: int = 0
    #: Variation seed. Neo's spelling and Neo's defaults, read from the BASE
    #: `StableDiffusionProcessing` -- which is why these work for txt2img and
    #: why `denoising_strength` (an img2img concept) does not belong beside
    #: them. `subseed_strength` 0 disables the whole group: Neo records no
    #: variation at all in that case, so the other three are inert.
    subseed: int = -1
    subseed_strength: float = 0.0
    seed_resize_from_w: int = -1
    seed_resize_from_h: int = -1
    steps: int = 12
    width: int = 768
    height: int = 768
    batch_size: int = 1
    cfg_scale: float = DEFAULT_CFG_SCALE
    distilled_cfg_scale: float = DEFAULT_DISTILLED_CFG_SCALE
    sampler: str = DEFAULT_SAMPLER
    scheduler: str = DEFAULT_SCHEDULER
    enable_hr: bool = False
    enable_adetailer: bool = False
    enable_extensions: bool = False
    reference_image_enabled: bool = False
    preview_enabled: bool = False
    #: The owner's GPU tile compositing preference, snapshotted server-side.
    #: Defaults True because the compositor is on by default and the runtime
    #: preflight -- not this flag -- decides whether it is safe.
    gpu_tile_compositing_requested: bool = True
    #: Clip Skip for this job, or None to leave the engine option alone.
    #:
    #: Consumed by `live_generation_port.generate`, which passes it into
    #: `job_scope` as `CLIP_stop_at_last_layers`. It is deliberately NOT put on
    #: the processing object: Neo conditions from `opts` and writes the
    #: infotext from `p`, so setting `p.clip_skip` would move the metadata
    #: without moving the image.
    clip_skip: int | None = None
    output_count: int = 1
    #: The Hires second pass, in NEO's spelling. Studio's own vocabulary lives
    #: in `HiresSettings`; the translation happens once, at the boundary, so
    #: neither side has to know the other's names.
    #:
    #: All inert while `enable_hr` is False, which is the phase's first
    #: acceptance criterion: Hires off must be byte-identical to Hires absent.
    hr_scale: float = 2.0
    hr_upscaler: str = ""
    hr_second_pass_steps: int = 0
    hr_denoising_strength: float = 0.7
    hr_sampler_name: str = ""
    hr_scheduler: str = ""
    hr_prompt: str = ""
    hr_negative_prompt: str = ""
    #: 0 means "inherit the base cfg_scale"; resolved at translation.
    hr_cfg: float = 0.0
    #: The Auto Detail slots, already RESOLVED and admitted.
    #:
    #: `auto_detail.SlotSpec` objects, carrying both the catalogue name and
    #: the filesystem path the detector was resolved to. The path is here
    #: because `detector_adapter.detect` requires one and only the catalogue
    #: may produce one -- and it is deliberately absent from `to_dict` below,
    #: which is the loggable view.
    #:
    #: Empty while Auto Detail is off, so an off job is byte-identical to one
    #: from before the field existed, exactly as `hr_*` are inert.
    ad_slots: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """A loggable view. Prompt text is deliberately excluded."""
        return {
            "request_id": self.request_id,
            "model_id": self.model_id,
            "operation": self.operation.value,
            "seed": self.seed,
            "steps": self.steps,
            "width": self.width,
            "height": self.height,
            "batch_size": self.batch_size,
            "cfg_scale": self.cfg_scale,
            "distilled_cfg_scale": self.distilled_cfg_scale,
            "sampler": self.sampler,
            "scheduler": self.scheduler,
            "enable_hr": self.enable_hr,
            "enable_adetailer": self.enable_adetailer,
            "enable_extensions": self.enable_extensions,
            "reference_image_enabled": self.reference_image_enabled,
            "preview_enabled": self.preview_enabled,
            "output_count": self.output_count,
            "positive_prompt_length": len(self.positive_prompt),
            "negative_prompt_length": len(self.negative_prompt),
            # Slot COUNT and detector NAMES. Never the resolved paths.
            #
            # This dict is the dispatch log's view, and `job_log._safe`
            # refuses any line that looks path-shaped -- so a path here would
            # not leak, it would silently suppress the whole DISPATCH line and
            # take the sampler, size and seed with it.
            "auto_detail_slots": len(self.ad_slots),
            "auto_detail_detectors": [
                str(getattr(slot, "detector", "")) for slot in self.ad_slots
            ],
        }


@dataclass(frozen=True)
class ResidentModel:
    """What validation needs to know about the loaded session."""

    model_id: str
    family: str
    resident: bool
    #: What this session can actually be asked for.
    #:
    #: DEFAULTED TO ALL THREE, deliberately, and changed from txt2img-only.
    #: The old default dated from when `Operation` had one member ("one member
    #: until Gate 2"), and WP1.4 added the inpaint path without revisiting it.
    #: Nothing in production ever assigned this field, so every real session
    #: took the default and `validate_request` refused every img2img and
    #: inpaint job with GENERATION_OPERATION_UNSUPPORTED -- before the mask
    #: decode, the field mapping or the clip-to-mask composite could run. The
    #: entire feature was unreachable while its own tests passed, because they
    #: exercised admission on one side and hand-built `ResidentModel`s on the
    #: other, and the break sat between them.
    #:
    #: All three is the honest default because the ENGINE does not gate on it:
    #: `processing.py:332` sets `is_using_inpainting_conditioning` from
    #: `sd_model.is_inpaint` as an automatic enhancement, not a permission, so
    #: an ordinary checkpoint runs img2img and inpaint perfectly well. The one
    #: real gate in the pinned engine runs the other way -- `processing.py:879`
    #: refuses TXT2IMG under `--pid`, which this field can express when Studio
    #: exposes that mode.
    supported_operations: tuple[str, ...] = tuple(
        member.value for member in Operation)
    supported_samplers: tuple[str, ...] = ()
    supported_schedulers: tuple[str, ...] = ()


@dataclass
class ValidationResult:
    ok: bool
    problems: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems)}


def _problem(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def validate_request(
    request: FirstImageRequest,
    model: ResidentModel,
    *,
    options_available: bool,
    progress_installed: bool,
    result_root_writable: bool,
    generation_authorized: bool,
) -> ValidationResult:
    """Every check the request must pass before reaching the generation port.

    Returns all problems, not the first: one run should reveal the whole list.
    """

    problems: list[dict[str, str]] = []

    if not model.resident:
        problems.append(_problem("GENERATION_MODEL_NOT_RESIDENT", "no model session"))
    if model.model_id != request.model_id:
        problems.append(
            _problem("GENERATION_MODEL_MISMATCH", "request names a different model")
        )
    if request.operation.value not in model.supported_operations:
        problems.append(
            _problem("GENERATION_OPERATION_UNSUPPORTED", request.operation.value)
        )

    for name, value in (("width", request.width), ("height", request.height)):
        # NO CEILING, and no floor. Owner decision, 2026-08-16: Studio does not
        # restrict how large an owner may generate. Ever.
        #
        # This refused `width 2304 is outside 256-1536` on a card that had
        # never been consulted, for a limit Forge does not have. The range was
        # a Tier-0 profile bound that outlived its phase, and the comment on
        # the Hires scale below had already diagnosed the same mistake in the
        # same file: "a constant here caps every install".
        #
        # What actually limits a generation is VRAM, and that is answered where
        # it can be answered honestly -- an out-of-memory becomes a named error
        # carrying the measured free and total. A guess made in advance by a
        # constant is not a safety feature; it is a smaller product.
        if value <= 0:
            problems.append(
                _problem(
                    "GENERATION_DIMENSION_OUT_OF_RANGE",
                    f"{name} {value} is not a usable size",
                )
            )
        # NO ALIGNMENT REFUSAL. `GENERATION_DIMENSION_MISALIGNED` lived here
        # and refused any dimension not divisible by 8.
        #
        # Studio's core does not need it and never did. `modules/processing.py`
        # computes the latent as `p.height // opt_f, p.width // opt_f` -- floor
        # division, no assert, no refusal. Owner-verified end to end: 500x500
        # produces a 496x496 image, silently, and the owner's ruling on that is
        # "if it needs to silently adjust that's FINE."
        #
        # So this refused a request the rest of the product handles without
        # complaint. Removed 2026-08-20.
        #
        # `DIMENSION_ALIGNMENT` and `_snap_to_alignment` stay: the Hires target
        # at :649 uses them to COMPUTE a size, which is a calculation and not a
        # refusal.

    # Every detail below is a SENTENCE naming the value and the bound it
    # broke, not the bare offending value.
    #
    # `str(request.steps)` alone read, live, as
    # `{"code": ..., "message": "60"}` -- an owner who asked for 60 steps was
    # answered with the number they had just typed. A refusal that does not
    # say what the limit is cannot be acted on, and a bound the product
    # enforces is not a secret.
    if request.batch_size != 1:
        problems.append(
            _problem(
                "GENERATION_BATCH_SIZE_UNSUPPORTED",
                f"batch size {request.batch_size} is not supported; "
                "one image at a time",
            )
        )
    if request.output_count != 1:
        problems.append(
            _problem(
                "GENERATION_OUTPUT_COUNT_UNSUPPORTED",
                f"output count {request.output_count} is not supported; "
                "one image at a time",
            )
        )
    # A FLOOR, not a range. Fewer than one step is not a number of steps --
    # that is arithmetic, not a policy about how long an owner may wait.
    if request.steps < 1:
        problems.append(
            _problem(
                "GENERATION_STEPS_OUT_OF_RANGE",
                f"steps {request.steps} is below 1",
            )
        )
    if request.seed < 0:
        problems.append(
            _problem(
                "GENERATION_SEED_NOT_FIXED",
                "the first image uses an explicit seed, never -1",
            )
        )

    if model.supported_samplers and request.sampler not in model.supported_samplers:
        problems.append(_problem("GENERATION_SAMPLER_UNSUPPORTED", request.sampler))
    if model.supported_schedulers and request.scheduler not in model.supported_schedulers:
        problems.append(_problem("GENERATION_SCHEDULER_UNSUPPORTED", request.scheduler))

    # `enable_hr` left this loop in P0.7. A blanket refusal was right while
    # Studio had no field for it; now the request carries one, so the checks
    # below are about whether the ASKED-FOR pass is dispatchable.
    for flag, code, detail in (
        # `enable_adetailer` LEFT this loop, exactly as `enable_hr` did in
        # P0.7 and for the same reason: the slots now travel and the runtime
        # runs them, so the question is no longer whether Auto Detail is
        # permitted but whether the pass ASKED FOR is dispatchable. The checks
        # below answer that.
        (
            request.enable_extensions,
            "GENERATION_EXTENSIONS_NOT_PERMITTED",
            "extensions are disabled for the first controlled image",
        ),
        (
            request.reference_image_enabled,
            "GENERATION_REFERENCE_NOT_PERMITTED",
            "reference images are disabled for the first controlled image",
        ),
    ):
        if flag:
            problems.append(_problem(code, detail))

    if request.enable_adetailer and not request.ad_slots:
        # On with nothing to do. `translate_request` cannot produce this --
        # it derives the flag from the slots -- but a hand-built request can,
        # and accepting it would advance the job into an Auto Detail stage
        # that runs nothing and then reports that it detailed.
        problems.append(
            _problem(
                "GENERATION_ADETAILER_NO_SLOTS",
                "Auto Detail is enabled with no enabled slot",
            )
        )

    if request.enable_adetailer:
        # The pass ASKED FOR, checked the way the Hires pass is checked below.
        # A slot whose detector did not resolve is refused HERE, before a
        # base pass has been paid for -- the orchestrator refuses it too, but
        # by then the image exists and the cost is spent.
        for slot in request.ad_slots:
            where = f"slot {getattr(slot, 'index', '?')}"
            if not str(getattr(slot, "detector_path", "") or ""):
                problems.append(
                    _problem(
                        "GENERATION_ADETAILER_DETECTOR_UNRESOLVED",
                        f"Auto Detail {where} names the detector "
                        f"{str(getattr(slot, 'detector', '') or '')!r}, which is "
                        "not an admitted detector on this machine",
                    )
                )
            confidence = float(getattr(slot, "confidence", 0.3))
            if not 0.0 <= confidence <= 1.0:
                problems.append(
                    _problem(
                        "GENERATION_ADETAILER_CONFIDENCE_OUT_OF_RANGE",
                        f"Auto Detail {where} confidence {confidence} is "
                        "outside 0.0-1.0",
                    )
                )
            denoise = float(getattr(slot, "denoising_strength", 0.30))
            if not 0.0 <= denoise <= 1.0:
                problems.append(
                    _problem(
                        "GENERATION_ADETAILER_DENOISE_OUT_OF_RANGE",
                        f"Auto Detail {where} denoising strength {denoise} is "
                        "outside 0.0-1.0",
                    )
                )
            steps = int(getattr(slot, "steps", 0))
            # 0 means "inherit the base pass", so the floor is 0 and not 1.
            if steps < 0:
                problems.append(
                    _problem(
                        "GENERATION_ADETAILER_STEPS_OUT_OF_RANGE",
                        f"Auto Detail {where} steps {steps} is below 0",
                    )
                )

    if request.enable_hr:
        if not HIRES_SCALE_MIN <= float(request.hr_scale) <= HIRES_SCALE_MAX:
            problems.append(
                _problem(
                    "GENERATION_HIRES_SCALE_OUT_OF_RANGE",
                    f"Hires scale {request.hr_scale} is outside "
                    f"{HIRES_SCALE_MIN}-{HIRES_SCALE_MAX}",
                )
            )
        if int(request.hr_second_pass_steps) < 0:
            problems.append(
                _problem(
                    "GENERATION_HIRES_STEPS_OUT_OF_RANGE",
                    f"Hires second-pass steps {request.hr_second_pass_steps} "
                    "is below 0",
                )
            )
        if not 0.0 <= float(request.hr_denoising_strength) <= 1.0:
            problems.append(
                _problem(
                    "GENERATION_HIRES_DENOISE_OUT_OF_RANGE",
                    f"Hires denoising strength "
                    f"{request.hr_denoising_strength} is outside 0.0-1.0",
                )
            )
        # The TARGET, checked at the size the ENGINE will actually use.
        #
        # This used to refuse a misaligned target outright, on the stated
        # grounds that it "fails inside the second pass, after the base pass
        # has already been paid for". That is not true, and it cost an owner a
        # real job: 1368 x 1.5 = 2052 was refused whole.
        #
        # Neo rounds the target itself. `calculate_target_resolution`
        # (modules/processing.py:1271) runs `sRound` on both axes in BOTH of
        # its branches, and `sRound` (modules/resolution.py:33) snaps to the
        # nearest multiple of the resolution step. A misaligned target never
        # reaches the second pass, so there was nothing to refuse -- 2052
        # becomes 2056 and generates.
        #
        # The base dimensions stay a hard refusal above, and should: those are
        # what the OWNER typed. The Hires target is DERIVED, and a scale is a
        # convenience rather than a contract. Snapping it is what the behaviour
        # oracle does and what the owner expects.
        for axis, value in (
            ("width", request.width),
            ("height", request.height),
        ):
            # Snapped, never refused, and never capped -- same owner decision
            # as the base dimensions. The engine rounds this itself and the
            # card decides what it can hold.
            _snap_to_alignment(int(value) * float(request.hr_scale))

    if not options_available:
        problems.append(
            _problem("GENERATION_OPTIONS_MISSING", "headless options are not installed")
        )
    if not progress_installed:
        problems.append(
            _problem("GENERATION_PROGRESS_NOT_INSTALLED", "no progress source")
        )
    if not result_root_writable:
        problems.append(
            _problem("GENERATION_RESULT_ROOT_UNAVAILABLE", "result root not writable")
        )
    if not generation_authorized:
        problems.append(
            _problem(
                "HEADLESS_GENERATION_NOT_AUTHORIZED",
                "real generation is not authorized in this phase",
            )
        )

    return ValidationResult(ok=not problems, problems=problems)


def build_first_image_request(
    request_id: str,
    model_id: str,
    *,
    positive_prompt: str = "",
    negative_prompt: str = "",
    seed: int = 0,
    steps: int = 12,
    width: int = 768,
    height: int = 768,
) -> FirstImageRequest:
    """Construct the bounded profile. Everything else is fixed by design."""

    if not request_id or not model_id:
        raise HeadlessError(
            "GENERATION_REQUEST_INCOMPLETE",
            "a request id and a model id are both required.",
        )
    return FirstImageRequest(
        request_id=request_id,
        model_id=model_id,
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        steps=steps,
        width=width,
        height=height,
    )


__all__ = (
    "DEFAULT_CFG_SCALE",
    "DEFAULT_DISTILLED_CFG_SCALE",
    "DEFAULT_SAMPLER",
    "DEFAULT_SCHEDULER",
    "DERIVATIONS",
    "DIMENSION_ALIGNMENT",
    "FirstImageRequest",
    "InpaintOptions",
    "Operation",
    "ResidentModel",
    "ValidationResult",
    "build_first_image_request",
    "validate_request",
)
