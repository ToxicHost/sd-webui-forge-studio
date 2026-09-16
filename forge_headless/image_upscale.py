"""Upscale one image, with no checkpoint and no diffusion.

    UPSCALE CANVAS  ->  /studio/upscale_and_refine  ->  here

WHY THIS IS ITS OWN THING

Studio already upscales, inside the Hires second pass, and that is the wrong
tool for the job this serves. Owner:

    one alpha tester is on a 6gb card and cannot do Hires, Adetailer, and Gen.
    He NEEDS the full upscale tool working

A pure ESRGAN pass is the cheapest large-image path Studio has -- a small
convolutional net, tiled, with NO checkpoint resident and NO sampling. It is
exactly what a card that cannot afford Hires needs, which is why the panel that
drives it was never a candidate for hiding.

WHAT IT DELIBERATELY DOES NOT DO

`_upscale_for_hires` falls back to LANCZOS when a named upscaler cannot be
found or throws. That is right inside a generation -- a failed upscaler must
not fail an image the owner already waited for -- and wrong here, where the
upscale IS the job. Handing back a bilinear-soft image and reporting success
would be the same lie in a smaller frame.

So this REFUSES an upscaler it cannot find, and only treats the fallback names
as fallbacks.

Studio-owned. Imports Neo only through the port's existing helper, and only
inside the call.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass
from typing import Any

from .contracts import HeadlessError

#: The names that mean "no upscaler", and resize rather than refuse.
#:
#: "Latent" is here because a latent upscaler has no latent to work on outside
#: a sampling pass -- the same reason the Hires helper excludes it -- and
#: pretending otherwise would silently produce something nobody selected.
PASSTHROUGH_UPSCALERS = ("", "None", "Latent", "Lanczos", "Nearest")

#: A data URL Studio will accept for the canvas composite.
_DATA_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,(.+)$", re.DOTALL)

UPSCALE_REFUSED = "UPSCALE_REFUSED"
UPSCALE_UNSUPPORTED = "UPSCALE_UNSUPPORTED"


@dataclass(frozen=True)
class UpscaleResult:
    """One finished upscale, and the size it actually reached."""

    image_b64: str
    width: int
    height: int
    upscaler: str


def _refuse(code: str, message: str) -> HeadlessError:
    return HeadlessError(code, message)


def available_upscalers() -> tuple[str, ...]:
    """Every upscaler name the engine offers, plus the passthrough names.

    Read from the engine rather than from a list here, because a list here
    would be a second answer that drifts. Empty when the engine is not
    loaded -- which is not an error, it just means nothing but the fallbacks
    can be offered yet.
    """

    try:
        from modules import shared

        names = tuple(
            str(getattr(candidate, "name", ""))
            for candidate in getattr(shared, "sd_upscalers", ()) or ()
            if getattr(candidate, "name", "")
        )
    except (Exception, SystemExit):
        # SystemExit is named for the same reason the port names it: importing
        # `modules.shared` parses argv at module scope and exits on one it does
        # not recognise.
        return PASSTHROUGH_UPSCALERS
    return tuple(dict.fromkeys(PASSTHROUGH_UPSCALERS + names))


def decode_canvas(image_b64: str) -> Any:
    """The canvas composite, as a PIL image, or a named refusal."""

    if not isinstance(image_b64, str) or not image_b64:
        raise _refuse(UPSCALE_REFUSED, "No image was supplied to upscale.")

    matched = _DATA_URL.match(image_b64)
    payload = matched.group(2) if matched else image_b64
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise _refuse(UPSCALE_REFUSED,
                      "That image is not valid base64.") from error
    if not raw:
        raise _refuse(UPSCALE_REFUSED, "That image is empty.")

    from PIL import Image, UnidentifiedImageError
    from PIL.Image import DecompressionBombError

    try:
        opened = Image.open(io.BytesIO(raw))
        opened.load()
    except DecompressionBombError as error:
        raise _refuse(
            UPSCALE_REFUSED,
            f"That image is beyond Pillow's decode guard of "
            f"{Image.MAX_IMAGE_PIXELS} pixels.") from error
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise _refuse(UPSCALE_REFUSED,
                      "That file is not an image Studio can read.") from error
    return opened


def target_size(width: int, height: int, scale: float) -> tuple[int, int]:
    """The size a scale asks for, aligned to the VAE factor.

    Aligned because the result goes back onto the canvas and is very often
    generated from next, and an odd canvas would make every later pass carry
    the rounding. `max(8, ...)` because a scale that rounds a small image to
    zero is not a size.
    """

    return (max(8, int(round(width * scale)) // 8 * 8),
            max(8, int(round(height * scale)) // 8 * 8))


def upscale(image_b64: str, *, upscaler: str, scale: float,
            run_refine: bool = False, run_ad: bool = False) -> UpscaleResult:
    """Upscale the canvas composite. No checkpoint, no sampling.

    `run_refine` and `run_ad` are REFUSED rather than ignored. They are a full
    img2img pass and Auto Detail -- the second half of WP6.4, and precisely the
    expensive work the owner this was built for cannot afford. Accepting the
    fields and quietly not doing them is the defect this program has spent a
    week removing from other paths.
    """

    if run_refine or run_ad:
        wanted = " and ".join(
            name for name, asked in (("refine", run_refine), ("Auto Detail", run_ad))
            if asked)
        raise _refuse(
            UPSCALE_UNSUPPORTED,
            f"Upscale can run on its own, but not with {wanted} yet. "
            "Upscale without it, then run a normal generation on the result.")

    try:
        scale = float(scale)
    except (TypeError, ValueError) as error:
        raise _refuse(UPSCALE_REFUSED, "Scale must be a number.") from error
    if scale <= 0:
        raise _refuse(UPSCALE_REFUSED, f"Scale {scale} is not a size.")

    name = str(upscaler or "")
    if name not in PASSTHROUGH_UPSCALERS and name not in available_upscalers():
        raise _refuse(
            UPSCALE_REFUSED,
            f"There is no upscaler called {name!r}. "
            "Studio will not quietly resize instead.")

    image = decode_canvas(image_b64)
    width, height = target_size(image.width, image.height, scale)

    from .live_generation_port import _upscale_for_hires

    scaled = _upscale_for_hires(image, name, width, height)

    buffer = io.BytesIO()
    scaled.save(buffer, format="PNG")
    return UpscaleResult(
        image_b64="data:image/png;base64,"
                  + base64.b64encode(buffer.getvalue()).decode("ascii"),
        width=int(scaled.width),
        height=int(scaled.height),
        upscaler=name,
    )


__all__ = (
    "PASSTHROUGH_UPSCALERS",
    "UPSCALE_REFUSED",
    "UPSCALE_UNSUPPORTED",
    "UpscaleResult",
    "available_upscalers",
    "decode_canvas",
    "target_size",
    "upscale",
)
