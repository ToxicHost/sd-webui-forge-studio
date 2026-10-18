"""The heatmap viewer's numbers, from a picture's attention sidecar. HM3.

Here, not in `forge_studio`, because owned presentation code imports no
numpy (`test_device_capability.OwnedPackagePurityTests`). The words and the
lookup are `forge_studio.attention_view`; this module does the arithmetic
that module documents: each content word's summed map (Anima blocks 2-8,
SDXL's captured levels), its strength (p99 over the picture's median content
word), and Anima's "text used here" (1 - the padding share).

Each map leaves here as a PNG of one byte per pixel. For a family in
`LEVELLED` (Anima) it is at `SHARP_LONG_SIDE` pixels on the picture's long
side, cropped to the picture's real extent, and levelled by its own spread
(`levelled`): its median pixel is 0 and its 99th percentile 255. Any other
family's (SDXL's) is its grid over its peak, as viewer v0 sent it. The
capture grid is one cell per 16 pixels, so a
raw map is 16 px blocks; drawn big it is blurry and sits across edges. Where
a picture is given and the family has a setting in `SNAP`, the upsampled map
is edge-snapped to it with the research's guided filter:

- the filter is He, Sun and Tang's (2010/2013), colour form, as
  `anima-attention-2026-10-11/pm_lib.py` `guided_filter` has it;
- the settings are that research's, measured on 1024-pixel pictures
  (FINDINGS 3.3, the blocks 2-8 rows); the radius here scales with the
  picture's long side, so a picture drawn at 512 px takes half the radius.
"""

from __future__ import annotations

import base64
import io
import json
import math
from pathlib import Path
from typing import Any

#: Longest side, in pixels, of every map the viewer receives.
SHARP_LONG_SIDE = 512
#: Pixels of picture per capture-grid cell, both families.
CELL = 16
#: The research's measuring size: a setting's radius is in pixels of a picture
#: whose long side is this many.
REFERENCE_LONG_SIDE = 1024
#: family -> (guide, radius at the reference size, eps). Anima: the largest
#: radius whose median fidelity to the raw map still passes every kind's
#: floor in the research's sweep (objects 0.90, regions 0.90, light 0.95;
#: blocks 2-8, RGB r16 eps 1e-4: 0.970 / 0.930 / 0.951). A family in
#: `LEVELLED` with no entry is only upsampled.
SNAP: dict[str, tuple[str, int, float]] = {"anima": ("rgb", 16, 1e-4)}
#: Families whose maps are upsampled, snapped and levelled here (the owner
#: chose this for Anima, 2026-10-12). Any other family's maps go as viewer v0
#: sent them, each word's grid over its peak at the grid's own size, and the
#: browser draws them as v0 did ("display": "peak"): the owner, testing SDXL
#: on v0, found it clear and kept it.
LEVELLED = frozenset({"anima"})
#: (floor, ceiling) percentiles of a word's own map: below the floor it is
#: not drawn, at the ceiling it is full strength. A fixed floor left one-spot
#: words (a candle's flame) invisible and laid diffuse ones (a room, a city)
#: over the whole picture; levelled by its own spread, every word on the
#: research pictures shows on 8-33% of the picture (`heatmap-sharpen-2026-10-12/
#: probe-adaptive.txt`; the owner, 2026-10-12: "stronger on some of these").
LEVELS = (50.0, 99.0)
#: A picture whose shape differs from the capture's by more than this is not
#: the picture the maps describe (a crop, say); the maps are then not snapped.
ASPECT_TOLERANCE = 0.02


