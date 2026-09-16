"""Studio-owned application contracts for the Alpha S0 vertical slice."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


_REQUIRED_METADATA = object()


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Contract:
    """Small JSON-view helper shared by the public contracts."""

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class StructuredError(Contract):
    code: str
    message: str
    field: str | None = None
    #: Optional safe, structured context. Scalars only, and never a path, a
    #: filename, a repr or a traceback -- `to_dict` is a public projection.
    #: Added so a load failure can carry its stage instead of being reduced to
    #: an exception class name. Defaulted, so every existing construction and
    #: every existing equality check is unaffected.
    detail: dict[str, Any] | None = None


class StudioError(ValueError):
    """Application-safe failure with a structured public error."""

    def __init__(self, error: StructuredError) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True)
class BackendStatus(Contract):
    backend_id: str
    state: str
    ready: bool
    is_mock: bool
    message: str
    model_loaded: bool
    cuda_initialized: bool
    network_access: bool


@dataclass(frozen=True)
class ModelSummary(Contract):
    model_id: str
    name: str
    description: str
    is_mock: bool = False


#: Transport ceilings for an inline image. Section 8: an inline payload must
#: not become an unbounded HTTP surface.
#:
#: Encoded rather than decoded, because encoded is what the server has BEFORE
#: it commits to anything -- a decoded ceiling can only be checked after
#: spending the memory the ceiling exists to protect. base64 costs 4/3, so this
#: admits a ~24 MB image, which is a 4096x4096 PNG with room to spare.
#: A TRANSPORT bound -- how much request body the server buffers for one
#: image -- and not a statement about how big a picture an owner may use.
#:
#: Raised from 32 MB on 2026-08-20. With `MAX_SOURCE_PIXELS` gone (AR6.3), this
#: became the new effective wall: an 8192x8192 PNG is around 100 MB before
#: base64, which is 4/3 again on the wire, so 32 MB refused the very images the
#: pixel-cap removal was meant to allow.
#:
#: Kept rather than removed, unlike the caps around it. This one bounds memory
#: the SERVER holds per request rather than anything the owner is making, and
#: an unbounded body is a way to exhaust it. 256 MB clears any real photograph
#: or canvas with room to spare.
MAX_SOURCE_IMAGE_BYTES = 256 * 1024 * 1024

#: What Studio will decode. Narrow on purpose: the Canvas emits JPEG for the
#: source and PNG for the mask, and every additional format is another decoder
#: reachable from a browser request.
SOURCE_IMAGE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")


@dataclass(frozen=True)
class CanvasDocument(Contract):
    """WHICH Canvas document a job captured, and WHICH version of it. AR2.1.

    EXISTS BECAUSE STUDIO QUEUES. `/api/generate` returns 202 and the job runs
    later, so between admission and execution the owner can paint, undo,
    resize, add a layer or open something else. Without this pair, nothing on
    the request says which Canvas state it was built from, and a queued job
    that executed against repainted pixels would be silently wrong with nothing
    downstream able to tell.

    The Extension has no equivalent and does not need one: `run_generation`
    receives the canvas and mask as arguments and consumes them in the same
    call, so it has no such interval. This is Studio-owned by necessity, not
    parity debt -- the same argument that produced `InputAsset.content_hash`,
    applied to the document rather than to the bytes.

    OPAQUE. `document_id` is 32 hex characters minted in the browser and means
    nothing outside it. It is deliberately not a filename, a title or a path:
    identity must not become a channel for the filesystem, which is the rule
    the asset service already holds.
    """

    #: 32 hex characters. Stable for the life of one document.
    document_id: str = ""
    #: Strictly increasing within a document. Undo does NOT rewind it -- going
    #: back to earlier pixels is still a new state, and a reused number would
    #: let two different states share an identity.
    revision: int = 0


@dataclass(frozen=True)
class InputAsset(Contract):
    """One image the owner supplied, on its way to inference.

    Inline bytes rather than a handle into a new registry. Section 8 permits
    either and asks for the reason: a source image is used ONCE, by the job it
    arrives with, and dies with it. A registry would add a lifetime, an
    eviction policy and a second thing to leak -- ownership machinery for an
    object whose owner is already the request.

    The dimensions here are DECLARED, by the browser, and are not trusted. They
    exist so a mismatch can be reported as a named refusal rather than
    discovered as a crash inside the sampler; `forge_headless` decodes the
    bytes and checks reality against this. Section 9 keeps the decode out of
    `forge_studio`, which may not import PIL.

    No path, ever. The browser names no file on the server, and this carries
    nothing that could be mistaken for one.
    """

    #: `data:image/png;base64,...`. The only shape the Canvas produces.
    data_url: str = ""
    media_type: str = ""
    #: What the browser SAYS. Verified against the decode, never believed.
    width: int = 0
    height: int = 0
    byte_length: int = 0
    #: Where it came from, for metadata and for refusing what Studio does not
    #: own. Not a path -- a category.
    origin: str = "canvas"
    #: sha256 of the DECODED bytes, computed by the server at admission.
    #:
    #: The one field here the browser does not supply and cannot influence.
    #: Everything else on this contract is a claim: `width`, `height` and
    #: `byte_length` are what the page said, kept so a mismatch can be named.
    #: This is what arrived.
    #:
    #: It exists because Studio QUEUES. `/api/generate` returns 202 and the job
    #: runs later, so the Canvas can be painted on between admission and
    #: execution; the Extension has no equivalent because its generate route
    #: consumes the payload in the same call and has no such interval. With the
    #: hash frozen on the job, "which pixels did this result come from" has an
    #: answer that survives the owner continuing to work.
    #:
    #: Over the decoded payload, not the data URL string, so a re-encoded
    #: prefix or different base64 padding cannot change the identity of
    #: identical pixels.
    content_hash: str = ""

    @property
    def present(self) -> bool:
        return bool(self.data_url)


@dataclass(frozen=True)
class PromptExpansion(Contract):
    """What the SERVER made of the owner's prompt before the model saw it.

    Same shape of fact as `gpu_tile_compositing_requested` and snapshotted the
    same way: composed at assembly from the durable store, never taken from the
    payload. A page loaded an hour ago holds an hour-old wildcard folder, and
    the browser is not the expansion authority -- it may PREVIEW an expansion,
    but the resolution that reaches the sampler is the server's.

    Both halves are kept. The resolved prompt is what was generated; the
    original is what the owner typed and the only thing they can edit and
    re-run. Recording one without the other makes a wildcard image
    irreproducible in the way that matters -- you can see what came out and not
    what asked for it.

    Absent means the feature did nothing: disabled, no folder, or a prompt with
    no tokens in it. In that case `resolved` equals `original` and no consumer
    needs to special-case anything.
    """

    #: What the owner typed.
    original_prompt: str = ""
    original_negative_prompt: str = ""
    #: What the sampler received. Equal to the original when nothing expanded.
    resolved_prompt: str = ""
    resolved_negative_prompt: str = ""
    #: Which line each token drew, as `name=value`. Names, never paths.
    choices: tuple[str, ...] = ()
    #: Tokens that named a wildcard the library does not have. Left legible in
    #: the prompt rather than blanked, so the owner sees what went wrong.
    missing: tuple[str, ...] = ()
    #: Expansion stopped at the depth bound with something still moving --
    #: a cycle, or nesting deeper than the bound. NOT set by a token that is
    #: merely missing, which is left in the prompt deliberately.
    truncated: bool = False
    #: What the owner should be told about their own prompt: a malformed
    #: multi-select count, a choice group empty after stripping. Separate from
    #: `missing`, which is its own diagnosis and already legible in the text.
    warnings: tuple[str, ...] = ()
    #: The seed the expansion was drawn with, AFTER -1 was resolved to a
    #: concrete value. Without it a "random seed" job cannot be reproduced even
    #: though every choice was deterministic given the number.
    seed: int = -1
    #: Bumped when the algorithm changes in a way that moves output for
    #: unchanged inputs. A recorded expansion is only reproducible against the
    #: version that produced it.
    version: int = 1

    @property
    def expanded(self) -> bool:
        """Whether anything actually changed. Cheap enough to ask every time."""

        return (self.resolved_prompt != self.original_prompt
                or self.resolved_negative_prompt != self.original_negative_prompt)


@dataclass(frozen=True)
class GenerationRequest(Contract):
    model_id: str
    positive_prompt: str
    negative_prompt: str
    seed: int
    steps: int
    cfg_scale: float
    width: int
    height: int
    #: The three opaque catalogue ids this job wants resident, as
    #: ``{"checkpoint_model_id": ..., "text_encoder_model_id": ...,
    #: "vae_model_id": ...}``. The job NAMES its model rather than inheriting
    #: whatever happens to be loaded, which is what lets generation be legal
    #: from `NO_MODEL` without ever running on a model nobody asked for.
    #:
    #: A nested sub-object rather than three flat fields, because P0.6 nests
    #: the rest of the request under `generation` and this shape survives that
    #: unchanged. Optional: a host with no catalogue omits it and behaves
    #: exactly as before.
    model_selection: dict[str, str] | None = None
    #: The sampler and scheduler the owner chose, by NAME, as Forge Neo's own
    #: registries spell them. Empty means "whatever the engine defaults to",
    #: which is what every Studio request meant until now -- the controls
    #: existed in the page and reached nothing, so an owner who picked
    #: `Euler a` got the default and was told otherwise.
    #:
    #: Names rather than ids because these are Neo's dispatch keys: an id
    #: would be a second identity for a thing the engine already names, and
    #: Studio would own the mapping between them forever.
    sampler: str = ""
    scheduler: str = ""
    #: Whether this job should decode live preview frames while it samples.
    #:
    #: The page has offered a Live Preview toggle for its whole existence and
    #: the backend pinned the flag off in two places, with `preview_enabled`
    #: sitting in `BACKEND_DEFAULTED_FIELDS` so the translation report called
    #: it a backend default rather than an owner choice. Same shape as sampler
    #: and scheduler before P0.6, and Hires before P0.7: a control that renders
    #: and reaches nothing.
    #:
    #: A plain bool rather than a nested group, because unlike `hires` there is
    #: no coherent set of settings that only mean anything together -- the
    #: frame budget and the decode size are properties of the SERVER, not of
    #: the job, and letting a request name them would make preview cost a
    #: client-chosen quantity.
    preview_enabled: bool = False
    #: Clip Skip, or None when this job names none.
    #:
    #: Neo spells it `CLIP_stop_at_last_layers` and treats it as a GLOBAL
    #: option, applied at `modules/processing.py:467` from `opts` -- never from
    #: the processing object. Studio therefore carries it as a request field
    #: and hands it to the per-job option scope at the boundary; it must never
    #: be written onto `p.clip_skip`, which changes only the infotext
    #: (`processing.py:712`) and would make the recorded metadata disagree with
    #: the image.
    #:
    #: None rather than 2 so that absent stays absent: a job that names no
    #: Clip Skip leaves the option alone, and is byte-identical to one made
    #: before this field existed.
    clip_skip: int | None = None
    #: The owner's GPU tile compositing preference, snapshotted by the SERVER
    #: at assembly. Not a browser field: a page loaded an hour ago holds an
    #: hour-old preference, and several pages can be open at once, so the
    #: durable store is the authority and the payload is not consulted.
    #:
    #: The REQUESTED preference, never the effective mode. A job can carry
    #: True and still composite on the CPU because the frame did not fit, and
    #: those are different facts that the result metadata reports separately.
    gpu_tile_compositing_requested: bool = True
    #: The wildcard resolution, snapshotted by the SERVER at assembly.
    #:
    #: On the request rather than beside it because every consumer that needs
    #: the resolved prompt already has the request, and because the previous
    #: arrangement -- adapter-instance state holding the last expansion -- was
    #: reachable only from the legacy route and was single-slot under
    #: concurrency. Absent on any host without a wildcard service, which is
    #: every mock and most tests.
    prompt_expansion: "PromptExpansion | None" = None
    #: What kind of generation this is. A STRING here rather than the headless
    #: `Operation` enum, because `forge_studio` does not import from
    #: `forge_headless` -- the direction rule runs the other way -- and the
    #: translation boundary is where the two vocabularies meet.
    operation: str = "txt2img"
    #: The image an IMG2IMG or INPAINT job works from. None for txt2img, and
    #: required for the other two: a missing source is a named refusal before
    #: admission, not a silent fall back to txt2img.
    source_image: "InputAsset | None" = None
    #: How far the sampler is allowed to move from the source. Only meaningful
    #: when a source exists.
    denoising_strength: float = 0.75
    #: The region an INPAINT job may change. None for txt2img and img2img.
    #:
    #: Its own asset rather than a channel of the source, because the two have
    #: different lifetimes and different failure modes: a source can be a photo
    #: the owner imported, while a mask is always painted here, and a mismatch
    #: between them is a named refusal that needs to name WHICH one was wrong.
    mask: "InputAsset | None" = None
    #: How the dimensions were chosen, when the randomizer was active. None
    #: for a fixed-size request, which stays byte-identical to a pre-WP1.5 one.
    aspect: "AspectRandomization | None" = None
    #: Which Canvas document and revision this job captured, when the page
    #: told us. None from an API caller that has no Canvas.
    document: "CanvasDocument | None" = None
    #: The ordinary inpaint settings, or None when this is not an inpaint job.
    #:
    #: A group for the same reason `hires` and `variation` are groups: these
    #: fields only mean anything together. `mask_blur` without a mask is not a
    #: weaker request, it is an incoherent one.
    inpaint: "InpaintSettings | None" = None
    #: The Hires second pass, or None when the owner did not ask for one.
    #:
    #: A typed nested contract rather than fourteen flat `hr_*` fields, because
    #: the book requires one canonical request with typed sub-objects, and
    #: because Hires is a GROUP: it is either on with a coherent set of
    #: settings or absent entirely. Fourteen optional flat fields would let a
    #: caller send `hr_scale` with `enabled` false and leave the reader to
    #: guess what was meant.
    #:
    #: Absent means absent. `enable_hr` stays False and the base pass is
    #: byte-identical to what it was before this field existed -- which is the
    #: first acceptance criterion the book lists for the phase.
    hires: "HiresSettings | None" = None
    #: Auto Detail, or None when the owner did not ask for it. Same rule as
    #: `hires`: absent means absent, and nothing downstream sets a single
    #: ADetailer field, so a base-only result is byte-identical to a pre-P0.8
    #: one.
    auto_detail: "AutoDetailSettings | None" = None
    #: How the finished picture is written to disk, or None for "as before".
    #:
    #: A group, not flat fields, by this contract's own rule: quality and
    #: lossless mean NOTHING without a format, which is exactly the test
    #: `preview_enabled` is documented as failing ("no coherent set of settings
    #: that only mean anything together").
    #:
    #: Absent means absent: no group and the result is written exactly as it
    #: was before this field existed, same bytes and same `image/png`. Same
    #: acceptance rule as `hires`.
    #:
    #: This existed as a Settings control for Studio's whole life and reached
    #: NOTHING -- the field was assembled below the lifecycle `return`, no
    #: server contract carried it, and the writer hardcoded PNG in three
    #: places. An owner who chose JPEG got a PNG and was told otherwise.
    output: "OutputSettings | None" = None
    #: `variation`: same rule again. Absent means absent, and nothing
    #: downstream sets a subseed -- so a request without this key produces the
    #: byte-identical image it did before the field existed. That is the
    #: acceptance criterion every additive group in this contract has had.
    variation: "VariationSettings | None" = None


@dataclass(frozen=True)
class AspectRandomization(Contract):
    """What the owner asked the randomizer for, and what it decided.

    BOTH HALVES, deliberately. `width` and `height` on the request are the
    concrete resolved values and are what executes; these are the provenance.
    A recipe carrying only the result cannot be read back as "this was a random
    roll", and one carrying only the request cannot reproduce the image.

    Resolved at ADMISSION and then frozen. The Extension rolls inside its
    per-image loop, which is right for a route that returns the image it
    generated; Studio queues, so a roll at execution would leave a job whose
    recipe and result disagree about what was asked for. Owner decision.

    PER IMAGE, still. Moving the roll earlier changed only when it happens, not
    how often: `rolls` holds one frozen entry for every image the submission
    covers, in order, exactly as upstream's batch loop produces them. Execution
    reads those integers and draws no further randomness, so a retry repeats
    the recorded sizes instead of inventing new ones.

    Only meaningful for txt2img. An img2img or inpaint job takes its geometry
    from the source image -- the Extension guards its call site with
    `if is_txt2img:` for the same reason.
    """

    randomize_base: bool = False
    randomize_ratio: bool = False
    randomize_orientation: bool = False
    #: What the owner selected. Empty means all, which is the Extension's rule.
    base_pool: tuple[int, ...] = ()
    ratio_pool: tuple[str, ...] = ()
    #: What each image's roll decided, in submission order. One entry per
    #: image, never a single answer shared by a batch.
    rolls: tuple["AspectRoll", ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.randomize_base or self.randomize_ratio
                    or self.randomize_orientation)


@dataclass(frozen=True)
class AspectRoll(Contract):
    """One image's frozen dimensions, plus how they were reached.

    `width` and `height` are what that image executes at -- already decided, no
    longer a request. The rest is provenance, kept for the same reason the
    group above keeps the pools: a recipe carrying only 1136x640 cannot be read
    back as "the randomizer chose this", and one carrying only the pools cannot
    reproduce the image.
    """

    width: int
    height: int
    base: int
    ratio: str
    orientation: str


@dataclass(frozen=True)
class OutputSettings(Contract):
    """How the finished picture is encoded on its way to disk.

    STUDIO'S NAMES, not the encoder's. `test_product_truthfulness` forbids the
    engine's and the imaging library's own spellings here, and
    `forge_headless/live_generation_port.py` is the one module allowed to
    translate them -- so `format` is "jpeg", never "JPEG", and the extension it
    lands on is that module's business rather than this one's.

    `quality` and `lossless` are deliberately BOTH present with only one
    meaningful at a time, because which one applies is a property of the
    FORMAT: JPEG has no lossless mode and WebP's quality is ignored when
    lossless is set. Refusing the incoherent combination in the contract would
    mean the contract knowing each encoder's capabilities, which is the
    translation layer's job.

    THE DEFAULTS ARE THE PAGE'S. `png` with quality 92 is what the Settings
    panel has always shown; an API caller who omits the group gets what the
    page would have sent.
    """

    #: `png`, `jpeg` or `webp`. Refused if it is anything else -- silently
    #: coercing an unknown format to PNG is precisely the defect this group
    #: exists to fix.
    format: str = "png"
    #: 1-100. Ignored for PNG, and ignored for WebP when `lossless` is set.
    quality: int = 92
    #: WebP only. JPEG has no lossless mode and PNG is always lossless, so
    #: this is meaningful for exactly one of the three.
    lossless: bool = False
    #: Whether the saved file carries the generation parameters.
    #:
    #: TRUE by default, which is a deliberate departure from this group's other
    #: fields. They are all "what the page would have sent"; this one is also
    #: what the page has ALWAYS shown -- the Settings toggle ships lit -- and
    #: what the Extension does. Defaulting it off would have meant shipping a
    #: control that is on and does nothing, which is the defect being fixed.
    #:
    #: The consequence is stated plainly because it changes an earlier rule:
    #: an omitted `output` group no longer means "byte-identical to before this
    #: group existed". It means the defaults, and the default writes a
    #: `parameters` chunk. See `_validated_output`.
    embed_metadata: bool = True


@dataclass(frozen=True)
class InpaintSettings(Contract):
    """What an inpaint job does at the edges of the owner's mask.

    STUDIO'S NAMES, not the engine's. `test_product_truthfulness` forbids the
    engine's own spellings from appearing in this module, and it is right to:
    the translation boundary is the one place the two vocabularies meet, and a
    contract that borrows the engine's identifiers is a contract that has to
    change whenever the engine renames one.

    THE DEFAULTS ARE THE PAGE'S, NOT THE ENGINE'S, and an earlier version of
    this docstring claimed the opposite. The engine's own class defaults differ
    on all three of fill, area and padding; these match what the inpaint bar
    has always shown an owner. That is the right choice -- an API caller who
    omits the group should get what the page would have sent -- but it is a
    CHOICE, and the previous text asserted a parity that was never there.

    The engine's values are named in
    `forge_headless/live_generation_port.py::_inpaint_kwargs`, which is the one
    module allowed to spell them; `test_product_truthfulness` keeps them out of
    here, and caught this docstring reintroducing them while correcting the
    very claim they belonged to.

    `full_resolution` is the one that differs from the page as well: the bar
    defaults to `Only Masked`, which is True. It stays False here so an omitted
    group cannot silently crop, and the collector sends the real value.

    `full_resolution` is the one worth reading twice. False inpaints the whole
    image at the requested size; True crops to the masked region, samples it at
    full resolution, and composites back -- which is what makes a small face
    usable, and what most owners mean by inpainting at all.
    """

    #: Softens the seam between changed and untouched pixels, in pixels.
    mask_blur: int = 4
    #: What the masked region starts from: 0 a flat fill, 1 what is already
    #: there, 2 latent noise, 3 latent nothing. 1 preserves the original.
    fill: int = 1
    #: Crop to the mask and sample it at full resolution, then composite back.
    full_resolution: bool = False
    #: Context kept around the crop, in pixels. Only meaningful with
    #: `full_resolution`.
    padding: int = 32
    #: False means "change what is painted"; True changes everything else.
    invert: bool = False
    #: Gradual blending at the mask boundary, or None when the owner did not
    #: switch it on. A group, and absent means absent, exactly as `hires`,
    #: `variation` and `aspect` are -- so an omitted group leaves the engine's
    #: own behaviour untouched rather than asserting a default over it.
    soft: "SoftInpainting | None" = None


@dataclass(frozen=True)
class SoftInpainting(Contract):
    """Blend the mask boundary gradually instead of at full strength.

    WHY THIS EXISTS AS A PRODUCT FEATURE. Without it the engine rounds the
    LATENT mask to 0 or 1, and a latent cell covers an 8x8 block of pixels --
    so every block is either wholly original or wholly regenerated and the
    seam is a staircase at 8px pitch, softened only by `mask_blur` afterwards.
    That is the "rough edges" an owner sees. Switching this on stops the
    rounding and blends the latent across the transition instead.

    STUDIO'S NAMES, which here are the ENGINE'S OWN UI LABELS rather than its
    field names -- "Schedule bias" and the rest are what the sliders have
    always been called, and what the Extension named its fields after. The
    engine's internal spellings live only in the translation boundary;
    `test_product_truthfulness` keeps them out of this module.

    THE DEFAULTS ARE THE ENGINE'S, unusually for this file, and they agree with
    the page's: `soft_inpainting.py:449` is
    `SoftInpaintingSettings(1, 0.5, 4, 0, 0.5, 2)` and the six controls in the
    inpaint bar already carry those same numbers. Nothing had to be chosen.

    The engine's own guidance is that a HIGH `mask_blur` suits this mode. Studio
    does not raise `mask_blur` on the owner's behalf when this is switched on --
    moving a control the owner can see, without being asked, is the failure this
    project keeps removing.
    """

    #: Shifts when original content is preserved during denoising. Below 1
    #: preserves more at the end, above 1 more at the beginning.
    schedule_bias: float = 1.0
    #: How strongly partially masked content keeps the original.
    preservation: float = 0.5
    #: Restores contrast that partial masking would otherwise wash out.
    transition_contrast: float = 4.0
    #: How much the mask itself, rather than the measured difference, decides
    #: the final composite.
    mask_influence: float = 0.0
    #: Below this difference, changed pixels are treated as unchanged.
    diff_threshold: float = 0.5
    #: How sharply the measured difference maps onto the composite.
    diff_contrast: float = 2.0


@dataclass(frozen=True)
class VariationSettings(Contract):
    """A variation seed, in Studio's vocabulary.

    Four fields that only mean anything together, which is why this is a nested
    group rather than four siblings of `seed`. `subseed` alone changes nothing:
    Neo reports "Variation seed" as None whenever `subseed_strength == 0`
    (modules/processing.py), so a subseed without a strength is a value the
    engine deliberately ignores.

    Defaults are Neo's own defaults for the same fields, read from the BASE
    `StableDiffusionProcessing` class -- which is why this works for txt2img at
    all, and why it does not need an img2img operation the way denoise does.

    `seed_resize_from_*` default to -1 here rather than the 0 the page sends.
    Neo gates on `<= 0`, so both spellings disable it; -1 is Neo's own, and
    matching it keeps the contract honest about whose default this is.
    """

    #: -1 means "let the engine draw one". Unlike `seed`, a variation subseed
    #: has no reproducibility requirement of its own -- it is only meaningful
    #: relative to a strength, and a job with strength 0 records neither.
    subseed: int = -1
    #: 0 disables variation entirely. Neo's range.
    subseed_strength: float = 0.0
    #: Resize-from dimensions, for reproducing a composition at another size.
    #: Both must be > 0 or Neo ignores the pair.
    seed_resize_from_w: int = -1
    seed_resize_from_h: int = -1


@dataclass(frozen=True)
class HiresSettings(Contract):
    """One Hires second pass, in Studio's vocabulary.

    Mapped onto Neo's `hr_*` fields at the headless boundary rather than here,
    so this stays the shape the OWNER chose and Neo's spelling stays Neo's
    problem. Every default matches Neo's own default for the same field, so an
    owner who enables Hires and sets nothing else gets Forge's behaviour rather
    than Studio's opinion of it.
    """

    enabled: bool = False
    #: Target multiple of the base dimensions. Forge's own range, 1.0 to 4.0.
    #:
    #: This briefly read 1.5-2.0, sized to what fits on one 16 GB card. That
    #: was a machine's constraint written into a product contract, and it would
    #: have capped every larger install at the smallest one anybody tested on.
    #: Measured since: 512 -> 2048 with an image upscaler peaks around 10 GB of
    #: 16, so the bound would have refused a pass that card runs with a third
    #: of its memory unused.
    #:
    #: What is genuinely fixed is checked elsewhere and is not a range at all:
    #: the TARGET must land on the VAE's factor of 8, refused before dispatch
    #: because a misaligned target fails inside the second pass after the base
    #: pass has already been paid for. Whether a target FITS is answered by the
    #: machine at dispatch, not by a constant here.
    #:
    #: That alignment was 16 until 2026-08-11, on the reasoning that the patch
    #: size multiplied the VAE factor. It does not: the model pads the latent
    #: to its patch grid and crops the output back, so the caller owes it
    #: nothing. See DIMENSION_ALIGNMENT in forge_headless/generation_request.py.
    #:
    #: A 4x-native upscaler is correct at any target: Forge upscales at the
    #: model's native scale and resizes, so the detail is the model's and the
    #: size is the owner's.
    scale: float = 2.0
    #: A name from `/api/registries`, latent or image. Empty means the engine
    #: default. Membership is checked where dispatch happens.
    upscaler: str = ""
    #: 0 means "same as the base pass", which is Neo's own meaning.
    second_pass_steps: int = 0
    denoising_strength: float = 0.7
    #: Empty means "inherit the base pass's choice", which is what Neo does
    #: when `hr_sampler_name`/`hr_scheduler` are unset.
    sampler: str = ""
    scheduler: str = ""
    #: Empty means "reuse the base prompt", again matching Neo.
    prompt: str = ""
    negative_prompt: str = ""
    #: 0 means "inherit the base CFG".
    #:
    #: Not invented: Neo already spells "same as the base pass" as 0 for the
    #: sibling field -- `steps = self.hr_second_pass_steps or self.steps`. So
    #: this follows the engine's own convention rather than adding a second
    #: one beside it.
    #:
    #: Above 0 the accepted range is Neo's UI range, 1.0 to 24.0, and 1.0 is a
    #: SENTINEL rather than merely a low value: `if self.hr_cfg == 1` makes
    #: Neo drop the negative conditioning entirely. Worth knowing before
    #: treating it as a continuous scale.
    cfg: float = 0.0


@dataclass(frozen=True)
class AutoDetailSlot(Contract):
    """One Auto Detail pass: detect, mask, inpaint.

    Studio's own vocabulary, not ADetailer's. The upstream extension exposes
    these as flat `ad_*` arguments introspected out of an A1111 Script, with
    names that have changed across versions and a schema that sometimes uses
    `extra='forbid'`. Studio owns the schema (owner decision) and translates at
    the adapter, so a version bump upstream is an adapter change rather than a
    wire-contract change.

    A slot is either enabled with a coherent set of settings or it is off. Off
    slots are not dispatched at all -- no detector is loaded, no pass runs --
    which is what keeps "three slots" from costing three detector loads for an
    owner using one.
    """

    enabled: bool = False
    #: A detector NAME from the Studio catalogue, never a path. The catalogue
    #: is local-only by policy, so this names a file the owner already has.
    detector: str = ""
    #: Detection confidence floor. Below this a candidate is not a detection.
    confidence: float = 0.3
    #: Keep at most this many detections, after sorting. 0 means all of them.
    top_k: int = 0
    #: Discard detections whose area falls outside this fraction of the frame.
    #: The default pair accepts everything; narrowing it is how an owner says
    #: "faces, not the whole portrait" without describing it in a prompt.
    min_ratio: float = 0.0
    max_ratio: float = 1.0
    #: Grow (positive) or shrink (negative) the mask, in pixels.
    dilate_erode: int = 4
    #: Soften the mask edge so the inpaint blends rather than seams.
    #:
    #: 6, not upstream's 4. These two defaults come from the owner's WORKING
    #: configuration against this checkpoint rather than from ADetailer's
    #: defaults, which are tuned for the models its author tested. A default
    #: that produces poor output is a quieter failure than one that errors, and
    #: the owner has a known-good pair.
    mask_blur: int = 6
    #: How much of the masked region the second pass may rewrite.
    #: 0.30, not upstream's 0.4 -- same reason.
    denoising_strength: float = 0.30
    #: Empty means "reuse the base prompt", matching how Hires and Neo behave.
    prompt: str = ""
    negative_prompt: str = ""
    #: Context pixels around the mask handed to the inpaint. Too little and the
    #: model has no surroundings to match; too much and it repaints the scene.
    inpaint_padding: int = 32
    #: 0 means "inherit the base pass", following Neo's own convention for
    #: `hr_second_pass_steps` rather than inventing a second spelling.
    steps: int = 0
    cfg: float = 0.0


#: Three, because the BOOK fixes the pipeline at
#: BASE -> optional HIRES -> AD1 -> AD2 -> AD3 -> PUBLISH.
#:
#: Part of the product shape, not a limit that happens to be three: a fourth
#: slot would change the pipeline contract. `forge_headless.auto_detail`
#: carries the same number for the runtime, and a test pins the two together
#: -- two constants meaning one rule is how they drift.
MAX_AUTO_DETAIL_SLOTS = 3


@dataclass(frozen=True)
class AutoDetailSettings(Contract):
    """Up to three Auto Detail passes, in order.

    Three because the book fixes the pipeline at BASE -> optional HIRES ->
    AD1 -> AD2 -> AD3 -> PUBLISH, as ONE public job. The count is part of the
    product shape rather than a limit that happens to be three, so it is a
    fixed tuple rather than an open list: a fourth slot would change the
    pipeline contract, not just a number.
    """

    enabled: bool = False
    slots: tuple[AutoDetailSlot, ...] = ()


@dataclass(frozen=True)
class GenerationJobIdentity(Contract):
    job_id: str
    state: JobState


@dataclass(frozen=True)
class ProgressEvent(Contract):
    """One non-consuming progress observation.

    ``progress`` is an integer percent, 0-100. ``step`` and ``total_steps`` are
    the backend's own counters when it reports them; they are the authoritative
    granularity, because integer percent cannot express "step 7 of 20" without
    rounding. Both are ``None`` when the backend does not report steps.
    """

    job_id: str
    state: JobState
    sequence: int
    progress: int
    message: str
    error: StructuredError | None = None
    step: int | None = None
    total_steps: int | None = None


@dataclass(frozen=True)
class ModelResidency(Contract):
    """Which model is actually resident in the backend, if any.

    Model residency is a backend fact, not a frontend selection: loading costs
    time and memory and can fail. ``model_id`` is ``None`` when nothing is
    resident.
    """

    model_id: str | None
    loaded: bool
    is_mock: bool
    message: str


class DeviceType(str, Enum):
    """The backend device actually selected for inference.

    This describes the *device*, never the host operating system. A macOS host
    may report ``CPU``; a Linux host may report ``CUDA``. Studio must not infer
    one from the other.
    """

    CUDA = "cuda"
    MPS = "mps"
    CPU = "cpu"
    XPU = "xpu"
    DIRECTML = "directml"
    UNKNOWN = "unknown"


class DtypePolicy(str, Enum):
    """The effective dtype policy the adapter applies, not a request."""

    FP32 = "fp32"
    FP16 = "fp16"
    BF16 = "bf16"
    MIXED = "mixed"
    BACKEND_DEFAULT = "backend_default"
    UNKNOWN = "unknown"


class AttentionBackend(str, Enum):
    """The attention implementation actually in use."""

    PYTORCH_SDPA = "pytorch_sdpa"
    XFORMERS = "xformers"
    SAGE = "sage"
    FLASH = "flash"
    BACKEND_DEFAULT = "backend_default"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelCapability(Contract):
    """Selected-model, selected-operation generation limits and environment.

    The dimension audit established that there is no universal alignment rule
    and no universal safe maximum, so capability must come from the backend
    rather than from a guessed constant. ``None`` means the backend imposes no
    limit on that axis. ``normalizes_dimensions`` is true only when the backend
    silently adjusts a request instead of rejecting it; Studio must disclose
    requested versus effective dimensions whenever it is true.

    The three device fields describe the active inference environment. They are
    reported by the backend and transported unchanged; Studio never derives
    them from ``sys.platform`` or any other host property. Each defaults to
    ``UNKNOWN`` so an older backend that does not set them stays contract-valid
    and is not mistaken for one that positively reported a device.

    Per-model limits may legitimately differ by device and dtype: the same
    checkpoint can carry different safe maxima on CUDA and MPS. A future
    adapter reports both together so the pairing stays coherent.
    """

    model_id: str
    operation: str
    dimension_alignment: int
    minimum_dimension: int
    maximum_dimension: int | None
    maximum_pixels: int | None
    normalizes_dimensions: bool
    is_mock: bool
    device_type: DeviceType = DeviceType.UNKNOWN
    dtype_policy: DtypePolicy = DtypePolicy.UNKNOWN
    attention_backend: AttentionBackend = AttentionBackend.UNKNOWN

    def __post_init__(self) -> None:
        # The three enums subclass str, so a raw string would serialize
        # identically and pass unnoticed. Require the member itself: a backend
        # that means "cuda" must say so with the vocabulary, and one that does
        # not know must say UNKNOWN rather than None.
        for field_name, expected in (
            ("device_type", DeviceType),
            ("dtype_policy", DtypePolicy),
            ("attention_backend", AttentionBackend),
        ):
            observed = getattr(self, field_name)
            if type(observed) is not expected:
                raise TypeError(
                    f"{field_name} must be a {expected.__name__} member, "
                    f"got {observed!r}"
                )


@dataclass(frozen=True)
class CancellationResult(Contract):
    job_id: str
    cancelled: bool
    state: JobState
    message: str


@dataclass(frozen=True, init=False)
class GeneratedResult(Contract):
    """Completed output with inline data or a backend-owned internal reference.

    ``output_path`` and ``metadata_path`` are backend ownership records, not
    browser URLs. The presentation boundary removes them until an explicit,
    contained result-delivery route exists.
    """

    job_id: str
    state: JobState
    mime_type: str
    image_data_url: str | None
    metadata: dict[str, Any]
    output_path: str | None = None
    metadata_path: str | None = None

    def __init__(
        self,
        job_id: str,
        state: JobState,
        mime_type: str,
        image_data_url: str | None = None,
        metadata: dict[str, Any] | object = _REQUIRED_METADATA,
        output_path: str | None = None,
        metadata_path: str | None = None,
    ) -> None:
        if metadata is _REQUIRED_METADATA:
            raise TypeError("metadata is required")
        if image_data_url is None and not output_path:
            raise ValueError(
                "A generated result requires image_data_url or output_path."
            )
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "mime_type", mime_type)
        object.__setattr__(self, "image_data_url", image_data_url)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "output_path", output_path)
        object.__setattr__(self, "metadata_path", metadata_path)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.image_data_url is None:
            del payload["image_data_url"]
        return payload
