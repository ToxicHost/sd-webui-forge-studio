"""A product-neutral generation port over retained `process_images_inner`.

This is the extracted, product-shaped counterpart of the smoke runner's
`LiveGenerationPort`. Every proven behaviour is kept; three things change,
and all three are the difference between a diagnostic and a product:

```text
multi-job      the smoke port sets `terminal` after ONE generate and refuses a
               second. A warm session that can run one job is not a warm
               session, so this one stays usable until the session closes.
owns release   `release_generation_references` is called HERE, once per job, in
               a finally. In the smoke path the runner did it from outside;
               in the product there is no runner.
no diagnostics it takes no LifecycleRecorder and reaches no residual module,
               both of which are prohibited in product code.
```

Nothing else is re-derived. The sampler/scheduler readback, the bridge
re-pointing, the observer-free construction inside the `try`, and the ownership
model are the smoke port's, because they were paid for live.

SCOPE: every Forge/Neo import is deferred to `generate()`. Importing this module
reaches no torch, no CUDA, no `modules.*`.
"""

from __future__ import annotations

import logging

import os
from pathlib import Path
from typing import Any

#: Characters allowed in a per-job result filename. A request id reaches this
#: from the transport, so it is filtered rather than trusted.
_SAFE_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


#: Studio's format names -> what the encoder, the filesystem and HTTP each
#: call the same thing. THE ONE PLACE these three vocabularies meet.
#:
#: `contracts.py` is forbidden from spelling the encoder's names
#: (`test_product_truthfulness`), so the translation lives here beside
#: `_inpaint_kwargs`, which exists for exactly the same reason.
#:
#: The extension and the media type are taken from the SAME row, so a `.jpg`
#: holding PNG bytes is not expressible -- which is the failure this feature is
#: most likely to produce.
OUTPUT_FORMATS: dict[str, tuple[str, str, str]] = {
    #  studio name -> (encoder format, extension, media type)
    "png": ("PNG", "png", "image/png"),
    "jpeg": ("JPEG", "jpg", "image/jpeg"),
    "webp": ("WEBP", "webp", "image/webp"),
}


def resolve_output_format(options: Any) -> tuple[str, str, str]:
    """One row of `OUTPUT_FORMATS`, or the PNG row when nothing was asked for.

    An UNKNOWN format is refused rather than coerced. Silently writing a PNG
    for someone who asked for JPEG is the exact defect this exists to fix, and
    doing it in the name of robustness would reintroduce it one layer down.
    """

    from .contracts import HeadlessError

    if options is None:
        return OUTPUT_FORMATS["png"]
    name = str(getattr(options, "format", "png") or "png").strip().lower()
    row = OUTPUT_FORMATS.get(name)
    if row is None:
        raise HeadlessError(
            "GENERATION_OUTPUT_FORMAT_UNSUPPORTED",
            f"{name!r} is not an output format Studio can write.",
        )
    return row


def output_save_kwargs(options: Any, encoder_format: str) -> dict[str, Any]:
    """The encoder arguments for one format, and only the ones it honours.

    Mirrors the Extension (`studio_api.py:3300-3306`), including the parts that
    look like details and are not:

      * JPEG at 4:4:4 (`subsampling=0`), because Studio's own Settings copy
        promises "no color smearing on fine edges or text" and the default
        would make that false;
      * WebP lossless XOR quality, never both.
    """

    if options is None or encoder_format == "PNG":
        return {}
    # NOT `or 92`: `0 or 92` is 92, so a caller asking for the lowest quality
    # would silently receive the default. Admission already refuses 0, but a
    # translator that depends on a validator upstream is one refactor away
    # from being wrong -- and this is the same shape as the `?? / ||` defect
    # this project has already fixed once.
    raw = getattr(options, "quality", None)
    quality = 92 if raw is None else int(raw)
    quality = max(1, min(100, quality))
    if encoder_format == "JPEG":
        return {"quality": quality, "subsampling": 0, "optimize": True}
    if bool(getattr(options, "lossless", False)):
        return {"lossless": True}
    return {"quality": quality}


def metadata_save_kwargs(infotext: str, encoder_format: str,
                         options: Any = None) -> dict[str, Any]:
    """The encoder arguments that carry the generation parameters, if any.

    Mirrors the Extension exactly (`studio_api.py:3227-3238` for PNG,
    `:3308-3315` for the lossy pair, `:1586-1612` for the EXIF builder), which
    matters more here than usual: the point of embedding parameters is that
    OTHER programs read them back, so this is an interoperability format and
    not a private one.

    Three details that look incidental and are not:

      * the PNG key is `parameters`. Every reader in the ecosystem looks for
        that exact word -- including Studio's own drag-and-drop reader -- so a
        tidier name of our own would produce files only Studio could read;
      * `UserComment` carries a mandatory charset prefix. Without the
        `ASCII\0\0\0` header the field is malformed and readers show leading
        garbage rather than the parameters;
      * a metadata failure returns EMPTY rather than raising. The owner's image
        must survive a problem with the text describing it. The Extension logs
        and saves anyway; so does this.

    `options is None` means the defaults, and the default is ON -- see the note
    on `resolve_output_format`. Absent stopped meaning "no metadata" when the
    toggle that ships lit was finally connected to something.
    """

    if not infotext:
        return {}
    if not bool(getattr(options, "embed_metadata", True)):
        return {}

    if encoder_format == "PNG":
        try:
            from PIL.PngImagePlugin import PngInfo

            block = PngInfo()
            block.add_text("parameters", infotext)
            return {"pnginfo": block}
        except Exception:
            return {}

    payload = _exif_user_comment(infotext)
    return {"exif": payload} if payload else {}


def _exif_user_comment(text: str) -> bytes | None:
    """EXIF bytes carrying `text` in UserComment, or None.

    `piexif` first and Pillow second, which is the Extension's order
    (`studio_api.py:1597-1612`) and is not arbitrary: piexif is in Neo's
    dependency tree, so the primary path is the one that actually runs here,
    and the fallback exists for a host where it is not.

    `errors="replace"` because a prompt can contain anything at all, and a
    character that will not encode must cost the metadata, never the image.
    """

    if not text:
        return None
    encoded = b"ASCII\x00\x00\x00" + text.encode("utf-8", errors="replace")
    try:
        import piexif

        return piexif.dump({"Exif": {piexif.ExifIFD.UserComment: encoded}})
    except Exception:
        pass
    try:
        from PIL import Image as _Image

        exif = _Image.Exif()
        exif[0x9286] = encoded          # UserComment, EXIF IFD
        return exif.tobytes()
    except Exception:
        return None


def safe_result_name(request_id: object, *, index: int,
                     extension: str = "png") -> str:
    """A filesystem-safe name for one job, in the owner's chosen format.

    Never derived from prompt text, and never allowed to escape the result root:
    anything outside `_SAFE_NAME` is dropped, and an empty result falls back to
    the job index rather than to a constant, so two jobs cannot collide.

    The extension comes from `OUTPUT_FORMATS`, never from a caller's string, so
    it always agrees with the media type reported for the same result.
    """

    cleaned = "".join(ch for ch in str(request_id) if ch in _SAFE_NAME)[:64]
    return f"{cleaned or f'job-{index}'}.{extension}"



_SRGB_ICC_CACHE: list = []


def _srgb_icc_bytes() -> bytes:
    """sRGB ICC bytes for tagging a saved result, or empty when unavailable.

    Lives in `forge_headless` rather than beside the import route in
    `forge_studio`, because the dependency direction is `forge_studio` ->
    `forge_headless` and never the reverse. The route creates its own
    conversion DESTINATION; this produces the bytes that get embedded.

    Cached: building an ICC profile per generation is pure waste, and the
    profile cannot change within a process.
    """

    if _SRGB_ICC_CACHE:
        return _SRGB_ICC_CACHE[0]
    # P3. THE EXTENSION'S v2.1 PROFILE, not LittleCMS's runtime one. The
    # Extension moved off `createProfile("sRGB")` because lcms2 stamps its own
    # spec version (4.4 on recent builds) with v4-only tag types, which strict
    # parsers -- certain Photoshop versions and asset tools -- reject as a
    # broken profile, and a Pillow upgrade silently changed every output's
    # profile (`studio_api.py:61-173`). Same colorimetry, so no pixel changes;
    # the same self-check and the same fallback.
    data = b""
    try:
        import io as _io

        from PIL import ImageCms

        built = _build_srgb_v2_profile()
        ImageCms.ImageCmsProfile(_io.BytesIO(built))
        data = built
    except Exception:  # noqa: BLE001 - fall back exactly as the Extension does
        try:
            from PIL import ImageCms

            data = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        except Exception:  # noqa: BLE001 - absence is an outcome, not a failure
            data = b""
    _SRGB_ICC_CACHE.append(data)
    return data