def _png(values: Any) -> str:
    """A uint8 array -> a base64 PNG."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(values).save(buffer, "PNG", compress_level=6)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _to_bytes(values: Any) -> Any:
    """A float map -> uint8, its peak at 255 (an all-zero map stays zero)."""
    import numpy as np

    values = np.asarray(values, dtype=np.float32)
    peak = float(values.max()) if values.size else 0.0
    scaled = values / peak if peak > 0 else values * 0
    return np.clip(scaled * 255 + 0.5, 0, 255).astype(np.uint8)


def _encode(values: Any) -> str:
    return _png(_to_bytes(values))


def unit(map_: Any) -> Any:
    """A float map -> the same map over 0..1 across its own range, as the
    research's `normalise` did (a flat map becomes all zero). Without this a
    word's map keeps whatever floor attention left under it, and the whole
    picture draws tinted."""
    import numpy as np

    map_ = np.asarray(map_, dtype=np.float32)
    low = float(map_.min()) if map_.size else 0.0
    span = float(map_.max()) - low if map_.size else 0.0
    return (map_ - low) / span if span > 1e-12 else map_ * 0


def display_size(width: int, height: int, long_side: int = SHARP_LONG_SIDE) -> tuple[int, int]:
    """The (width, height) every map is drawn at for a picture of this shape."""
    scale = long_side / max(width, height, 1)
    return max(1, round(width * scale)), max(1, round(height * scale))


def upsample(grid: Any, width: int, height: int, out_w: int, out_h: int) -> Any:
    """A capture grid -> a float32 map of `out_h` x `out_w`, bicubic, clipped
    to the grid's own range.

    The grid is whole 16-pixel cells padded at the right and bottom (Neo's
    `pad_to_patch_size`), so for a 1368-pixel side it spans 1376 pixels. It
    is stretched over the cells' full extent, then cropped to the picture's
    own, so a map is not 0.6% out at the far edge. A grid that is not that
    shape is stretched over the whole picture.
    """
    import numpy as np
    from PIL import Image

    grid = np.asarray(grid, dtype=np.float32)
    rows, cols = grid.shape
    if (rows, cols) == (math.ceil(height / CELL), math.ceil(width / CELL)) and width > 0 and height > 0:
        full_w, full_h = round(cols * CELL * out_w / width), round(rows * CELL * out_h / height)
    else:
        full_w, full_h = out_w, out_h
    big = np.asarray(Image.fromarray(grid).resize((max(full_w, out_w), max(full_h, out_h)), Image.Resampling.BICUBIC))
    low, high = float(grid.min()), float(grid.max())
    return np.clip(big[:out_h, :out_w], low, high)


def _box(x: Any, r: int) -> Any:
    """Mean over the (2r+1)-square window at each pixel, edges mirrored: the
    research's own call. OpenCV is a pinned requirement (Neo's `modules/` use
    it); an integral image in numpy is the same arithmetic and eight times
    slower, which on a picture's 25 words is the difference between a
    fraction of a second and two."""
    import cv2

    return cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1), normalize=True, borderType=cv2.BORDER_REFLECT)


class GuidedFilter:
    """The colour guided filter for one picture, with the picture's terms
    computed once and reused for every word's map."""

    def __init__(self, rgb: Any, radius: int, eps: float) -> None:
        import numpy as np

        guide = np.asarray(rgb, dtype=np.float64)
        self.radius = int(radius)
        self.guide = [guide[..., c] for c in range(3)]
        self.mean = [_box(g, self.radius) for g in self.guide]
        sigma = {}
        for i in range(3):
            for j in range(i, 3):
                sigma[i, j] = (_box(self.guide[i] * self.guide[j], self.radius)
                               - self.mean[i] * self.mean[j] + (eps if i == j else 0.0))
        s00, s01, s02, s11, s12, s22 = (sigma[0, 0], sigma[0, 1], sigma[0, 2],
                                        sigma[1, 1], sigma[1, 2], sigma[2, 2])
        self.inverse = (s11 * s22 - s12 * s12, s02 * s12 - s01 * s22, s01 * s12 - s02 * s11,
                        s00 * s22 - s02 * s02, s01 * s02 - s00 * s12, s00 * s11 - s01 * s01)
        self.determinant = s00 * self.inverse[0] + s01 * self.inverse[1] + s02 * self.inverse[2]

    def apply(self, p: Any) -> Any:
        """The filtered map (float64, not clipped) for a map `p` of the
        guide's shape."""
        import numpy as np

        p = np.asarray(p, dtype=np.float64)
        r = self.radius
        mean_p = _box(p, r)
        cov = [_box(self.guide[c] * p, r) - self.mean[c] * mean_p for c in range(3)]
        i00, i01, i02, i11, i12, i22 = self.inverse
        a = [(i00 * cov[0] + i01 * cov[1] + i02 * cov[2]) / self.determinant,
             (i01 * cov[0] + i11 * cov[1] + i12 * cov[2]) / self.determinant,
             (i02 * cov[0] + i12 * cov[1] + i22 * cov[2]) / self.determinant]
        b = mean_p - a[0] * self.mean[0] - a[1] * self.mean[1] - a[2] * self.mean[2]
        return (_box(a[0], r) * self.guide[0] + _box(a[1], r) * self.guide[1]
                + _box(a[2], r) * self.guide[2] + _box(b, r))


