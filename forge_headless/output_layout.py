"""Where a finished picture is written, and what it is called.

OF1. Studio wrote every result flat into its result root as
`<request id>.png`, and the owner's Save folder setting reached nothing. This
is the Extension's save structure (`studio_api.py:3059-3155`, the default the
owner chose) and Neo's own (`modules/shared_options.py:106-108,125,128`,
`modules/images.py:672-677`), computed in one place so the generation writer
and the Upscale writer cannot disagree. The review, with every divergence, is
`Evidence/source-review/OF1-output-folder-and-save-structure.md`.

```text
layout "studio" (default)   <base>/<create|edit|upscaled>/<YYYY-MM-DD>/
layout "neo"                <base>/<txt2img|img2img>-images/<YYYY-MM-DD>/
                            <base>/extras-images/            (Upscale)
base                        the owner's trusted folder, or
                            <result root>/studio  (studio layout)
                            <result root>         (neo layout)
name                        Studio-<NNNNN>-<seed>.<ext>
```

Pure: no Forge, no Neo, no Torch. The folder this returns has already been
vetted by Studio's trust policy (`forge_studio/save_roots.py`); this module
only arranges it.
"""

from __future__ import annotations

import datetime as _datetime
from pathlib import Path
from typing import Any

#: The two layouts. Anything else is treated as the default.
LAYOUTS = ("studio", "neo")

#: A result made by the Upscale panel, rather than by a generation.
PURPOSE_UPSCALED = "upscaled"

#: How far the counter probe walks past a taken name before giving up. The
#: Extension loops without a bound; a bound turns a pathological folder into
#: an error rather than a hang.
MAX_NAME_ATTEMPTS = 10_000

_NAME_PREFIX = "Studio-"


def mode_folder(operation: str, purpose: str = "") -> str:
    """The Extension's mode folder: `create`, `edit` or `upscaled`.

    The Extension's page sends `mode: "Edit"` exactly when a mask is present
    (`frontend/app.js:2428`), so img2img without a mask is `create` and
    inpaint is `edit` (`studio_api.py:3060`).
    """

    if purpose == PURPOSE_UPSCALED:
        return "upscaled"
    return "edit" if operation == "inpaint" else "create"


def neo_folder(operation: str, purpose: str = "") -> str:
    """Neo's per-mode leaf (`shared_options.py:106-108`)."""

    if purpose == PURPOSE_UPSCALED:
        return "extras-images"
    return "txt2img-images" if operation == "txt2img" else "img2img-images"


def output_folder(
    result_root: Path | str,
    *,
    save_dir: str = "",
    layout: str = "studio",
    operation: str = "txt2img",
    purpose: str = "",
    today: _datetime.date | None = None,
) -> Path:
    """The folder one result is written into.

    `save_dir` is an absolute folder Studio has ALREADY trusted, or "" for the
    default. A relative one is refused rather than resolved against whatever
    the working directory happens to be.
    """

    custom = str(save_dir or "").strip()
    if custom and not Path(custom).is_absolute():
        raise ValueError("A save folder must be an absolute path.")
    date = (today or _datetime.date.today()).strftime("%Y-%m-%d")
    root = Path(result_root)
    if layout == "neo":
        base = Path(custom) if custom else root
        leaf = base / neo_folder(operation, purpose)
        # Neo's Extras tab saves with `no_prompt=True`, which turns the date
        # subfolder off (`images.py:672-673`, `postprocessing.py:103`).
        return leaf if purpose == PURPOSE_UPSCALED else leaf / date
    base = Path(custom) if custom else root / "studio"
    return base / mode_folder(operation, purpose) / date


def next_counter(folder: Path | str) -> int:
    """The Extension's `_next_forge_counter` (`studio_api.py:676-697`).

    The highest `Studio-NNNNN-` already in the folder, plus one; 1 for an
    empty or missing folder. Neo's own `00000-...` names never match, so the
    two numberings cannot collide in a shared Neo folder.
    """

    path = Path(folder)
    highest = 0
    try:
        names = [entry.name for entry in path.iterdir() if entry.is_file()]
    except OSError:
        return 1
    for name in names:
        if (len(name) >= 13 and name.startswith(_NAME_PREFIX)
                and name[7:12].isdigit() and name[12] == "-"):
            highest = max(highest, int(name[7:12]))
    return highest + 1


def result_name(counter: int, seed: Any, extension: str) -> str:
    """`Studio-00001-<seed>.<ext>`, the Extension's name (`:3220`)."""

    try:
        seed_text = str(int(seed))
    except (TypeError, ValueError):
        seed_text = "0"
    if seed_text.startswith("-"):
        # An unresolved -1 is not a seed; the Extension writes 0 for it.
        seed_text = "0"
    return f"{_NAME_PREFIX}{int(counter):05d}-{seed_text}.{extension}"


def write_numbered(folder: Path, *, seed: Any, extension: str, write) -> Path:
    """Write one result under the next free `Studio-NNNNN-<seed>` name.

    `write(path)` must create the file EXCLUSIVELY and raise `FileExistsError`
    when the name is gone; the next number is then tried, so two writers
    racing for a name both succeed and neither overwrites the other.
    """

    folder.mkdir(parents=True, exist_ok=True)
    counter = next_counter(folder)
    for attempt in range(MAX_NAME_ATTEMPTS):
        candidate = folder / result_name(counter + attempt, seed, extension)
        if candidate.exists():
            continue
        try:
            write(candidate)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError("No free result name was found in the save folder.")


__all__ = (
    "LAYOUTS",
    "MAX_NAME_ATTEMPTS",
    "PURPOSE_UPSCALED",
    "mode_folder",
    "neo_folder",
    "next_counter",
    "output_folder",
    "result_name",
    "write_numbered",
)
