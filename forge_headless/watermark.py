"""The watermark composite, shared by export stamping and the generation writer.

P11. One implementation of the Extension's `studio_generation.apply_watermark`
(`:420-517`), unchanged in its arithmetic, used by:

  * `forge_studio.watermarks` -- Canvas Export's stamp and the library;
  * `live_generation_port._publish` -- the legacy "generation" mode, as the
    last pixel step before the result is written.

It lives in `forge_headless` because the generation writer does, and the
dependency direction is `forge_studio` -> `forge_headless`, never the reverse.

The watermark FOLDER is the state root's `watermarks/`. The writer cannot be
handed a state root -- it is built with a result root only -- so the server
registers the folder once at start-up (`set_watermark_folder`), which is the
same process-wide fact the state root already is. A request only ever names a
bare file; it is resolved here, under that folder, with the Extension's guard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ALLOWED_EXTS = (".png", ".webp")
POSITIONS = (
    "top-left", "top-center", "top-right",
    "center-left", "center", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
)

_FOLDER: list[Path] = []


def set_watermark_folder(folder: Any) -> None:
    """Register the state root's `watermarks/` for the generation writer."""

    _FOLDER[:] = [Path(folder)] if folder else []


def configured_folder() -> Path | None:
    return _FOLDER[0] if _FOLDER else None


def resolve_in(folder: Any, name: Any) -> Path | None:
    """A bare file name under `folder`, or None (the Extension's guard)."""

    raw = str(name or "").strip().replace("\\", "/")
    if not folder or not raw or "/" in raw or raw in (".", ".."):
        return None
    try:
        base = Path(folder).resolve()
        target = (base / raw).resolve()
        target.relative_to(base)
    except (OSError, ValueError):
        return None
    if target.suffix.lower() not in ALLOWED_EXTS or not target.is_file():
        return None
    return target


def apply_watermark(image: Any, config: Any, folder: Any) -> tuple[Any, bool]:
    """Composite the configured watermark onto `image`. Never raises.

    `config` is `{enable, name, position, opacity, scale, margin, rotation}`.
    Returns `(image, changed)`.
    """

    try:
        from PIL import Image

        if not config or not config.get("enable"):
            return image, False
        name = str(config.get("name") or "").strip()
        if not name:
            return image, False
        opacity = float(config.get("opacity", 1.0))
        if opacity <= 0.0:
            return image, False
        path = resolve_in(folder, name)
        if path is None:
            return image, False
        with Image.open(path) as source:
            source.load()
            mark = source.convert("RGBA")

        bw, bh = image.size
        short_edge = min(bw, bh)
        scale = max(0.0, float(config.get("scale", 0.15)))
        target_w = max(1, int(round(short_edge * scale)))
        target_w = min(target_w, mark.width, bw)
        factor = target_w / mark.width
        target_h = max(1, int(round(mark.height * factor)))
        target_h = min(target_h, bh)
        if (target_w, target_h) != mark.size:
            mark = mark.resize((target_w, target_h), Image.LANCZOS)
        if opacity < 1.0:
            mark.putalpha(mark.getchannel("A").point(lambda v: int(v * opacity)))
        rotation = float(config.get("rotation", 0.0))
        if rotation % 360 != 0:
            mark = mark.rotate(rotation, expand=True, resample=Image.BICUBIC)

        ww, wh = mark.size
        margin = int(config.get("margin", 16))
        position = str(config.get("position") or "bottom-right")
        if position not in POSITIONS:
            position = "bottom-right"
        vertical, _, horizontal = position.partition("-")
        x = margin if horizontal == "left" else (
            bw - ww - margin if horizontal == "right" else (bw - ww) // 2)
        y = margin if vertical == "top" else (
            bh - wh - margin if vertical == "bottom" else (bh - wh) // 2)
        base = image.convert("RGBA")
        base.alpha_composite(mark, (max(0, x), max(0, y)))
        return base.convert("RGB"), True
    except Exception:  # noqa: BLE001 - never break a save or a generation
        return image, False