def _build_srgb_v2_profile() -> bytes:
    """The Extension's canonical "sRGB IEC61966-2.1" ICC v2.1 profile, byte
    for byte (`studio_api.py:82-158`): v2-only tag types (desc/text/XYZ/curv),
    the classic HP/IEC tag layout, LCMS's fixed-point D50 primaries, and a
    fixed creation date so the bytes are deterministic."""

    import struct

    def s15f16(v):
        return int(round(v * 65536.0))

    def xyz_tag(x, y, z):
        return struct.pack(">4s4x3i", b"XYZ ", s15f16(x), s15f16(y), s15f16(z))

    def desc_tag(text):
        ascii_bytes = text.encode("ascii") + b"\x00"
        return (struct.pack(">4s4xI", b"desc", len(ascii_bytes)) + ascii_bytes
                + struct.pack(">II", 0, 0)
                + struct.pack(">H", 0)
                + b"\x00" * 68)

    def text_tag(text):
        return struct.pack(">4s4x", b"text") + text.encode("ascii") + b"\x00"

    def curv_tag(n=1024):
        pts = []
        for i in range(n):
            x = i / (n - 1)
            y = x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
            pts.append(min(65535, max(0, round(y * 65535.0))))
        return struct.pack(">4s4xI%dH" % n, b"curv", n, *pts)

    r = xyz_tag(0.436035, 0.222488, 0.013916)
    g = xyz_tag(0.385117, 0.716904, 0.097061)
    b = xyz_tag(0.143051, 0.060608, 0.713913)
    wtpt = xyz_tag(0.950455, 1.0, 1.089050)
    desc = desc_tag("sRGB IEC61966-2.1")
    cprt = text_tag("Public domain, no copyright")
    trc = curv_tag()

    tags = [(b"desc", desc), (b"cprt", cprt), (b"wtpt", wtpt),
            (b"rXYZ", r), (b"gXYZ", g), (b"bXYZ", b),
            (b"rTRC", trc), (b"gTRC", trc), (b"bTRC", trc)]

    offset = 128 + 4 + 12 * len(tags)
    placed = {}
    blobs = []
    entries = []
    for sig, data in tags:
        if id(data) not in placed:
            pad = (4 - offset % 4) % 4
            offset += pad
            blobs.append(b"\x00" * pad + data)
            placed[id(data)] = (offset, len(data))
            offset += len(data)
        o, s = placed[id(data)]
        entries.append(struct.pack(">4sII", sig, o, s))

    body = struct.pack(">I", len(tags)) + b"".join(entries) + b"".join(blobs)
    header = struct.pack(
        ">I4sI4s4s4s6H4s4sIIIQI3i I44x",
        128 + len(body),
        b"\x00" * 4,
        0x02100000,
        b"mntr",
        b"RGB ",
        b"XYZ ",
        2026, 1, 1, 0, 0, 0,
        b"acsp",
        b"\x00" * 4,
        0, 0, 0, 0,
        0,
        s15f16(0.9642), s15f16(1.0), s15f16(0.8249),
        0,
    )
    return header + body


def _looks_like_out_of_memory(exc: BaseException) -> bool:
    """Whether a failure was the allocator giving up.

    Matched by TYPE first -- `torch.cuda.OutOfMemoryError` when torch is
    importable -- and by message only as a fallback, because the same
    condition surfaces under several exception classes depending on where in
    the allocator it was hit.
    """

    import sys

    # Only if torch is ALREADY here. Importing it to identify an exception
    # would drag torch -- and its CUDA initialisation -- into any process that
    # merely asked "was that an OOM?", including the ones the purity tests
    # exist to keep clean. `test_import_boundaries` caught exactly that, from a
    # test that called this helper.
    #
    # Nothing is lost by the gate: without torch loaded there was no CUDA
    # allocator to run out, so the type check could not have matched anyway.
    if "torch" in sys.modules:
        try:
            torch = sys.modules["torch"]
            if isinstance(exc, torch.cuda.OutOfMemoryError):  # type: ignore[attr-defined]
                return True
        except Exception:  # noqa: BLE001 - absence just means fall through
            pass
    text = f"{type(exc).__name__}: {exc}".lower()
    return "out of memory" in text or "cuda error: out of memory" in text


def _device_memory_summary() -> str:
    """Measured free/total VRAM, or an honest silence."""

    try:
        import torch

        if not torch.cuda.is_available():
            return ""
        free, total = torch.cuda.mem_get_info()
        return (
            f"{free // (1024 * 1024)} MiB free of "
            f"{total // (1024 * 1024)} MiB at the time of failure."
        )
    except Exception:  # noqa: BLE001
        return ""


def _hires_target_description(request: Any) -> str:
    scale = float(getattr(request, "hr_scale", 0) or 0)
    width = int(getattr(request, "width", 0) or 0)
    height = int(getattr(request, "height", 0) or 0)
    if not (scale and width and height):
        return "the requested Hires size"
    return f"{int(round(width * scale))}x{int(round(height * scale))} (scale {scale})"


def _clip_to_mask(result: Any, source_image: Any, mask: Any,
                  mask_blur: int) -> Any:
    """Clip stray pixel changes OUTSIDE the mask. Ported from the Extension.

    `StableDiffusionProcessingImg2Img` already composites its output against
    the original using the blurred mask, inside `apply_overlay`. This is not a
    second composite of that kind and must not become one: what it catches is
    VAE ENCODER/DECODER LEAKAGE, small changes that appear outside the painted
    region because the whole frame went through the autoencoder.

    Without it, "nothing outside your mask changed" is not true, and Studio had
    nothing like it -- the omission was found by reading
    `studio_generation.py::_clip_to_mask` (:1900) after the fact, not by
    testing.

    Two details are load-bearing and both are the Extension's:

    The mask is DILATED by `mask_blur * 3` and then binarised, so the clip
    boundary sits fully outside Forge's Gaussian tail (~3 sigma). Inside that
    boundary the composite is pass-through and Forge's own transition survives
    untouched.

    It is binarised rather than left soft. Compositing with a blurred mask here
    would multiply Forge's transition by this one -- `M * M` -- squaring it, so
    a 50% transition pixel collapses to 25% and a high `mask_blur` loses the
    soft edge it was asked for.

    Never raises: a failed clip returns the unclipped result, because a
    slightly leaky image is a better outcome than losing one the owner waited
    for.
    """

    try:
        from PIL import Image, ImageFilter

        original = source_image.convert("RGB").resize(result.size,
                                                      Image.LANCZOS)
        clip = mask.convert("L").resize(result.size, Image.LANCZOS)
        if mask_blur > 0:
            clip = clip.filter(ImageFilter.GaussianBlur(radius=mask_blur * 3))
            clip = clip.point(lambda value: 255 if value > 1 else 0)
        return Image.composite(result, original, clip)
    except Exception:  # noqa: BLE001 - see the docstring
        return result