def _snapper(picture: Any, header: dict[str, Any], out_w: int, out_h: int,
             setting: tuple[str, int, float]) -> GuidedFilter | None:
    """The filter for this picture, or None when the picture is not the shape
    the capture described (or cannot be used)."""
    import numpy as np
    from PIL import Image

    width, height = int(header.get("width") or 0), int(header.get("height") or 0)
    if picture is None or width <= 0 or height <= 0 or setting[0] != "rgb":
        return None
    pic_w, pic_h = picture.size
    if pic_w <= 0 or pic_h <= 0 or abs((pic_w / pic_h) / (width / height) - 1.0) > ASPECT_TOLERANCE:
        return None
    rgb = np.asarray(picture.convert("RGB").resize((out_w, out_h), Image.Resampling.LANCZOS), dtype=np.float64) / 255.0
    radius = max(1, round(setting[1] * max(out_w, out_h) / REFERENCE_LONG_SIDE))
    return GuidedFilter(rgb, radius, setting[2])


def sharpen(map_: Any, guide: GuidedFilter) -> Any:
    """A float map upsampled to the guide's size -> the same map snapped to
    the picture's edges, in 0..1 with its peak at 1.

    As the research did: the map is scaled to 0..1 over its own range, then
    filtered, then clipped to 0..1.
    """
    import numpy as np

    out = np.clip(guide.apply(unit(map_)), 0.0, 1.0).astype(np.float32)
    peak = float(out.max())
    return out / peak if peak > 0 else out


def levelled(map_: Any, levels: tuple[float, float] = LEVELS) -> Any:
    """A map -> 0..1 between its own `levels` percentiles: the floor's pixels
    and below at 0, the ceiling's and above at 1 (a map with no spread
    between them is all 0).

    A word whose map is one hot spot over a low plateau (a candle's flame)
    keeps its plateau visible; a word spread over the whole picture (a room)
    shows only where it is strongest, so the picture reads through.
    """
    import numpy as np

    map_ = np.asarray(map_, dtype=np.float32)
    low, high = (float(np.percentile(map_, q)) for q in levels) if map_.size else (0.0, 0.0)
    return np.clip((map_ - low) / (high - low), 0.0, 1.0) if high - low > 1e-6 else map_ * 0


def _crop(header: dict[str, Any]) -> tuple[int, int, int, int, int, int] | None:
    """`(x, y, w, h, frame_w, frame_h)` for a capture of Neo's "Only masked"
    crop, or None for a capture of the whole picture.

    That pass samples `crop` of the init picture (`frame`, its own pixels)
    scaled to the generation size (`modules/processing.py:1767-1773`,
    `:1838-1844`), so its maps describe the box alone, and the delivered
    picture is the whole frame with the box pasted back.
    """
    crop, frame = header.get("crop"), header.get("frame")
    try:
        x, y, w, h = (int(v) for v in crop)
        frame_w, frame_h = (int(v) for v in frame)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0 or frame_w <= 0 or frame_h <= 0:
        return None
    return x, y, w, h, frame_w, frame_h


def content_maps(header: dict[str, Any], arrays: dict[str, Any]) -> tuple[str, Any, list[tuple[str, Any, float]]]:
    """`(family, maps, words)` from a sidecar: the family's map stack (Anima
    blocks 2-8, else all blocks; SDXL's levels), or None when it has none,
    and each content word with its summed grid and that grid's p99, in
    prompt order. The viewer's words and, for word masks (M2), the same."""
    import numpy as np

    from forge_studio.attention_view import content_words

    family = str(header.get("family") or "")
    pieces = [str(p) for p in header.get("pieces") or []]
    if family == "anima":
        maps = arrays.get("anima_b2_8")
        if maps is None:
            maps = arrays.get("anima_all")
    else:
        maps = arrays.get("sdxl")
    if maps is None:
        return family, None, []
    maps = np.asarray(maps, dtype=np.float32)
    words = []
    for word, positions in content_words(family, pieces):
        usable = [p for p in positions if p < maps.shape[0]]
        if not usable:
            continue
        summed = maps[usable].sum(axis=0)
        words.append((word, summed, float(np.percentile(summed, 99))))
    return family, maps, words


def strengths(words: list[tuple[str, Any, float]]) -> list[float]:
    """Each word's p99 as a multiple of the picture's median word's (FINDINGS
    4.1/4.4, within the picture), rounded as the viewer sends it."""
    import numpy as np

    median = float(np.median([w[2] for w in words])) if words else 0.0
    return [round(p99 / median, 3) if median > 0 else 0.0 for _word, _summed, p99 in words]


