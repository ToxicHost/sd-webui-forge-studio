"""Watermarks: the library, and the composite. P11.

Standalone answered `/studio/watermarks` with `[]` and served neither the
export stamp nor the folder button, so the whole Settings > Watermark feature
was inert. This is the Extension's `scripts/studio_watermark.py` (library and
resolution) and `studio_generation.apply_watermark` (the composite), ported:

  * the folder is `<state root>/watermarks/` -- `state_root.STATE_LAYOUT`
    already reserves it -- where the Extension used `<ext root>/watermarks/`;
  * only `.png` and `.webp` are offered (an opaque JPEG is rarely a wanted
    watermark);
  * the page only ever sends a bare file name, resolved here under the folder
    with a traversal guard; no path goes back to the page;
  * `apply_watermark` is the Extension's arithmetic unchanged: scale against
    the shorter edge, never upscaling past the mark's own size or the canvas;
    opacity scales the mark's alpha; rotation expands; nine anchors; margin;
    and it NEVER raises -- a watermark problem returns `(image, False)`.

Source review: Evidence/source-review/P11-watermarks.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge_headless.watermark import ALLOWED_EXTS, POSITIONS  # noqa: E402,F401


def watermarks_dir(state_root: Any, *, create: bool = True) -> Path | None:
    if not state_root:
        return None
    folder = Path(state_root) / "watermarks"
    if create:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return folder


def list_watermarks(state_root: Any) -> list[dict[str, str]]:
    """`[{"name": "<file.png>"}]`, sorted case-insensitively."""

    folder = watermarks_dir(state_root)
    if folder is None:
        return []
    try:
        return [{"name": p.name}
                for p in sorted(folder.iterdir(), key=lambda p: p.name.lower())
                if p.is_file() and p.suffix.lower() in ALLOWED_EXTS]
    except OSError:
        return []


def resolve_watermark_path(state_root: Any, name: Any) -> Path | None:
    """A bare file name under the folder, or None (Extension's guard)."""

    from forge_headless.watermark import resolve_in

    return resolve_in(watermarks_dir(state_root, create=False), name)


def apply_watermark(image: Any, config: Any, state_root: Any) -> tuple[Any, bool]:
    """Composite the configured watermark onto `image`. Never raises.

    The one implementation is `forge_headless.watermark.apply_watermark`,
    shared with the generation writer."""

    from forge_headless.watermark import apply_watermark as composite

    return composite(image, config, watermarks_dir(state_root, create=False))


def stamp_export(payload: Any, state_root: Any) -> dict[str, Any]:
    """The Extension's `/studio/export_watermark` (`studio_api.py:5615-5675`):
    a flattened canvas export in, the stamped PNG data URL out, transparency
    kept where the source had it. `changed: false` tells the page the mark
    could not be applied, so it can say so."""

    import base64
    import io

    from PIL import Image

    body = payload if isinstance(payload, dict) else {}
    data = str(body.get("image_b64") or "")
    name = str(body.get("name") or "").strip()
    if not data:
        raise ValueError("Missing image_b64")
    if not name:
        raise ValueError("No watermark configured")

    def number(key: str, default: Any, cast: Any) -> Any:
        try:
            value = body.get(key)
            return default if value is None else cast(value)
        except (TypeError, ValueError):
            return default

    raw = data.split(",", 1)[1] if "," in data else data
    image = Image.open(io.BytesIO(base64.b64decode(raw)))
    image.load()
    alpha = None
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        alpha = image.convert("RGBA").getchannel("A")
    stamped, changed = apply_watermark(image, {
        "enable": True, "name": name,
        "position": str(body.get("position") or "bottom-right"),
        "opacity": number("opacity", 1.0, float),
        "scale": number("scale", 0.15, float),
        "margin": number("margin", 16, int),
        "rotation": number("rotation", 0.0, float),
    }, state_root)
    if changed and alpha is not None:
        stamped = stamped.convert("RGBA")
        stamped.putalpha(alpha)
    buffer = io.BytesIO()
    stamped.save(buffer, format="PNG")
    return {"ok": True, "changed": bool(changed),
            "image_b64": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")}
