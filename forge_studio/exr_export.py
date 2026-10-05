"""EXR export: the output panel's "Export EXR (Standard)". P12.

`app.js` `_exportEXR` posts `/studio/export/exr`, which Standalone did not
serve, so the menu item always failed. This is the Extension's handler
(`scripts/studio_api.py:3692-3821`) on its "image_b64" path: the 8-bit output
as float32 RGB in [0, 1], written as a single-part scanline OpenEXR.

Divergences, recorded in `Evidence/source-review/P12-exr-export.md`:

  * `float_path` and `mask_path` are never read. They are filesystem paths
    from the page, and Standalone writes no `.float32.bin` sidecars (the High
    Precision control was removed in AR5), so a page cannot legitimately hold
    one. With `image_b64` present the export proceeds, as the Extension's own
    fallback does when the sidecar is missing.
  * The writer is always the Extension's `_write_exr_minimal` (uncompressed
    float32). Its preferred cv2 path needs `OPENCV_IO_ENABLE_OPENEXR` set
    before cv2 is first imported, which Neo has already done by the time
    Studio runs; the pixels are identical either way.
  * The folder is P1's: `<result root>/downloads/` by default. The reply
    carries the file name, never the path. A file is never overwritten.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from forge_headless.exr import encode_exr, image_to_rgb_float  # noqa: F401 - re-exported

from .output_save import SavedImage, SaveRefusal, _decode, _destination

#: As `output_save`: a bound turns a pathological folder into an error.
_MAX_SUFFIX = 10_000


def export_exr(payload: Any, *, result_root: Any) -> SavedImage:
    """Write one EXR and say what was written. Raises `SaveRefusal`."""

    if not isinstance(payload, dict):
        raise SaveRefusal("The export request could not be read.")
    if not str(payload.get("image_b64") or "").strip():
        raise SaveRefusal("There is no image to export.")
    image = _decode(payload.get("image_b64"))
    rgb = image_to_rgb_float(image)

    folder = _destination({"subfolder": str(payload.get("subfolder") or "downloads")},
                          result_root=result_root, extra_roots=())
    data = encode_exr(rgb)
    stem = _stem(payload.get("filename"))
    for counter in range(_MAX_SUFFIX):
        candidate = folder / (f"{stem}.exr" if counter == 0 else f"{stem}-{counter}.exr")
        try:
            with open(candidate, "xb") as handle:
                handle.write(data)
        except FileExistsError:
            continue
        except OSError as error:
            raise SaveRefusal(f"The EXR could not be written: {error.strerror or error}.",
                              500) from None
        return SavedImage(filename=candidate.name, path=candidate, media_type="image/x-exr")
    raise SaveRefusal("No free file name was found in that folder.", 500)


def _stem(value: Any) -> str:
    """The Extension's rule: a cleaned stem, `export` if nothing survives,
    `Studio-<time>` when none was sent."""

    raw = str(value or "").strip()
    if not raw:
        return f"Studio-{int(time.time())}"
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
    dot = raw.rfind(".")
    raw = raw[:dot] if dot > 0 else raw
    return re.sub(r"[^\w.\-]+", "_", raw) or "export"