def _upscale_for_hires(image, name: str, width: int, height: int):
    """Neo own upscaler registry, or LANCZOS when it has no such entry.

    Mirrors `studio_generation.py:566-577`. The named upscaler is looked up in
    `shared.sd_upscalers` and asked to scale by the RATIO rather than to a
    size, because that is the interface Neo upscalers expose; the result is
    then resized only if it missed, which the Extension also does.

    "Latent" and the empty name fall through to LANCZOS deliberately. A latent
    upscaler has no latent to work on here -- this pass starts from a decoded
    image -- and pretending otherwise would silently produce something the
    owner did not select.
    """

    from PIL import Image as _Image

    # The import sits INSIDE the gate so the fallback names never touch
    # `modules.shared` at all -- there is nothing to look up for them.
    if name and name not in ("None", "Latent"):
        try:
            from modules import shared

            for candidate in getattr(shared, "sd_upscalers", ()) or ():
                if getattr(candidate, "name", "") != name:
                    continue
                scaled = candidate.scaler.upscale(
                    image, width / image.width, candidate.data_path)
                if scaled.size != (width, height):
                    scaled = scaled.resize((width, height), _Image.LANCZOS)
                return scaled
        except (Exception, SystemExit):
            # A failed upscaler is not a failed generation. The pass still
            # runs, at the requested size, on a plainly resized image.
            #
            # SystemExit is named explicitly because it is NOT an Exception:
            # importing `modules.shared` pulls in `shared_cmd_options`, which
            # calls `parser.parse_args()` at module scope and exits on an argv
            # it does not recognise. A bare `except Exception` let that kill
            # the job, which is how this was found. KeyboardInterrupt is
            # deliberately still allowed through -- cancellation must not be
            # swallowed by an upscaler lookup.
            pass
    return image.resize((width, height), _Image.LANCZOS)


def _arm_lora_support(request: Any) -> None:
    """Register the engine's LoRA handler, once per process.

    NEVER RAISES. A LoRA that cannot be armed must not cost the owner the
    image: the prompt still generates, simply without the network applied.
    That is the same rule `_upscale_for_hires` follows for a missing upscaler
    inside a pass, and the opposite of the standalone upscale, where the
    upscale IS the job.

    The roots come from the request rather than a global, because the port has
    no settings registry of its own -- `translate_request` carries them across
    with everything else.
    """

    try:
        from .lora_bridge import arm

        roots = tuple(getattr(request, "lora_roots", ()) or ())
        arm(roots)
    except (Exception, SystemExit) as error:
        # SystemExit for the reason `_upscale_for_hires` names it: importing
        # `modules.shared` parses argv at module scope and can exit on one it
        # does not recognise.
        #
        # REPORTED, not swallowed. A LoRA that silently does nothing is
        # indistinguishable from a LoRA with no effect, and that is the exact
        # defect this whole line of work exists to remove -- the first version
        # of this handler swallowed the failure and the tag went on being
        # dropped with the log saying nothing.
        import logging

        logging.getLogger("studio.lora").warning(
            "LoRA support could not be armed, so <lora:...> tags will be "
            "ignored: %s: %s", type(error).__name__, error)
        return


def _soft_inpainting_bridge(request: Any, is_inpaint: bool) -> Any:
    """The bridge, or None -- and None is the pre-WP1.6 behaviour exactly.

    TWO CONDITIONS, both required. The job must be an inpaint, and the owner
    must have sent the group. Neither is inferred from the other: an img2img
    job has no mask for the blend to work across, and an inpaint job whose
    owner never switched the feature on must keep the engine's own edge
    behaviour rather than have six defaults asserted over it.

    The engine's `processing_uses_inpainting` is checked AGAIN inside every
    hook. That is not redundant -- this decides whether to install the bridge
    from Studio's request, and that decides whether to act from what actually
    reached the processing object.
    """

    if not is_inpaint:
        return None
    options = getattr(request, "inpaint", None)
    settings = getattr(options, "soft", None)
    if settings is None:
        return None
    from .soft_inpainting_bridge import SoftInpaintingBridge

    return SoftInpaintingBridge(settings)


def _inpaint_kwargs(request: Any, source: Any) -> dict[str, Any]:
    """Studio's inpaint options, in the engine's spelling.

    The translation boundary, and the only module where both vocabularies are
    allowed to appear. `forge_headless/studio_generation.py` carries Studio's
    names all the way here precisely so it can stay independent of the engine's
    -- a guard in `test_product_truthfulness` enforces that, and it is right to.

    `mask` and not `image_mask`. `image_mask` is declared `init=False` on
    `StableDiffusionProcessingImg2Img`, so passing it to the constructor raises
    a TypeError; the constructor parameter is `mask`, which `init()` then reads
    into `image_mask` itself.

    Everything downstream of these values is Neo's: binarisation, inversion,
    the blur, the full-resolution crop and paste, and the final composite all
    happen inside `init()`. Studio supplies settings and a mask; it does not
    touch pixels.
    """

    options = getattr(request, "inpaint", None)
    return {
        "mask": getattr(source, "mask", None),
        "mask_blur": int(getattr(options, "mask_blur", 4) or 0),
        "inpainting_fill": int(getattr(options, "fill", 1) or 0),
        "inpaint_full_res": bool(getattr(options, "full_resolution", False)),
        "inpaint_full_res_padding": int(getattr(options, "padding", 32) or 0),
        # Neo reads this as an int, not a bool: 0 keeps the painted region as
        # the target, 1 flips it.
        "inpainting_mask_invert": 1 if getattr(options, "invert", False) else 0,
    }


def _hires_kwargs(request: Any) -> dict[str, Any]:
    """Neo's `hr_*` construction arguments, or NOTHING at all.

    With Hires off this returns an empty dict, so the processing object is
    constructed exactly as it was before P0.7 -- not with Hires fields set to
    inert values, but without them mentioned. "Off is byte-identical to absent"
    is the phase's first acceptance criterion, and the cheapest way to hold it
    is to have nothing to hold.

    `hr_additional_modules` is the one that bites. It is declared
    `list = field(default=None)`, and `modules/processing.py` does
    `"Use same choices" not in self.hr_additional_modules` -- so the DEFAULT
    raises `TypeError: argument of type 'NoneType' is not iterable`, and it
    raises AFTER the base pass has already burned its steps. A mid-job crash,
    not a validation error.

    It must be the LIST, not the bare string. Both survive that membership
    test -- a string by substring luck -- but `processing.py` also does
    `isinstance(self.hr_additional_modules, list)` before recording
    `Hires Module 1` in the metadata, so a string produces a working generation
    whose recipe is silently incomplete. The Forge-Studio extension's comment
    saying it MUST be a string is true of an older Forge whose check differed;
    this build is the authority for this build.
    """

    if not getattr(request, "enable_hr", False):
        return {}
    return {
        "hr_scale": float(getattr(request, "hr_scale", 2.0)),
        # Empty means "engine default"; Neo reads None, not "".
        "hr_upscaler": (getattr(request, "hr_upscaler", "") or None),
        "hr_second_pass_steps": int(getattr(request, "hr_second_pass_steps", 0)),
        "denoising_strength": float(getattr(request, "hr_denoising_strength", 0.7)),
        "hr_sampler_name": (getattr(request, "hr_sampler_name", "") or None),
        "hr_scheduler": (getattr(request, "hr_scheduler", "") or None),
        "hr_prompt": str(getattr(request, "hr_prompt", "") or ""),
        "hr_negative_prompt": str(getattr(request, "hr_negative_prompt", "") or ""),
        "hr_cfg": float(getattr(request, "hr_cfg", 0.0) or 0.0),
        "hr_additional_modules": ["Use same choices"],
    }


def _stamped(image: Any, request: Any) -> Any:
    """P11. The legacy "generation" watermark, as the last pixel step.

    The Extension's rule (`studio_generation.py:3061-3066`): skipped after an
    inpaint, because the unmasked pixels came from the canvas and may already
    carry the mark from an earlier generation. Never raises -- a missing or
    unreadable watermark saves the picture unstamped."""

    settings = getattr(getattr(request, "output", None), "watermark", None)
    if settings is None:
        return image
    source = getattr(request, "source", None)
    if (getattr(source, "mask", None) is not None
            and getattr(source, "mask_present", False)):
        return image
    from .watermark import apply_watermark, configured_folder

    stamped, _ = apply_watermark(image, {
        "enable": True, "name": settings.name, "position": settings.position,
        "opacity": settings.opacity, "scale": settings.scale,
        "margin": settings.margin, "rotation": settings.rotation,
    }, configured_folder())
    return stamped