def build(header: dict[str, Any], arrays: dict[str, Any], picture: Any = None,
          snaps: dict[str, tuple[str, int, float]] | None = None,
          levels: tuple[float, float] | None = LEVELS) -> dict[str, Any]:
    """The viewer's answer from a sidecar's header and arrays.

    `picture` is the Gallery picture as a Pillow image (any size), or None;
    `snaps` replaces `SNAP`, for trying a setting; `levels` replaces
    `LEVELS`, and None sends each map scaled over its own range only.
    """
    import numpy as np

    family, maps, words = content_maps(header, arrays)
    if maps is None:
        return {"available": False}
    width, height = int(header.get("width") or 0), int(header.get("height") or 0)
    sharp = family in LEVELLED and width > 0 and height > 0
    crop = _crop(header) if width > 0 and height > 0 else None
    if crop is not None:
        # The maps fill the crop's box inside the whole picture and nothing
        # else: every family, because a grid stretched over the frame would
        # put the crop's words across the whole picture.
        x, y, w, h, frame_w, frame_h = crop
        out_w, out_h = display_size(frame_w, frame_h)
        sx, sy = out_w / frame_w, out_h / frame_h
        box = (round(x * sx), round(y * sy), max(1, round(w * sx)), max(1, round(h * sy)))
        map_w, map_h = box[2], box[3]
        region = None
        if picture is not None:
            pic_w, pic_h = picture.size
            region = picture.crop((round(x * pic_w / frame_w), round(y * pic_h / frame_h),
                                   round((x + w) * pic_w / frame_w), round((y + h) * pic_h / frame_h)))
    else:
        box = None
        out_w, out_h = display_size(width, height) if sharp else (int(maps.shape[2]), int(maps.shape[1]))
        map_w, map_h = out_w, out_h
        region = picture
    setting = (SNAP if snaps is None else snaps).get(family) if sharp else None
    guide = _snapper(region, header, map_w, map_h, setting) if setting else None

    def placed(values: Any) -> Any:
        """A map of the box -> the whole frame, zero outside the box."""
        if box is None:
            return values
        frame = np.zeros((out_h, out_w), dtype=np.float32)
        bx, by = box[0], box[1]
        part = values[:max(0, out_h - by), :max(0, out_w - bx)]
        frame[by:by + part.shape[0], bx:bx + part.shape[1]] = part
        return frame

    def drawn(grid: Any) -> str:
        if not sharp:
            if box is None:
                return _encode(grid)                # viewer v0's map: the grid over its peak
            # v0's map, placed: the grid over its peak, stretched over the box.
            return _encode(placed(upsample(grid, width, height, map_w, map_h)))
        up = upsample(grid, width, height, map_w, map_h)
        shaped = sharpen(up, guide) if guide is not None else unit(up)
        # Levelled over the box alone: the empty frame around it is not part
        # of the word's spread.
        return _encode(placed(shaped if levels is None else levelled(shaped, levels)))

    reply: dict[str, Any] = {
        "available": True, "family": family,
        "grid": [int(maps.shape[1]), int(maps.shape[2])], "size": [out_w, out_h],
        "display": "levelled" if sharp else "peak", "snapped": guide is not None,
        "width": width, "height": height,
        "prompt": str(header.get("prompt") or ""),
        "words": [{"word": word, "strength": strength, "map": drawn(summed)}
                  for (word, summed, _p99), strength in zip(words, strengths(words))],
    }
    if crop is not None:
        reply["crop"], reply["frame"] = list(crop[:4]), list(crop[4:])
    pad = arrays.get("anima_pad")
    if family == "anima" and pad is not None:
        # "Text used" is a coarse map: upsampled, never snapped; inside the
        # crop's box when there is one.
        used = upsample(1.0 - np.asarray(pad, dtype=np.float32), width, height, map_w, map_h)
        reply["text_used"] = _encode(placed(used))
    return reply


def read(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """A sidecar's `(header, arrays)`, without pickles (the format
    `attention_capture.write_sidecar` writes)."""
    import numpy as np

    with np.load(Path(path), allow_pickle=False) as data:
        header = json.loads(str(data["header"]))
        arrays = {name: data[name] for name in data.files if name != "header"}
    return header, arrays


def view(path: Path, picture: Any = None) -> dict[str, Any]:
    """`build(*read(path), picture)`, or `{"available": False}` for an
    unreadable file."""
    try:
        return build(*read(path), picture)
    except (OSError, ValueError, KeyError):
        return {"available": False}


__all__ = ("GuidedFilter", "build", "content_maps", "display_size", "levelled", "read", "sharpen", "strengths",
           "unit", "upsample", "view")
