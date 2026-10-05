"""Save a finished output image: the output panel's Save and Save to Gallery.

P1. `app.js` `_saveSelectedOutput` posts `/studio/save_image`, which Standalone
did not serve, so every Save failed. This is the Extension's handler
(`scripts/studio_api.py:6081-6260`) on Standalone's own folders and encoders:
the image is written by `save_result_exclusively`, the generation writer, so a
saved file is tagged sRGB, carries its parameters the way every reader expects,
and can never overwrite an existing file.

Divergences from the Extension, each deliberate, are recorded in
`Evidence/source-review/P1-save-output-image.md`: Studio's result root is the
base folder, there is no silent fallback folder, the reply never carries a
path, and a subfolder is a plain name.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

#: The UI's format names. Anything else saves PNG, as the Extension does
#: (`ext_map.get(req.format, "png")`).
_FORMATS = ("png", "jpeg", "webp")

#: A subfolder is one or more plain names. The UI only ever sends "" or
#: "downloads"; anything that is not a plain name is refused rather than
#: cleaned, because a cleaned absolute path is still a path.
_SUBFOLDER = re.compile(r"^[\w\- ]+(?:/[\w\- ]+)*$")

#: How many `_N` suffixes to try before giving up. The Extension loops without
#: a bound; a bound turns a pathological folder into an error, not a hang.
_MAX_SUFFIX = 10_000


@dataclass(frozen=True)
class SavedImage:
    """What was written. `path` stays on the server; the reply never has it."""

    filename: str
    path: Path
    media_type: str


class SaveRefusal(Exception):
    """A save that was not made, with the status and the owner-facing reason."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def save_output_image(payload: Any, *, result_root: Any,
                      extra_roots: Iterable[Any] = ()) -> SavedImage:
    """Write one output image and say what was written.

    `result_root` is Studio's output folder; `extra_roots` are the other
    folders a `dest_dir` may name (the Gallery's linked folders and
    `STUDIO_SAVE_ROOTS`). Raises `SaveRefusal` for anything not saved.
    """

    from forge_headless.contracts import HeadlessError
    from forge_headless.live_generation_port import (
        OUTPUT_FORMATS,
        metadata_save_kwargs,
        output_save_kwargs,
        save_result_exclusively,
    )

    if not isinstance(payload, dict):
        raise SaveRefusal("The save request could not be read.")
    if payload.get("save_token") or str(payload.get("full_path") or "").strip():
        raise SaveRefusal("Server-side Save As has been disabled. "
                          "Use browser Save As / Download instead.")

    image = _decode(payload.get("image_b64"))
    name = str(payload.get("format") or "png").strip().lower()
    if name not in _FORMATS:
        name = "png"
    quality = _quality(payload.get("quality"))
    encoder, extension, media_type = OUTPUT_FORMATS[name]
    options = SimpleNamespace(format=name, quality=quality, lossless=False,
                              embed_metadata=True)
    save_kwargs = dict(output_save_kwargs(options, encoder))
    metadata = payload.get("metadata")
    save_kwargs.update(metadata_save_kwargs(
        metadata if isinstance(metadata, str) else "", encoder, options))

    folder = _destination(payload, result_root=result_root, extra_roots=extra_roots)
    stem = _stem(payload.get("filename"))
    for counter in range(_MAX_SUFFIX):
        candidate = folder / (f"{stem}.{extension}" if counter == 0
                              else f"{stem}_{counter}.{extension}")
        if candidate.exists():
            continue
        try:
            save_result_exclusively(image, candidate, encoder_format=encoder,
                                    save_kwargs=save_kwargs)
        except HeadlessError:
            # Another writer took this name between the check and the
            # exclusive create. The next suffix, as the Extension's loop does.
            continue
        except OSError as error:
            raise SaveRefusal(f"The image could not be written: {error.strerror or error}.",
                              500) from None
        return SavedImage(filename=candidate.name, path=candidate, media_type=media_type)
    raise SaveRefusal("No free file name was found in that folder.", 500)


def _decode(value: Any):
    """The image, decoded strictly, or a refusal saying the data is bad."""

    from PIL import Image

    text = value if isinstance(value, str) else ""
    if "," in text:
        text = text.split(",", 1)[1]
    try:
        image = Image.open(io.BytesIO(base64.b64decode(text, validate=True)))
        image.load()
    except (binascii.Error, ValueError, OSError) as error:
        raise SaveRefusal(f"Invalid image data: {error}") from None
    return image


def _quality(value: Any) -> int:
    """1..100, default 80 -- the Extension's field (`ge=1, le=100`)."""

    if value is None:
        return 80
    try:
        quality = int(value)
    except (TypeError, ValueError):
        raise SaveRefusal("Quality must be a whole number from 1 to 100.") from None
    if not 1 <= quality <= 100:
        raise SaveRefusal("Quality must be a whole number from 1 to 100.")
    return quality


def _stem(value: Any) -> str:
    """The Extension's filename rule, or `studio_<time>_<pid>`."""

    raw = str(value or "").strip()
    if raw:
        raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
        dot = raw.rfind(".")
        raw = raw[:dot] if dot > 0 else raw
        raw = re.sub(r"[^\w\-. ()]", "_", raw).strip(" .")[:120]
    return raw or f"studio_{int(time.time())}_{os.getpid()}"


def _resolved(value: Any) -> Path | None:
    try:
        text = str(value or "").strip()
        return Path(text).expanduser().resolve() if text else None
    except (OSError, RuntimeError, ValueError):
        return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _writable(folder: Path) -> bool:
    """A real write probe, as the Extension does: `os.access` lies on Windows."""

    import tempfile

    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(folder), prefix=".saveprobe_",
                                         delete=True):
            pass
        return True
    except OSError:
        return False


def _destination(payload: dict, *, result_root: Any, extra_roots: Iterable[Any]) -> Path:
    root = _resolved(result_root)
    dest = str(payload.get("dest_dir") or "").strip()
    if dest:
        target = _resolved(dest)
        allowed = [r for r in [root, *(_resolved(x) for x in extra_roots)] if r]
        if target is None or not any(_inside(target, r) for r in allowed):
            raise SaveRefusal(
                "Save folder is outside an allowed location. Link it in the "
                "Gallery first, or set STUDIO_SAVE_ROOTS on the server.", 403)
        if not _writable(target):
            raise SaveRefusal(
                "The configured Save to Gallery folder could not be written to. "
                "Check that it exists and is writable.", 500)
        return target

    if root is None:
        raise SaveRefusal("Studio has no output folder configured.", 503)
    sub = str(payload.get("subfolder") or "").replace("\\", "/").strip("/")
    if sub and not _SUBFOLDER.match(sub):
        raise SaveRefusal("That subfolder name is not allowed.")
    target = root / sub if sub else root
    if not _writable(target):
        raise SaveRefusal("Studio's output folder could not be written to. "
                          "Check its permissions.", 500)
    return target


def configured_save_roots() -> list[str]:
    """Operator-configured extra roots, as the Extension reads them."""

    return [chunk.strip() for chunk in os.environ.get("STUDIO_SAVE_ROOTS", "").split(os.pathsep)
            if chunk.strip()]