def save_result_exclusively(image: Any, target: Path, *,
                            encoder_format: str = "PNG",
                            save_kwargs: dict[str, Any] | None = None) -> None:
    """Write one result, or refuse -- never over an existing file.

    This was `image.save(str(target))`, which overwrites unconditionally. With
    the old process-local `headless-%06d` identifier that meant a restarted
    Studio silently destroyed the owner's earlier generations, one file at a
    time, starting from `headless-000001.png`. It happened during live
    verification.

    The identifier is now a uuid4, so a collision should be impossible; this
    exists so that "should be" is not the only thing standing between the
    owner and lost work. `O_CREAT | O_EXCL` decides atomically in the
    filesystem, which an `exists()` check followed by a save cannot do -- two
    processes can both pass the check and then both write.

    A collision is therefore a real fault (a duplicate injected id, or a
    caller reusing one) and is reported as one rather than resolved by
    retrying under a different name: the produced result must keep the
    request_id its job was created with, or `request_id` in the metadata stops
    naming the file it describes.
    """

    from .contracts import HeadlessError  # local, matching this module

    # Reserve the name first. The empty file this creates is the claim; the
    # encoder then fills our own reservation. Reserving rather than writing
    # through the descriptor keeps the encoder call path-shaped, which is what
    # every caller and test double already expects.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(target), flags)
    except FileExistsError:
        raise HeadlessError(
            "GENERATION_RESULT_IDENTIFIER_COLLISION",
            "That result identifier is already in use.",
        ) from None
    os.close(descriptor)
    try:
        # TAGGED sRGB. Results were written untagged while canvas-core.js
        # stated the opposite, which made an untagged file and an sRGB file
        # indistinguishable to anything that read one back -- including
        # Studio's own colour-managed import path, which reports `missing` for
        # a profile that was never written rather than for a decision anyone
        # made. Tagging costs a few hundred bytes and makes the claim true.
        #
        # Absent ICC support saves exactly as before rather than failing: a
        # host without Pillow's ImageCms still produces the owner's image, and
        # the import path reports `unavailable` honestly.
        # JPEG HAS NO ALPHA, and a Canvas result legitimately can. Saving an
        # RGBA image as JPEG raises, so the flatten is not cosmetic -- it is
        # what makes the format usable at all. Matches the Extension, which
        # does `img.convert("RGB")` for the same reason.
        subject = image
        if encoder_format == "JPEG" and getattr(image, "mode", "") not in ("RGB", "L"):
            subject = image.convert("RGB")
        extra = dict(save_kwargs or {})

        profile = _srgb_icc_bytes()
        if not profile:
            subject.save(str(target), format=encoder_format, **extra)
        else:
            try:
                subject.save(str(target), format=encoder_format,
                             icc_profile=profile, **extra)
            except TypeError:
                # An encoder that does not accept `icc_profile`. This helper's
                # contract, stated above, is that the call stays path-shaped --
                # every caller and test double is written to that. Tagging is an
                # improvement to the FILE, never a new requirement on the
                # encoder, so an encoder that cannot take it still writes the
                # owner's image instead of failing the job.
                #
                # Adding the kwarg unconditionally broke 25 tests in one run.
                subject.save(str(target), format=encoder_format, **extra)
    except BaseException:
        # Release the reservation. Leaving it would occupy the identifier with
        # an empty file that answers `exists()` for a result never produced --
        # and this only ever removes the file this call just created.
        try:
            os.unlink(str(target))
        except OSError:
            pass
        raise


