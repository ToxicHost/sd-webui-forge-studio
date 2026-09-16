"""One decoded live-preview frame, from the latent Neo is already publishing.

Studio has shown a Live Preview toggle in Settings for its whole existence and
never delivered a frame. Three separate things were true at once:

```text
preview_enabled=False        hardcoded at both HeadlessProgress construction
                             sites, and `preview_enabled` sat in
                             BACKEND_DEFAULTED_FIELDS -- "Studio's request
                             cannot express this" -- while the page shipped
                             the control
"preview": None              hardcoded on every /studio/ws progress message
current_latent               written by Neo into the state bridge on every
                             sampler step, stored in a dict, and discarded
```

So the channel existed, the client already consumed `data.preview`, and the
pixels were arriving. Only the decode was missing. This module is the decode.

`approximation=2` -- the RGB cheap approximation -- is passed EXPLICITLY, and
that choice is load-bearing rather than a default worth accepting:

```text
0 Full     a real VAE decode, per frame, contending with the sampler for the
           card the sampler is using
1 Approx NN  needs `sd_vae_approx`'s weights file
3 TAESD    needs the TAESD decoder, which Neo DOWNLOADS on first use
2 RGB      a linear transform of the latent using the model's own
           `latent_format`. No file, no network, no second model resident
```

Only option 2 satisfies "no runtime download, no surprise fetch" while a job
is mid-flight. Leaving `approximation` as None would have read
`opts.show_progress_type`, which can be TAESD, so the option that avoids the
download has to be named rather than defaulted into.

Nothing here is imported at module scope. Like `neo_registries`, this refuses
to be the thing that drags Neo or Torch into a Studio process: it checks that
the engine is ALREADY standing and returns None when it is not.
"""

from __future__ import annotations

import base64
import io
import sys
from typing import Any

#: Longest edge of a delivered frame, in pixels.
#:
#: A SERVER constant, deliberately not a request field. The client sends
#: `max_edge` on its legacy `preview_config` message and this ignores it: a
#: preview costs GPU time on the card that is mid-generation, and letting a
#: request name the cost would make a page able to slow down the image it is
#: waiting for.
PREVIEW_MAX_EDGE = 480

#: JPEG, not PNG. A 480px preview frame is roughly 25 kB as JPEG and 400 kB as
#: PNG, and it is redrawn several times a second over a socket that is also
#: carrying progress. Nothing is ever saved from it -- the RESULT is PNG and
#: goes through the normal publication path -- so lossy is free here.
PREVIEW_JPEG_QUALITY = 80

_DATA_URL_PREFIX = "data:image/jpeg;base64,"


def encode_preview(latent: Any, *, max_edge: int = PREVIEW_MAX_EDGE) -> str | None:
    """A data URL for one preview frame, or None if one cannot be made.

    Returns None rather than raising, in every failure mode. This runs on the
    SAMPLING thread, between two steps of the owner's actual image: a preview
    that can fail a generation is worse than no preview at all, which is the
    same reasoning that makes `read_registries` answer EMPTY instead of
    raising.
    """

    if latent is None:
        return None

    # The `neo_registries` discipline, for the same reason. Without this the
    # function performs a real `from modules import ...` in any process that
    # reaches it, importing Neo into one that had deliberately avoided it --
    # and wrapping the call in `except` would make that look harmless, because
    # nothing would raise and the import would still have happened.
    if "modules.sd_samplers_common" not in sys.modules:
        return None

    try:
        import torch
        from PIL import Image

        from modules.sd_samplers_common import single_sample_to_image
    except Exception:  # noqa: BLE001 - a preview is never worth a failed job
        return None

    try:
        with torch.inference_mode():
            # Index 0 of the batch, which is what Neo's own `sample_to_image`
            # does. Studio submits `batch_size=1`, so this is the image; on a
            # batch it would be the first, and showing one frame of many is
            # what the control has always meant.
            sample = latent[0] if getattr(latent, "ndim", 0) >= 4 else latent
            image = single_sample_to_image(sample, approximation=2)
    except Exception:  # noqa: BLE001 - see above
        return None

    try:
        if max(image.size) > max_edge:
            scale = max_edge / float(max(image.size))
            image = image.resize(
                (max(1, round(image.width * scale)),
                 max(1, round(image.height * scale))),
                Image.BILINEAR,
            )
        buffer = io.BytesIO()
        image.convert("RGB").save(
            buffer, format="JPEG", quality=PREVIEW_JPEG_QUALITY
        )
    except Exception:  # noqa: BLE001 - see above
        return None

    return _DATA_URL_PREFIX + base64.b64encode(buffer.getvalue()).decode("ascii")


__all__ = (
    "PREVIEW_JPEG_QUALITY",
    "PREVIEW_MAX_EDGE",
    "encode_preview",
)
