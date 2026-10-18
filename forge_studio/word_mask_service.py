"""`POST /studio/masks/word`: word masks for a picture on the Canvas. M2.

`Evidence/NAMED-MASKS-SPEC.md` section 6. The page names the picture by its
`content_hash` (the Gallery's pixel hash, recorded on the layer it was placed
on) and, once the owner has chosen one, a word:

- `{"content_hash"}` -> the picture's words and strengths, or
  `{"available": false, "reason": ...}` saying why there are none;
- `{"content_hash", "progress": true}` -> how far the picture's slow part
  (begun by the words request, in the background) has got;
- `{"content_hash", "word"}` or `{"content_hash", "words": [...]}` -> the
  three previews (Object, Area / light, Everything else) as PNGs at the
  picture's size; several words build one mask (M2b).

The capture is found as the Gallery viewer finds it (`attention_view`): the
sidecar named on the picture's metadata row, else the one its hash names.
The picture is the Gallery's file for that hash. A picture the Gallery has no
file for (outputs not saved) may be sent by the page as `image_b64`, and is
used only when its own pixel hash is the one asked about: an edited layer is
not the picture the capture describes.

The arithmetic and MobileSAM are `forge_headless.word_masks`: owned
presentation code imports no numerical library.
"""

from __future__ import annotations

import base64
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from .attention_store import relative_for, resolve

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
#: Longest word the page may name (a prompt word, not a sentence).
MAX_WORD = 64
#: Most words one mask may be built from.
MAX_WORDS = 16

#: Why a picture has no word masks, for the page to say.
REASONS = {
    "no-capture": "This picture has no word data: it was made before heatmaps, with recording off, "
                  "or it is not a Studio picture.",
    "no-picture": "The picture this layer came from is not in the Gallery.",
    "no-gallery": "Word data needs the Gallery, which is not available here.",
    "unreadable": "This picture's word data could not be read.",
    "mismatch": "The picture is not the shape its word data describes.",
    "no-words": "This picture's word data has no words.",
    "no-word": "That word is not in this picture's prompt.",
    "no-model": "MobileSAM is not installed, so word masks cannot be made.",
}


class WordMaskRefusal(ValueError):
    """A request the route refuses (400)."""


def _unavailable(code: str, **extra: Any) -> dict[str, Any]:
    return {"available": False, "reason": code, "message": REASONS.get(code, code), **extra}


def _shape(payload: dict) -> tuple[int, tuple[float, float] | None]:
    """S4: a part step (-8..8) and a clicked point (picture pixels), both
    optional."""
    part = payload.get("part", 0)
    if part is None:
        part = 0
    if isinstance(part, bool) or not isinstance(part, int) or not -8 <= part <= 8:
        raise WordMaskRefusal("part must be a whole number from -8 to 8.")
    point = payload.get("point")
    if point is not None:
        if (not isinstance(point, list) or len(point) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1e6 for v in point)):
            raise WordMaskRefusal("point must be [x, y] in the picture's pixels.")
        point = (float(point[0]), float(point[1]))
    return part, point


def _parse(payload: Any) -> tuple[str, list[str], str]:
    if not isinstance(payload, dict):
        raise WordMaskRefusal("The request must be a JSON object.")
    digest = payload.get("content_hash")
    if not isinstance(digest, str) or not _DIGEST.match(digest.strip().lower()):
        raise WordMaskRefusal("content_hash must be a picture's 64-character hash.")
    # One word ("word") or several building one mask ("words", M2b).
    asked = payload.get("words")
    if asked is None:
        single = payload.get("word") or ""
        asked = [single] if single else []
    if not isinstance(asked, list) or len(asked) > MAX_WORDS \
            or any(not isinstance(w, str) or not w.strip() or len(w) > MAX_WORD for w in asked):
        raise WordMaskRefusal(f"words must be up to {MAX_WORDS} short strings.")
    image = payload.get("image_b64", "")
    if image is not None and not isinstance(image, str):
        raise WordMaskRefusal("image_b64 must be a string.")
    return digest.strip().lower(), [w.strip().lower() for w in asked], image or ""


def _sidecar(store: Any, store_root: Path, digest: str) -> Path | None:
    with store.read() as connection:
        named = connection.execute(
            "SELECT attention_path FROM image_metadata WHERE content_hash = ? AND content_hash != ''",
            (digest,)).fetchone()
    relative = str(named[0] or "") if named else ""
    for candidate in (relative, relative_for(digest) or ""):
        path = resolve(store_root, candidate) if candidate else None
        if path is not None and path.is_file():
            return path
    return None


def _gallery_picture(store: Any, digest: str) -> Any:
    with store.read() as connection:
        paths = [str(row[0] or "") for row in connection.execute(
            "SELECT filepath FROM images WHERE content_hash = ? ORDER BY id", (digest,))]
    for filepath in paths:
        if not filepath:
            continue
        try:
            from PIL import Image

            picture = Image.open(filepath)
            picture.load()
            return picture
        except (ImportError, OSError, ValueError):
            continue
    return None


def _uploaded_picture(image_b64: str, digest: str) -> Any:
    """The page's copy of the picture, only if it IS the picture."""
    from .gallery_index import content_hash

    text = image_b64.strip()
    if text.startswith("data:"):
        text = text.partition(",")[2]
    try:
        data = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise WordMaskRefusal("image_b64 is not valid base64.") from None
    if content_hash(data) != digest:
        return None
    from PIL import Image

    picture = Image.open(BytesIO(data))
    picture.load()
    return picture


def handle(payload: Any, *, store: Any | None, store_root: Path | None,
           app_root: Path | str | None = None,
           engine: Callable[[], Any] | None = None) -> dict[str, Any]:
    """The route's answer. Raises `WordMaskRefusal` for a malformed request.

    `engine` returns the arithmetic module (`forge_headless.word_masks`);
    tests pass their own.
    """
    digest, words_, image_b64 = _parse(payload)
    part, point = _shape(payload)
    if engine is None:
        def engine() -> Any:
            from forge_headless import word_masks

            return word_masks
    # How far the picture's preparation has got (the page's loading bar):
    # nothing is read for it.
    if payload.get("progress") is True:
        return engine().progress(digest)
    if store is None or store_root is None:
        return _unavailable("no-gallery")
    sidecar = _sidecar(store, Path(store_root), digest)
    if sidecar is None:
        return _unavailable("no-capture")
    picture = _gallery_picture(store, digest)
    if picture is None and image_b64:
        picture = _uploaded_picture(image_b64, digest)
    if picture is None:
        # The page may send its copy of the picture (an unsaved output).
        return _unavailable("no-picture", need_picture=True)
    module = engine()
    try:
        if not words_:
            return module.words(digest, sidecar, picture, app_root=app_root)
        if part or point is not None:
            return module.previews(digest, sidecar, picture, words_, app_root=app_root, part=part, point=point)
        return module.previews(digest, sidecar, picture, words_, app_root=app_root)
    except module.WordMaskError as error:
        return _unavailable(error.code)
    finally:
        picture.close()


__all__ = ("MAX_WORD", "MAX_WORDS", "REASONS", "WordMaskRefusal", "handle")