class StudioLiveGenerationPort:
    """`GenerationPort` over one warm engine, reusable across jobs.

    Ownership is two-tiered, which is the whole point:

    ```text
    per job        the processing request, the processed result, the published
                   image references, the class-level conditioning caches
    per session    the engine, its component aliases, the state bridge
    ```

    Per-job release runs after every job. The engine is released only by
    `release_engine()`, which the session close calls. Confusing the two is
    exactly the bug the per-job milestone existed to fix.
    """

    authorized = True

    def __init__(
        self,
        *,
        engine: object,
        bridge: object | None,
        result_root: Path | str,
    ) -> None:
        self._engine = engine
        self._bridge = bridge
        self._result_root = Path(result_root)
        # Component aliases are held only so they can be cleared deliberately.
        self._forge_objects: object | None = None
        self._vae_component: object | None = None

        self.generate_calls = 0
        self.release_calls = 0
        self.terminal = False
        self.engine_released = False

        #: Per-job scalars, readable for reporting between jobs.
        self.resolved_sampler = ""
        self.resolved_scheduler = ""
        self.sampler_resolution_source = "unavailable"
        self.last_release: dict[str, Any] = {}

    # -- ownership ---------------------------------------------------------

    def release_engine(self) -> dict[str, Any]:
        """Drop every reference this port holds. Idempotent, never raises."""

        had_engine = self._engine is not None
        self._engine = None
        self._forge_objects = None
        self._vae_component = None
        self._bridge = None
        self.engine_released = True
        self.terminal = True
        if had_engine:
            # The CUDA/model ownership boundary: engine, component aliases and
            # bridge closures are all gone from here.
            from .unload_events import ENGINE_OWNERSHIP_RELEASED, record

            record(ENGINE_OWNERSHIP_RELEASED)
        return {
            "engine_reference_cleared": True,
            "component_aliases_cleared": True,
            "closures_cleared": True,
            "had_engine": had_engine,
            "terminal": True,
            "generate_calls": self.generate_calls,
            "release_calls": self.release_calls,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": type(self).__name__,
            "authorized": self.authorized,
            "generate_calls": self.generate_calls,
            "release_calls": self.release_calls,
            "terminal": self.terminal,
            "engine_released": self.engine_released,
            "holds_engine": self._engine is not None,
            "resolved_sampler": self.resolved_sampler,
            "resolved_scheduler": self.resolved_scheduler,
            "sampler_resolution_source": self.sampler_resolution_source,
        }

    # -- per-job release ---------------------------------------------------

    def _release_job(
        self,
        processing: object | None,
        processed: object | None,
        exception: BaseException | None,
    ) -> dict[str, Any]:
        """Call the shipped seam exactly once for this job. Never raises.

        Called BEFORE the per-job fields are dropped: the seam needs the
        processing object to reach the sampler, the CFG denoiser and the
        class-level conditioning caches. Dropping first would leave the caches
        populated and the release would silently free nothing -- the three-cycle
        live run measured 4,784,640 bytes here on every job.
        """

        self.release_calls += 1
        try:
            from .failure_cleanup import release_generation_references
        except BaseException as exc:  # noqa: BLE001 - cleanup reports, never raises
            return {"called": False, "reason": type(exc).__name__}

        try:
            report = release_generation_references(
                processing=processing,
                processed=processed,
                state_bridge=self._bridge,
                exception=exception,
                outcome=None,
            )
        except BaseException as exc:  # noqa: BLE001
            return {"called": False, "reason": type(exc).__name__}

        to_dict = getattr(report, "to_dict", None)
        return {"called": True, **(to_dict() if callable(to_dict) else {})}

    # -- the port ----------------------------------------------------------

    def generate(self, request: object, progress: object) -> object:
        """Run the generation with this job's option overrides in force.

        The scope is opened here because this is the last frame Studio owns
        before Neo takes over, and closed here because a `ContextVar` left set
        would hand the next job on this thread the previous job's preference.
        The decision is read back INSIDE the scope -- outside it the record is
        gone, which is the point.
        """
        from dataclasses import is_dataclass, replace

        from .job_options import job_scope, recorded

        overrides: dict[str, object] = {}
        # From the ADMITTED job, never from the store. A preference
        # changed while this job runs belongs to the next one.
        requested = getattr(request, "gpu_tile_compositing_requested", None)
        if requested is not None:
            overrides["composite_tiles_on_gpu"] = bool(requested)

        # CLIP SKIP, and it travels the same way for the same reason.
        #
        # Neo conditions the model from `opts.CLIP_stop_at_last_layers`
        # (`modules/processing.py:467`, inside `get_conds_with_caching`), not
        # from anything on the processing object. Writing it onto `shared.opts`
        # is closed -- about forty modules bind `opts` at import, so the next
        # queued job would inherit it -- and Neo's own apply/restore wrapper
        # lives in `process_images`, which Studio deliberately does not call.
        #
        # The scope solves all three: per-thread by construction, nothing left
        # behind when the job ends or raises, and no `modules/` edit. Every
        # `opts.X` read already routes through `HeadlessOptions.__getattr__`,
        # which checks the scope first.
        #
        # It also makes the metadata true for free. `Processed.clip_skip`
        # (`processing.py:542`) and the filename pattern (`images.py:422`) read
        # the same option inside the same scope, so what is recorded is what
        # was applied. Setting `p.clip_skip` would have moved only the infotext
        # (`processing.py:712`) and left the image alone.
        #
        # None means the job named none: the option keeps its own value.
        clip_skip = getattr(request, "clip_skip", None)
        if clip_skip is not None:
            overrides["CLIP_stop_at_last_layers"] = int(clip_skip)

        with job_scope(**overrides):
            outcome = self._generate_in_scope(request, progress)
            decision = recorded()

        if not decision or not is_dataclass(outcome):
            return outcome
        return replace(
            outcome,
            gpu_tile_composite_requested=bool(
                decision.get("gpu_tile_composite_requested", False)),
            upscale_composite_effective=str(
                decision.get("upscale_composite_effective", "")),
            upscale_composite_reason=str(
                decision.get("upscale_composite_reason", "")),
        )

    def _generate_in_scope(self, request: object, progress: object) -> object:
        from .contracts import HeadlessError

        if self.terminal or self._engine is None:
            raise HeadlessError(
                "GENERATION_PORT_RELEASED",
                "The model session has been released and cannot generate.",
            )
        self.generate_calls += 1

        # WHERE THE TIME GOES. Built before the optimisations it exists to
        # judge, because this project has twice reached a confident conclusion
        # from a number that meant something else. What it can bracket is what
        # Studio OWNS: `process_images_inner` is Neo's and covers base,
        # upscale and Hires as one call, so the boundary either side of it --
        # and the Hires -> Auto Detail boundary, which is Studio's -- is what
        # separates here.
        from .generation_telemetry import GenerationTelemetry, ModelMoveObserver

        telemetry = GenerationTelemetry(
            str(getattr(request, "request_id", "") or ""))
        self.telemetry = telemetry

        from .headless_progress import JobState

        # The bridge predates the `modules.processing` import, and the per-job
        # progress is created by the gateway afterwards, so the bridge is
        # re-pointed at the authoritative object for this job.
        if self._bridge is not None:
            object.__setattr__(self._bridge, "_progress", progress)

        from modules.processing import (
            StableDiffusionProcessingTxt2Img,
            process_images_inner,
        )

        engine = self._engine
        objects = getattr(engine, "forge_objects", None)
        self._forge_objects = objects
        self._vae_component = getattr(objects, "vae", None) if objects else None

        # Declared BEFORE sampling starts, so the progress model can tell the
        # second `launch_sampling` from the first. Without it, "a new step
        # budget arrived" would be a guess about what the engine meant; with
        # it, a base-only job can never transition into the Hires states.
        if getattr(request, "enable_hr", False):
            expect = getattr(progress, "expect_hires_pass", None)
            if callable(expect):
                expect()
        progress.set_total_steps(int(request.steps))  # type: ignore[attr-defined]
        progress.advance_to(JobState.SAMPLING)  # type: ignore[attr-defined]

        processing: object | None = None
        processed: object | None = None
        failure: BaseException | None = None
        # Every model move passes through `memory_management.load_models_gpu`
        # (:616), which is also what logs `Moving model(s) has taken N`
        # (:702). Observing that ONE name for the life of the generation
        # gives the move count and cost per stage -- the measurement the
        # same-job retention work turns on, and one that survives a noisy
        # machine in a way seconds do not.
        moves = ModelMoveObserver(telemetry)
        moves.__enter__()
        try:
            # Construction is inside the `try` on purpose: a request that fails
            # to build must still reach the per-job release below.
            #
            # ONE construction with a swapped class, not two code paths. Every
            # field below is shared; img2img adds an init image and a denoise
            # and drops the Hires group. Duplicating the whole call would be
            # two places for the next field to be added to, and this project
            # has five recorded instances of a field reaching one path and not
            # the other.
            source = getattr(request, "source", None)
            # The DECLARED operation, not an inference from what happens to be
            # attached. `source is not None` was the old test, and it could not
            # tell an inpaint job from an img2img one at all -- so an admitted
            # mask was carried through the whole request, frozen onto the job,
            # and then silently dropped here. Inpaint executed as plain img2img
            # and nothing said otherwise.
            operation = getattr(request, "operation", None)
            operation_name = str(getattr(operation, "value", operation) or "txt2img")
            is_inpaint = operation_name == "inpaint"
            is_img2img = source is not None
            if is_img2img:
                # THE REFUSAL THAT USED TO LIVE HERE IS GONE. WP1.7.
                #
                # It raised UNSUPPORTED_OPERATION_COMBINATION, "Hires is not
                # available for image-to-image yet", and its own comment
                # already recorded that it was a KNOWN DEFECT: the premise
                # "Studio does not invent an img2img Hires" was false, because
                # the Extension implements one. The claim had come from
                # grepping a single Extension function
                # (`_run_attention_couple_image`) and generalising.
                #
                # Read properly this time. `run_generation` calls
                # `run_hires_fix` at `studio_generation.py:3456` for every
                # non-txt2img job with Hires on and scale above 1, with no mask
                # exclusion -- ordinary img2img AND inpaint both get a second
                # pass. `_img2img_hires` below now performs it, after
                # `process_images_inner` and BEFORE the first clip.
                #
                # `enable_hr` is still never set on the img2img class: it is a
                # `StableDiffusionProcessingTxt2Img` field and setting it there
                # would do nothing. The old comment's warning about that stands
                # and is the reason the pass is explicit rather than a flag.
                # Imported HERE, not beside the txt2img class. Every double in
                # the suite stubs `modules.processing`, and most supply only
                # the txt2img name -- so a module-level import made a txt2img
                # job depend on a symbol it never uses and broke 38 tests that
                # had nothing to do with img2img. `_detail_pass` below already
                # imports it this way for the same reason.
                from modules.processing import StableDiffusionProcessingImg2Img

                processing_class = StableDiffusionProcessingImg2Img
            else:
                processing_class = StableDiffusionProcessingTxt2Img
            operation_kwargs: dict[str, Any] = (
                {
                    # A LIST, because Neo iterates it -- one entry, because
                    # Studio generates one image per job.
                    "init_images": [source.image],
                    "denoising_strength": float(
                        getattr(request, "denoising_strength", 0.75)),
                    # THE ONE PLACE the two vocabularies meet. Studio says
                    # `fill` and `full_resolution`; the engine says
                    # `inpainting_fill` and `inpaint_full_res`. Mapping here
                    # keeps engine identifiers out of the translator, which is
                    # what `test_product_truthfulness` protects.
                    #
                    # `mask=`, never `image_mask=`: the latter is `init=False`
                    # on the dataclass, so passing it to the constructor is a
                    # TypeError rather than a mask.
                    #
                    # Neo does the rest inside `init()` -- binarise, invert,
                    # blur, crop for full-resolution, composite back. Studio
                    # supplies the values and must not reimplement any of it.
                    **(_inpaint_kwargs(request, source) if is_inpaint else {}),
                }
                if is_img2img
                else {
                    "enable_hr": bool(getattr(request, "enable_hr", False)),
                    **_hires_kwargs(request),
                }
            )
            processing = processing_class(
                sd_model=engine,
                outpath_samples=str(self._result_root),
                outpath_grids=str(self._result_root),
                prompt=request.positive_prompt,  # type: ignore[attr-defined]
                negative_prompt=request.negative_prompt,  # type: ignore[attr-defined]
                seed=int(request.seed),  # type: ignore[attr-defined]
                # The owner's variation, not a pin. This was `-1` while the
                # page shipped an Extra panel with a variation seed, a strength
                # and two resize-from fields -- four controls that remembered
                # their values and reached nothing. `FirstImageRequest` carries
                # Neo's own defaults, so a request without a `variation` group
                # sets subseed -1 / strength 0 exactly as this constant did.
                subseed=int(getattr(request, "subseed", -1)),
                subseed_strength=float(getattr(request, "subseed_strength", 0.0)),
                seed_resize_from_w=int(getattr(request, "seed_resize_from_w", -1)),
                seed_resize_from_h=int(getattr(request, "seed_resize_from_h", -1)),
                sampler_name=request.sampler,  # type: ignore[attr-defined]
                scheduler=request.scheduler,  # type: ignore[attr-defined]
                batch_size=int(request.batch_size),  # type: ignore[attr-defined]
                n_iter=1,
                steps=int(request.steps),  # type: ignore[attr-defined]
                cfg_scale=float(request.cfg_scale),  # type: ignore[attr-defined]
                distilled_cfg_scale=float(request.distilled_cfg_scale),  # type: ignore[attr-defined]
                width=int(request.width),  # type: ignore[attr-defined]
                height=int(request.height),  # type: ignore[attr-defined]
                # `getattr`, not attribute access: a request reaching this port
                # is duck-typed, and the doubles predate this field. Matching
                # `_hires_kwargs` below, which reads the same way.
                do_not_save_samples=True,
                do_not_save_grid=True,
                override_settings={},
                **operation_kwargs,
            )
            # None unless the owner asked for Soft Inpainting on an inpaint
            # job. `None` is what this port has always set and what every
            # other job keeps: a bridge is installed ONLY when there is a
            # reason for one, so a txt2img or plain img2img job takes the
            # identical path it took before WP1.6.
            processing.scripts = _soft_inpainting_bridge(  # type: ignore[attr-defined]
                request, is_inpaint)
            # `<lora:name:weight>` in the prompt does nothing without this.
            #
            # `parse_extra_network_prompts` strips the tag a few frames below,
            # in `process_images_inner`, and hands it to a registry that had no
            # LoRA handler in it -- Forge registers one from `before_ui`, and
            # Studio runs neither `load_scripts()` nor `before_ui_callback()`,
            # both refusals being deliberate. So the tag was parsed out and
            # discarded, and the image came back identical to one without it.
            # Measured: 0 of 262,144 pixels differed.
            #
            # Armed HERE rather than at bootstrap because this is where the
            # configured roots are reachable, and because a Studio that never
            # generates should not pay for a directory scan.
            _arm_lora_support(request)
            # One bracket, because `process_images_inner` runs base, the
            # upscale and Hires inside itself. The per-step rates come from
            # the progress callbacks; this is the wall clock the owner waits.
            # NOT "base": this bracket is around inherited
            # `process_images_inner`, which runs the base denoise, its decode,
            # the upscale AND the Hires denoise as one call. The metadata is
            # what makes the row interpretable, since it is not a single-stage
            # timer and must not be read as one.
            telemetry.start("primary_pipeline")
            processed = process_images_inner(processing)
            # THE SECOND PASS FOR AN IMAGE OPERATION, and it runs HERE --
            # before the clip below, not after it.
            #
            # The Extension clips first and then upscales
            # (`studio_generation.py:3431` then :3456), so its Hires pass
            # denoises at the new resolution with nothing clipping it
            # afterwards. Putting the pass above the existing clip means the
            # clip Studio already had simply lands after Hires, and the
            # post-Auto-Detail clip is untouched. Both masked clips therefore
            # follow the final pixel-changing stage that precedes them.
            #
            # A no-op for txt2img, where Neo's built-in `enable_hr` already ran
            # the second pass inside `process_images_inner` above.
            self._img2img_hires(request, source, processed, progress)
            self._clip_inpaint(request, processed)
            hires_on = bool(getattr(processing, "enable_hr", False))
            telemetry.stop(
                "primary_pipeline",
                width=int(getattr(processing, "width", 0) or 0),
                height=int(getattr(processing, "height", 0) or 0),
                steps=int(getattr(processing, "steps", 0) or 0),
                sampler=str(getattr(processing, "sampler_name", "") or ""),
                scheduler=str(getattr(processing, "scheduler", "") or ""),
                hires=hires_on,
                hires_target=(
                    f"{int(getattr(processing, 'hr_upscale_to_x', 0) or 0)}x"
                    f"{int(getattr(processing, 'hr_upscale_to_y', 0) or 0)}"
                    if hires_on else ""
                ),
                hires_steps=int(getattr(processing, "hr_second_pass_steps", 0) or 0),
                upscaler=str(getattr(processing, "hr_upscaler", "") or ""),
            )

            # `prepare_detail` opens here and closes when the first region
            # actually starts denoising, so the unload/reload churn between
            # Hires and Auto Detail lands in its own row rather than being
            # charged to the detail pass.
            telemetry.start("prepare_detail")
            telemetry.start("detail")
            self.auto_detail_outcomes = self._auto_detail(
                request, processing, processed, progress
            )
            telemetry.stop("prepare_detail")   # a no-op if it already closed
            telemetry.stop(
                "detail",
                regions=sum(int(getattr(o, "regions", 0) or 0)
                            for o in (self.auto_detail_outcomes or ())),
            )

            # Again after Auto Detail, which inpaints regions of its own.
            self._clip_inpaint(request, processed)

            telemetry.start("publish")
            outcome = self._publish(request, processing, processed, progress)
            telemetry.stop("publish")
            # At debug, so a production log is not made noisy by it. The
            # record is on the port either way, for a harness to read.
            # `logging` rather than a module logger: this file keeps no
            # module-level state, and the record lives on the port
            # regardless of whether anything is listening.
            logging.getLogger("studio.generation").debug(
                "GENERATION TIMELINE%s%s", chr(10), telemetry.summary())
        except BaseException as exc:  # noqa: BLE001 - released, then re-raised
            failure = exc
            # An out-of-memory in the second pass is the ONE failure whose
            # cause the owner can act on, and the one most likely to be
            # misread. Unconverted it surfaces as a generic backend failure --
            # or, when the allocator loses the race harder, as the exit-139
            # crash this project already spent a session attributing to a code
            # regression it had nothing to do with.
            #
            # No projection is attempted anywhere: there is no validated model
            # of what a Hires pass costs on an arbitrary card, and a
            # confident-looking guess would be worse than none. This reports
            # what was ACTUALLY measured at the moment it failed.
            if getattr(request, "enable_hr", False) and _looks_like_out_of_memory(exc):
                from .contracts import HeadlessError

                raise HeadlessError(
                    "GENERATION_HIRES_OUT_OF_MEMORY",
                    "The Hires pass ran out of GPU memory at "
                    f"{_hires_target_description(request)}. {_device_memory_summary()}",
                ) from exc
            raise
        finally:
            # Restored FIRST, and always: a patched module left behind
            # would attribute the next generation's moves to a telemetry
            # object nobody is reading.
            moves.__exit__(None, None, None)
            # Exactly once per job, on every exit path, before the references
            # are dropped. The engine is deliberately untouched.
            self.last_release = self._release_job(processing, processed, failure)
            processing = None
            processed = None
            self._forge_objects = None
            self._vae_component = None

        return outcome

    # -- auto detail -------------------------------------------------------


    def _clip_inpaint(self, request: Any, processed: Any) -> None:
        """Apply the outside-mask clip to every image, in place.

        Called TWICE, at the two points the Extension calls it
        (studio_generation.py:2013 and :2033): once after the base pass and
        again after Auto Detail. Auto Detail inpaints regions of its own and
        can move pixels outside the owner's mask, so a single clip before it
        would be undone.

        In place, so `_publish` needs no change and the intermediate images are
        never results -- the same rule `_auto_detail` follows.
        """

        source = getattr(request, "source", None)
        mask = getattr(source, "mask", None)
        if mask is None or not getattr(source, "mask_present", False):
            return
        images = list(getattr(processed, "images", ()) or ())
        if not images:
            return
        blur = int(getattr(getattr(request, "inpaint", None), "mask_blur", 4) or 0)
        processed.images = [
            _clip_to_mask(image, source.image, mask, blur) for image in images
        ]

    def _auto_detail(
        self,
        request: object,
        processing: object,
        processed: object,
        progress: object,
    ) -> tuple:
        """Run the enabled Auto Detail slots over the images, in order.

        Between `process_images_inner` and `_publish`, which is the only place
        the images exist and nothing has been published yet. `_publish` needs
        no change: this replaces `processed.images` in place, so the result
        that gets saved is the detailed one and the intermediate passes are
        never results.

        The RULES live in `forge_headless.auto_detail` and are tested without
        a GPU. What is here is the two things that genuinely need the engine:
        running a detector, and running the Neo inpaint pass.
        """

        from .auto_detail import run_auto_detail
        from .headless_progress import JobState

        slots = tuple(getattr(request, "ad_slots", ()) or ())
        if not getattr(request, "enable_adetailer", False) or not slots:
            return ()

        images = list(getattr(processed, "images", []) or [])
        if not images:
            return ()

        stages = {
            1: JobState.AUTO_DETAIL_1,
            2: JobState.AUTO_DETAIL_2,
            3: JobState.AUTO_DETAIL_3,
        }

        def on_slot_start(slot: object) -> None:
            state = stages.get(int(getattr(slot, "index", 0)))
            if state is not None and not progress.terminal:  # type: ignore[attr-defined]
                progress.advance_to(state)  # type: ignore[attr-defined]

        def cancelled() -> bool:
            return bool(
                getattr(progress, "cancellation_requested", False)
            )

        def detect(detector_path: str, image: object, *, confidence: float):
            from .detector_adapter import detect as run_detect

            return run_detect(detector_path, image, confidence=confidence)

        detailed, outcomes = run_auto_detail(
            images,
            slots,
            detect=detect,
            detail=lambda image, mask, slot: self._detail_pass(
                request, processing, image, mask, slot
            ),
            on_slot_start=on_slot_start,
            cancelled=cancelled,
        )
        # In place, so `_publish` reads the detailed image without knowing a
        # detector ever ran.
        processed.images = detailed  # type: ignore[attr-defined]
        return outcomes

    def _img2img_hires(self, request, source, processed, progress) -> None:
        """Hires for an image operation, in place. WP1.7.

        `enable_hr` is a `StableDiffusionProcessingTxt2Img` field, so Neo's
        built-in second pass is unreachable from an img2img job. The Extension
        answers that with `run_hires_fix` (`studio_generation.py:520-676`) and
        calls it for every non-txt2img job with Hires on and scale above 1 --
        no mask exclusion, so ordinary img2img and inpaint both get it
        (:3456). Studio's Hires controls reached neither until now.

        DIVERGENCE, deliberate: a FRESH processing object rather than the
        Extension's mutate-and-restore of the base one. `_detail_pass` already
        works this way, the engine stays resident through `sd_model` either
        way, and it deletes the whole hazard class the Extension's `_saved`
        dict exists to manage -- `mask`, `nmask` and `overlay_images` left at
        base resolution are three separate documented bugs there.

        Neo owns the actual work. The upscaler comes from `shared.sd_upscalers`
        and the denoise is `process_images_inner`; nothing here reimplements
        either.
        """

        if not bool(getattr(request, "enable_hr", False)):
            return
        scale = float(getattr(request, "hr_scale", 0.0) or 0.0)
        # `< 1.0`, not `<= 1.0`. The control's minimum IS 1.0, so an owner can
        # reach it, Hires reads as ON everywhere in the UI, and the second pass
        # was silently skipped -- no error, no metadata, nothing. A 1:1 second
        # pass is a refinement denoise, which is exactly what the txt2img path
        # runs at the same value.
        if scale < 1.0:
            return
        operation = getattr(request, "operation", None)
        if source is None or getattr(operation, "value", operation) == "txt2img":
            return
        images = list(getattr(processed, "images", ()) or ())
        if not images:
            return

        from modules.processing import (
            StableDiffusionProcessingImg2Img,
            process_images_inner,
        )
        from PIL import Image as _Image

        image = images[0]
        # FLOOR to a multiple of 8, which is the Extension's own arithmetic
        # (`int(w * scale) // 8 * 8`) and NOT `round8`'s nearest-8. No upper
        # bound: Studio imposes no maximum resolution, and this must never
        # acquire one.
        target_width = (int(image.width * scale) // 8) * 8
        target_height = (int(image.height * scale) // 8) * 8
        upscaled = _upscale_for_hires(
            image, str(getattr(request, "hr_upscaler", "") or ""),
            target_width, target_height)

        # The mask travels, upscaled. Without it the second pass redenoises the
        # whole frame and moves pixels the owner never asked to change; the
        # Extension passes it for the same reason (:610-617) and turns
        # full-resolution cropping off, because the mask is constraining the
        # denoise rather than selecting a region to crop to.
        mask = getattr(source, "mask", None)
        if mask is not None and getattr(source, "mask_present", False):
            mask = mask.convert("L").resize(
                (target_width, target_height), _Image.LANCZOS)
        else:
            mask = None

        steps = int(getattr(request, "hr_second_pass_steps", 0) or 0) or int(
            getattr(request, "steps", 20))
        cfg = float(getattr(request, "hr_cfg", 0.0) or 0.0) or float(
            getattr(request, "cfg_scale", 7.0))
        prompt = str(getattr(request, "hr_prompt", "") or "") or str(
            getattr(request, "positive_prompt", ""))
        negative = str(getattr(request, "hr_negative_prompt", "") or "") or str(
            getattr(request, "negative_prompt", ""))

        mask_kwargs = {}
        if mask is not None:
            mask_kwargs = {
                "mask": mask,
                "inpaint_full_res": False,
                "mask_blur": int(getattr(
                    getattr(request, "inpaint", None), "mask_blur", 4) or 0),
            }

        pass_ = StableDiffusionProcessingImg2Img(
            sd_model=self._engine,
            outpath_samples=str(self._result_root),
            outpath_grids=str(self._result_root),
            prompt=prompt,
            negative_prompt=negative,
            # The SAME seed as the base pass. A second pass that reseeded would
            # make a fixed-seed job irreproducible at exactly the point the
            # owner cares about.
            seed=int(getattr(request, "seed", 0)),
            subseed=-1,
            sampler_name=(getattr(request, "hr_sampler_name", "")
                          or getattr(request, "sampler", None)),
            scheduler=(getattr(request, "hr_scheduler", "")
                       or getattr(request, "scheduler", None)),
            batch_size=1,
            n_iter=1,
            steps=steps,
            cfg_scale=cfg,
            distilled_cfg_scale=float(
                getattr(request, "distilled_cfg_scale", 3.0)),
            width=target_width,
            height=target_height,
            init_images=[upscaled],
            denoising_strength=float(
                getattr(request, "hr_denoising_strength", 0.7)),
            do_not_save_samples=True,
            do_not_save_grid=True,
            override_settings={},
            **mask_kwargs,
        )
        # Soft Inpainting applies to this pass too. The Extension leaves its
        # alwayson script installed and hands the pass an `image_mask`, so
        # `processing_uses_inpainting` is true and the hooks run at the NEW
        # resolution -- which is the point of clearing the base-resolution
        # `mask`/`nmask` first. A fresh object has neither, so Neo `init()`
        # rebuilds both from the upscaled mask and the bridge blends against
        # the right tensors.
        pass_.scripts = _soft_inpainting_bridge(request, mask is not None)

        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            telemetry.start("img2img_hires")
        try:
            second = process_images_inner(pass_)
        finally:
            if telemetry is not None:
                telemetry.stop(
                    "img2img_hires", width=target_width, height=target_height,
                    steps=steps, upscaler=str(
                        getattr(request, "hr_upscaler", "") or ""))
        results = list(getattr(second, "images", ()) or ())
        if results:
            # In place, so `_publish` needs no change and the base frame is
            # never a result -- the same rule `_auto_detail` and
            # `_clip_inpaint` follow.
            processed.images = [results[0]] + images[1:]

    def _detail_pass(
        self,
        request: object,
        processing: object,
        image: object,
        mask: object,
        slot: object,
    ) -> object:
        """One Neo-compatible inpaint over one mask. Returns the new image.

        `mask=`, never `image_mask=`: the latter is `init=False` and
        `__post_init__` moves `mask` into it, so naming it directly is a
        TypeError. After construction `p.mask` is None and `p.image_mask`
        holds the mask -- and inside `init()` `mask` is rebound again, to a
        latent tensor. Three meanings, one attribute.

        `inpaint_full_res` is left at its default True: Neo crops to the
        masked region, generates there and pastes back inside
        `process_images_inner`, so nothing here composites.
        """

        from modules.processing import (
            StableDiffusionProcessingImg2Img,
            process_images_inner,
        )

        steps = int(getattr(slot, "steps", 0)) or int(getattr(request, "steps", 20))
        cfg = float(getattr(slot, "cfg", 0.0)) or float(
            getattr(request, "cfg_scale", 7.0)
        )
        prompt = str(getattr(slot, "prompt", "") or "") or str(
            getattr(request, "positive_prompt", "")
        )
        negative = str(getattr(slot, "negative_prompt", "") or "") or str(
            getattr(request, "negative_prompt", "")
        )

        # Named before use so the telemetry can record what was ACTUALLY
        # denoised rather than what was asked for. The frame handed in is the
        # post-Hires one; these are the base dimensions.
        _detail_width = int(getattr(request, "width", 0) or image.width)
        _detail_height = int(getattr(request, "height", 0) or image.height)
        telemetry = getattr(self, "telemetry", None)
        if telemetry is not None:
            # A SETUP CALL, not an encode. Neo's conditioning cache is
            # class-level and Studio cannot see through it without forking, so
            # naming this "encodes" would report a number nobody measured.
            telemetry.count("conditioning_setup_calls")
            telemetry.count("detail_regions")

        pass_ = StableDiffusionProcessingImg2Img(
            sd_model=self._engine,
            outpath_samples=str(self._result_root),
            outpath_grids=str(self._result_root),
            prompt=prompt,
            negative_prompt=negative,
            seed=int(getattr(request, "seed", 0)),
            # STILL PINNED, deliberately. This is the Auto Detail detail pass,
            # not the base image. Whether an owner's variation seed should also
            # perturb each detected region's inpaint is a product question --
            # the book gives AD slots their own settings -- and carrying it here
            # silently would answer that question by accident. The base pass
            # above takes the variation; this one does not until someone
            # decides it should.
            subseed=-1,
            sampler_name=getattr(request, "sampler", None),
            scheduler=getattr(request, "scheduler", None),
            batch_size=1,
            n_iter=1,
            steps=steps,
            cfg_scale=cfg,
            distilled_cfg_scale=float(getattr(request, "distilled_cfg_scale", 3.0)),
            # THE BASE SIZE, not the image's own. This is the single line
            # that made Auto Detail 4.4x slower than Neo's.
            #
            # `image` here is the post-Hires frame, 1536x1536 for a 1024 base
            # at 1.5x. Neo crops to the masked region and then resizes that
            # crop to `self.width` x `self.height` before denoising
            # (`modules/processing.py:1767-1770`), so passing the frame's own
            # size denoises a FACE at 1536x1536.
            #
            # ADetailer -- the behaviour oracle, `scripts/!adetailer.py:352-363`
            # -- takes `init_images=[image]` from the same post-Hires frame but
            # sizes the pass from `p.width`/`p.height`, which Hires never
            # touches: it writes its target to `hr_upscale_to_x`
            # (`modules/processing.py:1283`) and leaves `width` at the base.
            #
            #   area ratio      1536^2 / 1024^2   = 2.25x
            #   measured gap    4.78 / 1.08 it/s  = 4.43x
            #   4.43 / 2.25                       = 1.97x, the global gap
            #
            # So this accounted for exactly the part of Auto Detail's cost that
            # the global denoising gap does not. It is a correctness difference
            # too, not only a cost one: a face detailed at 1536 is not the face
            # the oracle produces at 1024.
            width=_detail_width,
            height=_detail_height,
            init_images=[image],
            mask=mask,
            mask_blur=int(getattr(slot, "mask_blur", 6)),
            # 1 == keep the original masked content as the starting point,
            # which is what a DETAIL pass means. The default, 0, fills the
            # region first and discards the face it was asked to improve.
            inpainting_fill=1,
            inpaint_full_res_padding=int(getattr(slot, "inpaint_padding", 32)),
            denoising_strength=float(getattr(slot, "denoising_strength", 0.30)),
            do_not_save_samples=True,
            do_not_save_grid=True,
            override_settings={},
        )
        pass_.scripts = None  # type: ignore[attr-defined]
        # The detail preparation ends HERE -- detection, the crop, the mask
        # and the conditioning setup are done, and the first region is about
        # to denoise. Everything charged to `prepare_detail` is therefore work
        # between the two stages, which is what the retention plan targets.
        if telemetry is not None:
            telemetry.stop("prepare_detail")
        result = process_images_inner(pass_)
        images = list(getattr(result, "images", []) or [])
        # A pass that produced nothing leaves the image it was given, rather
        # than replacing a real picture with None.
        return images[0] if images else image

    # -- publication -------------------------------------------------------

    def _publish(
        self,
        request: object,
        processing: object,
        processed: object,
        progress: object,
    ) -> object:
        from .contracts import HeadlessError
        from .generation_port import GenerationOutcome
        from .headless_progress import JobState

        sampler_object = getattr(processing, "sampler", None)
        self.resolved_sampler = str(
            getattr(sampler_object, "name", None)
            or getattr(processing, "sampler_name", "")
            or ""
        )
        self.resolved_scheduler = str(
            getattr(sampler_object, "scheduler", None)
            or getattr(processing, "scheduler", "")
            or ""
        )
        self.sampler_resolution_source = (
            "sampler object" if sampler_object is not None else "request (unavailable)"
        )

        images = list(getattr(processed, "images", []) or [])
        if not images:
            progress.mark_failed("generation produced no image")  # type: ignore[attr-defined]
            raise HeadlessError(
                "GENERATION_NO_RESULT", "The generation produced no image."
            )

        if not progress.terminal:  # type: ignore[attr-defined]
            progress.advance_to(JobState.DECODING)  # type: ignore[attr-defined]
        if not progress.terminal:  # type: ignore[attr-defined]
            progress.advance_to(JobState.PUBLISHING)  # type: ignore[attr-defined]

        # The owner's chosen format, resolved ONCE so the name, the encoder
        # and the reported media type cannot disagree with each other.
        encoder_format, extension, media_type = resolve_output_format(
            getattr(request, "output", None))
        name = safe_result_name(
            getattr(request, "request_id", ""), index=self.generate_calls,
            extension=extension,
        )
        # The engine's own parameter string, taken rather than rebuilt.
        # `process_images_inner` already called `create_infotext` for this
        # image (`processing.py:1118-1119`) and handed it back here; Studio
        # read `images` off the same object and ignored this field, which is
        # why every result the owner ever saved was anonymous.
        infotexts = list(getattr(processed, "infotexts", []) or [])
        infotext = str(infotexts[0]) if infotexts else ""

        image = _stamped(images[0], request)

        self._result_root.mkdir(parents=True, exist_ok=True)
        save_result_exclusively(
            image, self._result_root / name,
            encoder_format=encoder_format,
            save_kwargs={
                **output_save_kwargs(getattr(request, "output", None),
                                     encoder_format),
                # Merged rather than passed separately so there is one
                # save call and one set of encoder arguments. The two
                # groups cannot collide: `pnginfo`/`exif` are metadata
                # containers, and nothing above emits either.
                **metadata_save_kwargs(infotext, encoder_format,
                                       getattr(request, "output", None)),
            },
        )
        width, height = image.size

        seed = int(getattr(processed, "seed", None) or getattr(request, "seed", 0))
        progress.mark_completed()  # type: ignore[attr-defined]

        return GenerationOutcome(
            job_id=request.request_id,  # type: ignore[attr-defined]
            request_id=request.request_id,  # type: ignore[attr-defined]
            result_relative_location=name,
            # From the same row as the extension above. A `.jpg` reported as
            # `image/png` is the failure this feature is most likely to
            # produce, and taking both from one table makes it inexpressible.
            media_type=media_type,
            width=int(width),
            height=int(height),
            seed=seed,
            auto_detail=tuple(getattr(self, "auto_detail_outcomes", ()) or ()),
            # Returned as well as embedded: the file carries it for other
            # programs, and the page needs it for the output info bar, Copy
            # Seed and the two Recycle buttons -- all of which read a string
            # that was, until now, always empty.
            infotext=infotext,
        )


__all__ = (
    "StudioLiveGenerationPort",
    "safe_result_name",
    "save_result_exclusively",
)
