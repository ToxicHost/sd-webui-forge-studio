"""Decode an image to raw sRGB RGBA on the SERVER, deliberately.

The browser's own image decode bakes the DISPLAY profile into the pixels on a
calibrated setup. Once those pixels land on a canvas layer, every downstream
export, img2img and inpaint carries the dulled colours, and no display setting
undoes it -- the damage is in the data by then. So the decode happens here,
with an explicit ICC conversion to sRGB, and the browser receives bytes it has
no opportunity to reinterpret.

Four honest outcomes, reported rather than guessed:

```text
srgb         the image declared an sRGB profile; used as-is
converted    a DIFFERENT profile was declared and converted to sRGB
missing      no profile at all. Passed through UNCONVERTED and said so --
             assuming sRGB would be a guess wearing a measurement's clothes
unavailable  Pillow's ICC support is absent. The bytes still decode; the
             colour claim does not
```

`missing` is the one worth reading twice. An untagged image is not an sRGB
image, it is an image whose author did not say. Converting it *from* an assumed
sRGB is a no-op, and converting it from anything else would be invention, so it
passes through and the caller is told the profile is unknown.

Nothing is imported from Pillow at module scope. The canonical discovery runner
executes under `-I -S -B`, which hides site-packages, so a module-level
`from PIL import Image` would fail collection for the entire suite rather than
for the one route that needs it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The four values `profile_state` may take. Pinned by test so a new one cannot
#: be introduced without the frontend contract being updated to know it.
PROFILE_STATES = ("srgb", "converted", "missing", "unavailable")

#: RGBA. The response contract is exactly `width * height * 4` bytes, and the
#: client asserts that length before trusting the buffer.
BYTES_PER_PIXEL = 4


class PixelImportError(Exception):
    """The bytes could not be decoded as an image."""


@dataclass(frozen=True)
class DecodedPixels:
    """One decoded image: raw RGBA and an honest account of its colour."""

    width: int
    height: int
    rgba: bytes
    profile_state: str

    def __post_init__(self) -> None:
        expected = self.width * self.height * BYTES_PER_PIXEL
        if len(self.rgba) != expected:
            raise PixelImportError(
                f"Decoded {len(self.rgba)} bytes, expected {expected}."
            )
        if self.profile_state not in PROFILE_STATES:
            raise PixelImportError(f"Unknown profile state {self.profile_state!r}.")


def _srgb_destination():
    """The sRGB profile to convert INTO, or None when ICC is unavailable."""

    try:
        from PIL import ImageCms
    except Exception:  # noqa: BLE001 - absence is an outcome, not a failure
        return None
    try:
        return ImageCms.createProfile("sRGB")
    except Exception:  # noqa: BLE001
        return None


def _describes_srgb(profile_bytes: bytes) -> bool:
    """Whether an embedded profile is already sRGB, by its own description.

    Read from the profile rather than assumed from its size: two profiles can
    share a byte count and describe different spaces.
    """

    try:
        import io

        from PIL import ImageCms

        profile = ImageCms.ImageCmsProfile(io.BytesIO(profile_bytes))
        description = ImageCms.getProfileDescription(profile) or ""
    except Exception:  # noqa: BLE001
        return False
    return "srgb" in description.strip().lower()


def decode_to_srgb_rgba(data: bytes) -> DecodedPixels:
    """Decode encoded image bytes to raw sRGB RGBA.

    `data` is attacker-influenceable: it arrives from an owner-chosen file or
    from a result handle. Every decoder failure is converted into
    `PixelImportError` so the route answers with a refusal rather than a
    traceback, and so a malformed image cannot be told apart from an
    unsupported one by the shape of the response.
    """

    if not isinstance(data, (bytes, bytearray)) or not data:
        raise PixelImportError("No image bytes were supplied.")

    try:
        import io

        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise PixelImportError("Image decoding is unavailable.") from exc

    try:
        image = Image.open(io.BytesIO(bytes(data)))
        image.load()
    except Exception as exc:  # noqa: BLE001 - any decoder failure is a refusal
        raise PixelImportError("Those bytes are not a readable image.") from exc

    profile_bytes = image.info.get("icc_profile")
    destination = _srgb_destination()

    if not profile_bytes:
        state = "missing" if destination is not None else "unavailable"
        converted = image.convert("RGBA")
    elif destination is None:
        state = "unavailable"
        converted = image.convert("RGBA")
    elif _describes_srgb(profile_bytes):
        state = "srgb"
        converted = image.convert("RGBA")
    else:
        try:
            import io

            from PIL import ImageCms

            source = ImageCms.ImageCmsProfile(io.BytesIO(profile_bytes))
            converted = ImageCms.profileToProfile(
                image, source, destination, outputMode="RGBA"
            )
            state = "converted"
        except Exception:  # noqa: BLE001 - a failed conversion is not a failed
            # decode. The pixels are still usable; only the colour claim is
            # weaker, and saying so beats refusing an image the owner can see.
            converted = image.convert("RGBA")
            state = "unavailable"

    if converted is None:  # pragma: no cover - defensive
        raise PixelImportError("The image could not be converted to RGBA.")

    width, height = converted.size
    if width <= 0 or height <= 0:
        raise PixelImportError("That image has no pixels.")

    return DecodedPixels(
        width=int(width),
        height=int(height),
        rgba=converted.tobytes(),
        profile_state=state,
    )


def sample_grid(pixels: DecodedPixels, *, step: int = 8) -> list[dict[str, int]]:
    """A sparse grid of RGBA samples, for the acceptance instrument.

    Acceptance for this path is "the raw path was actually exercised", and that
    cannot be proven by an image that merely looks right -- the fallback
    produces a plausible image too. This returns addressable pixel values so a
    diagnostic can compare the two paths numerically.
    """

    step = max(1, int(step))
    out: list[dict[str, int]] = []
    for y in range(0, pixels.height, step):
        for x in range(0, pixels.width, step):
            offset = (y * pixels.width + x) * BYTES_PER_PIXEL
            out.append(
                {
                    "x": x,
                    "y": y,
                    "r": pixels.rgba[offset],
                    "g": pixels.rgba[offset + 1],
                    "b": pixels.rgba[offset + 2],
                    "a": pixels.rgba[offset + 3],
                }
            )
    return out


__all__ = (
    "BYTES_PER_PIXEL",
    "PROFILE_STATES",
    "DecodedPixels",
    "PixelImportError",
    "decode_to_srgb_rgba",
    "sample_grid",
)
