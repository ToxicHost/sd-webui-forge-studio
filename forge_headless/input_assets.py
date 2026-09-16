"""Turning what the browser SENT into something the sampler can be given.

`forge_studio.contracts.InputAsset` carries what the page said: a data URL, a
media type, and dimensions. None of that is trusted. This decodes the bytes and
establishes what they actually are, here in `forge_headless` because
`forge_studio` may not import PIL.

Three rules, and the order matters:

**Refuse before spending.** The encoded ceiling is checked against the string
BEFORE any decode, because a decoded ceiling can only be applied after
allocating the memory it exists to prevent. A 32 MB base64 payload is roughly a
24 MB image, which is a 4096x4096 PNG with room to spare.

**Believe the pixels, not the payload.** The declared width and height exist so
a mismatch is a named refusal instead of a crash inside the sampler. They are
compared against the decode and then discarded.

**Every failure is named.** Section 50: a bad source image is
`SOURCE_IMAGE_INVALID`, not a 500 and not a traceback. The owner gets a
sentence they can act on, and the job never reaches admission.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from .contracts import HeadlessError
from .generation_request import SourceImage

#: `data:<media-type>;base64,<payload>`. Anchored, because a permissive match
#: here is a decoder reachable from an unvalidated browser string.
_DATA_URL = re.compile(r"^data:([a-z]+/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)

#: Mirrors `forge_studio.contracts`. Duplicated rather than imported: the
#: direction rule permits it, but a ceiling that lives on both sides of a
#: boundary should be visible on both sides rather than looked up.
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
SOURCE_IMAGE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")

#: THERE IS NO STUDIO PIXEL CAP. `MAX_SOURCE_PIXELS = 4096 * 4096` lived here
#: and refused any source image above 16.7 megapixels -- so a photograph from
#: any real camera could not be used for img2img or inpaint.
#:
#: Its justification did not survive reading: it said the byte ceiling "does
#: not bound the allocation" because a small file can decode to an enormous
#: image. True, and irrelevant here -- `decode_source` calls `load()` to force
#: the decode BEFORE this check ran, so the allocation it claimed to prevent
#: had already happened. It bounded nothing and capped the owner.
#:
#: Removed 2026-08-20 by owner ruling: "we should have no restrictions like
#: that." `Evidence/source-review/AR6.3-source-image-cap.md`.
#:
#: What remains is Pillow's own `MAX_IMAGE_PIXELS` (89478485, about 9459
#: square) -- a LIBRARY default rather than a Studio opinion, which
#: `decode_source` now reports as a named refusal instead of letting it escape
#: uncaught.


class InputAssetRefused(HeadlessError):
    """A supplied image Studio will not use.

    Nothing added: `HeadlessError` already carries `(code, message)` and
    renders `to_dict`, which is the envelope every other headless refusal
    speaks. A subclass exists only so a caller can catch THIS without catching
    every headless failure.
    """


def _refuse(code: str, message: str) -> "InputAssetRefused":
    return InputAssetRefused(code, message)


def decode_source(asset: Any, *, required: bool) -> SourceImage | None:
    """One `InputAsset` into a verified `SourceImage`, or a named refusal.

    `required` is the operation's business, not this function's: img2img and
    inpaint pass True, and txt2img never calls here at all.
    """

    data_url = str(getattr(asset, "data_url", "") or "") if asset is not None else ""
    if not data_url:
        if required:
            raise _refuse(
                "SOURCE_IMAGE_REQUIRED",
                "This operation needs a source image and none was supplied.")
        return None

    # BEFORE the decode. The whole point of an encoded ceiling.
    if len(data_url) > MAX_SOURCE_IMAGE_BYTES:
        raise _refuse(
            "SOURCE_IMAGE_INVALID",
            f"That image is larger than the "
            f"{MAX_SOURCE_IMAGE_BYTES // (1024 * 1024)} MB limit.")

    matched = _DATA_URL.match(data_url)
    if matched is None:
        raise _refuse("SOURCE_IMAGE_INVALID",
                      "The source image is not a base64 data URL.")
    media_type = matched.group(1).lower()
    if media_type not in SOURCE_IMAGE_MEDIA_TYPES:
        raise _refuse(
            "SOURCE_IMAGE_INVALID",
            f"Studio does not read {media_type} images.")

    try:
        raw = base64.b64decode(matched.group(2), validate=True)
    except (binascii.Error, ValueError) as error:
        raise _refuse("SOURCE_IMAGE_INVALID",
                      "The source image is not valid base64.") from error
    if not raw:
        raise _refuse("SOURCE_IMAGE_INVALID", "The source image is empty.")

    from io import BytesIO

    from PIL import Image, UnidentifiedImageError
    from PIL.Image import DecompressionBombError

    try:
        opened = Image.open(BytesIO(raw))
        # `load()` forces the decode NOW, inside this try, so a truncated file
        # becomes a named refusal here rather than an exception three layers
        # down inside the sampler.
        opened.load()
    except DecompressionBombError as error:
        # Pillow's own guard, and the only pixel ceiling left. It is NOT caught
        # by the tuple below -- it subclasses none of those -- so once Studio's
        # own cap was removed this would have escaped uncaught the moment an
        # image passed 89 megapixels. Named here so an owner with a very large
        # panorama is told what happened rather than shown a stack trace, and
        # so the number is attributable to Pillow rather than to us.
        raise _refuse(
            "SOURCE_IMAGE_INVALID",
            f"That image is beyond Pillow's decode guard of "
            f"{Image.MAX_IMAGE_PIXELS} pixels.") from error
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise _refuse("SOURCE_IMAGE_INVALID",
                      "That file is not an image Studio can read.") from error

    width, height = opened.size
    if width <= 0 or height <= 0:
        raise _refuse("SOURCE_IMAGE_INVALID", "That image has no size.")

    # What the page CLAIMED, checked against what arrived. Reported rather than
    # silently corrected: a page that disagrees with its own export is a bug
    # worth surfacing, and correcting it quietly would hide the next one.
    declared_w = int(getattr(asset, "width", 0) or 0)
    declared_h = int(getattr(asset, "height", 0) or 0)
    if declared_w and declared_h and (declared_w, declared_h) != (width, height):
        raise _refuse(
            "SOURCE_IMAGE_INVALID",
            f"The source image is {width}x{height}, "
            f"but the request described it as {declared_w}x{declared_h}.")

    # RGB, because that is what the engine samples. An alpha channel on an
    # init image is not transparency to the sampler, it is a fourth plane it
    # will not read -- dropping it here keeps that decision in one place.
    if opened.mode != "RGB":
        opened = opened.convert("RGB")

    return SourceImage(
        image=opened,
        mask=None,
        width=width,
        height=height,
        media_type=media_type,
        mask_present=False,
    )


def decode_mask(asset: Any, source: SourceImage) -> Any:
    """One mask asset into the single-channel image the engine reads.

    ITS OWN REFUSAL CODES. Reusing `SOURCE_IMAGE_*` would tell an owner whose
    mask is malformed that their source image is wrong, and the two are
    separate assets for exactly that reason.

    Studio's Canvas has already done the hard part: `exportMask` paints white
    wherever alpha is non-zero on a black ground and returns an opaque PNG, so
    there is no alpha channel to interpret here. Neo's own img2img reads a
    painted mask out of the alpha plane (modules/img2img.py:192-194) because
    ITS canvas hands one over; ours does not, and pretending otherwise would
    read an all-opaque plane and mask the entire image.

    What IS copied from Neo is the binarisation -- `mask.point(v > 128)` at
    modules/img2img.py:207 -- because a soft edge here would be blurred a
    second time by `mask_blur` downstream and the two would compound. Neo does
    every other mask operation itself inside
    `StableDiffusionProcessingImg2Img.init()`: blur, inversion, the
    full-resolution crop and the composite back. Studio must not reimplement
    any of it.
    """

    data_url = str(getattr(asset, "data_url", "") or "") if asset is not None else ""
    if not data_url:
        raise _refuse("MASK_REQUIRED",
                      "Inpaint needs a mask and none was supplied.")
    if len(data_url) > MAX_SOURCE_IMAGE_BYTES:
        raise _refuse(
            "MASK_INVALID",
            f"That mask is larger than the "
            f"{MAX_SOURCE_IMAGE_BYTES // (1024 * 1024)} MB limit.")

    matched = _DATA_URL.match(data_url)
    if matched is None:
        raise _refuse("MASK_INVALID", "The mask is not a base64 data URL.")
    if matched.group(1).lower() not in SOURCE_IMAGE_MEDIA_TYPES:
        raise _refuse("MASK_INVALID",
                      f"Studio does not read {matched.group(1)} masks.")
    try:
        raw = base64.b64decode(matched.group(2), validate=True)
    except (binascii.Error, ValueError) as error:
        raise _refuse("MASK_INVALID",
                      "The mask is not valid base64.") from error
    if not raw:
        raise _refuse("MASK_INVALID", "The mask is empty.")

    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    try:
        opened = Image.open(BytesIO(raw))
        opened.load()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise _refuse("MASK_INVALID",
                      "That file is not an image Studio can read.") from error

    # RESIZED, not refused. The first version raised
    # MASK_DIMENSION_MISMATCH on the reasoning that a mask which does not line
    # up is "a mask for a different image" -- which is wrong, and refused jobs
    # the Extension runs. The Canvas exports at document size while the owner
    # generates at whatever width/height they chose, so the two differ as a
    # matter of course. `_prepare_mask` resizes with LANCZOS
    # (studio_generation.py:786) and Studio matches it.
    from PIL import Image

    grey = opened.convert("L")
    if grey.size != (source.width, source.height):
        grey = grey.resize((source.width, source.height), Image.LANCZOS)

    # Is anything actually painted? The Extension asks the same question
    # (`np.array(mask).max() >= 10`) because a mask can be painted and then
    # erased, leaving a black image that is not the frontend's `"null"`
    # sentinel and would otherwise be inpainted against nothing.
    #
    # DIVERGENCE, recorded: the Extension DOWNGRADES to img2img here, because
    # its operation is derived from what it happens to hold. Studio's operation
    # is DECLARED and admission has already refused inpaint without a mask by
    # name -- so silently rewriting the owner's declared operation would
    # contradict the rule that no field is quietly ignored. Refused instead,
    # with a code that says which of the two it is.
    if max(grey.getextrema()) < 10:
        raise _refuse(
            "MASK_EMPTY",
            "The mask has nothing painted in it.")

    return grey.point(lambda value: 255 if value > 128 else 0)


__all__ = (
    "MAX_SOURCE_IMAGE_BYTES",
    "decode_mask",
    "SOURCE_IMAGE_MEDIA_TYPES",
    "InputAssetRefused",
    "decode_source",
)
