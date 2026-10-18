"""Word masks from a picture's heatmap capture. M2.

`Evidence/NAMED-MASKS-SPEC.md` section 6, decided "yes to all" by the owner
(2026-10-15). For one word of a picture, three previews the owner chooses
between by sight, each a mask at the picture's size:

- **Object** (crisp edge): MobileSAM seeded from the word's map, the
  research's variant A -- the peak of the map after the object guided filter,
  plus the box of that map's >= 0.4 region (`anima-attention-2026-10-11/
  s3_pipeline.py`). The size switch (FINDINGS 3.6, `s3_smallsam.py`): when
  A's mask is more than twice the attention's own extent (the area where the
  map, blurred with sigma 12 px, is at least half its maximum), the smallest
  of SAM's three candidates for the blurred map's peak alone that contains
  that peak. Measured: "woman" 0.932 IoU with the person mask; flame and
  neon signs right where A took the container. The research switched object
  words only; here it switches only when A covers at most
  `SUBJECT_MAX_SHARE` of the picture and the part is smaller than A.
- **Area / light** (soft): the word's map as the Gallery viewer draws it
  (`attention_maps`): upsampled, snapped to the picture with the colour
  guided filter, levelled by its own spread.
- **Everything else**: 1 - the union of the picture's object words' A masks
  (FINDINGS 3.6, inv-union-SAM: 0.993 against the white-background proxy).
  Studio has no word kinds (the research's were the owner's labels), so an
  "object word" here is any other word of the picture whose A mask covers at
  most `SUBJECT_MAX_SHARE` of it: a thing, not the scene. The research's SAM
  took 70-100 % of the picture for every scene word ("room", "background",
  "forest", "dusk"; `s3-pipeline.txt`) and at most 29 % for its objects.

Everything is computed on the picture at `WORK_LONG_SIDE` pixels on its long
side, the size the research measured at, so every radius and sigma is the
research's own; the masks are then resized to the picture. A capture of an
"Only masked" inpaint (M0: `crop` and `frame` in the header) describes its
box alone: every mask is made inside the box and is empty outside it.

MobileSAM runs on the CPU through `ultralytics_boundary.import_sam`. One
model per process; the image embedding is computed once per picture (about
0.6 s) and kept for the last `CACHE_PICTURES` pictures, with each word's
masks, so a second word costs only its prompts.

numpy, OpenCV and torch live here, not in `forge_studio`
(`test_device_capability.OwnedPackagePurityTests`).
"""

from __future__ import annotations

import base64
import io
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .attention_maps import (
    REFERENCE_LONG_SIDE,
    GuidedFilter,
    _box,
    _crop,
    content_maps,
    display_size,
    levelled,
    sharpen,
    strengths,
    unit,
    upsample,
)

#: The research's measuring size; radii and sigmas below are in its pixels.
WORK_LONG_SIDE = REFERENCE_LONG_SIDE
#: The object seed's guided filter per family, (guide, radius, eps).
#: Anima: early blocks, objects (`s3-gf-best.json`, "early"/"object"; blocks
#: 2-8 picked the same). SDXL: round 2's object setting (`s1-best.json`).
OBJECT_SEED: dict[str, tuple[str, int, float]] = {
    "anima": ("lum", 64, 1e-4),
    "sdxl": ("rgb", 32, 1e-3),
}
#: The Area / light preview's filter: Anima's is the viewer's (`SNAP`); SDXL's
#: is round 2's region setting (the viewer leaves SDXL unsnapped).
AREA: dict[str, tuple[str, int, float]] = {
    "anima": ("rgb", 16, 1e-4),
    "sdxl": ("rgb", 32, 1e-3),
}
#: The seed box is the filtered map's region at or above this (round 2's 0.4).
SEED_THRESHOLD = 0.4
#: The size switch's blur, in work pixels, and its extent level and ratio.
PEAK_SIGMA = 12.0
EXTENT_LEVEL = 0.5
SWITCH_RATIO = 2.0
#: "Everything else": a word whose A mask covers more than this share of the
#: picture names the scene, not a thing in it.
SUBJECT_MAX_SHARE = 1.0 / 3.0
#: Where the bundled weights live (`packaging/assets.json`).
MODEL_RELATIVE = Path("models") / "sam" / "mobile_sam.pt"
#: Pictures whose embedding (and word masks) stay in memory.
CACHE_PICTURES = 3

METHODS = {
    "object-part": "SAM, another of its shapes for the word",
    "object-click": "SAM, the shape at the point clicked",
    "object": "SAM seeded from the word's map",
    "object-small": "SAM, the smallest part at the word's peak",
    "area": "the word's map, snapped to the picture",
    "everything": "everything but the picture's objects",
}


class WordMaskError(RuntimeError):
    """A word mask cannot be made; `code` says why, for the page."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ maps ----

def _grey_guided(guide: Any, p: Any, r: int, eps: float) -> Any:
    """He, Sun and Tang's grey guided filter (`pm_lib.guided_filter`, the
    scalar form), float64."""
    import numpy as np

    guide = np.asarray(guide, dtype=np.float64)
    p = np.asarray(p, dtype=np.float64)
    mean_i, mean_p = _box(guide, r), _box(p, r)
    a = (_box(guide * p, r) - mean_i * mean_p) / (_box(guide * guide, r) - mean_i * mean_i + eps)
    b = mean_p - a * mean_i
    return _box(a, r) * guide + _box(b, r)


def _filtered(rgb: Any, map_: Any, setting: tuple[str, int, float], scale: float) -> Any:
    """`pm_lib.filtered`: the map through the guided filter, clipped to 0..1
    (not renormalised: the seed threshold is the research's on this)."""
    import numpy as np

    guide_kind, radius, eps = setting
    r = max(1, round(radius * scale))
    if guide_kind == "lum":
        lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
        out = _grey_guided(lum, map_, r, eps)
    else:
        out = GuidedFilter(rgb, r, eps).apply(map_)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _bbox(mask: Any) -> list[int] | None:
    import numpy as np

    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if xs.size else None


def _argmax(map_: Any) -> tuple[int, int]:
    import numpy as np

    y, x = np.unravel_index(int(np.argmax(map_)), map_.shape)
    return int(x), int(y)


def _png(values: Any) -> str:
    """A uint8 greyscale array -> base64 PNG."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(values, mode="L").save(buffer, "PNG", compress_level=6)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class _Picture:
    """One picture's working state: its capture, the picture at the working
    size, the region the capture describes, and SAM's embedding of it."""

    def __init__(self, header: dict[str, Any], arrays: dict[str, Any], picture: Any) -> None:
        import numpy as np
        from PIL import Image

        self.family, maps, self.words = content_maps(header, arrays)
        if maps is None or not self.words:
            raise WordMaskError("no-words", "This picture's capture has no words.")
        self.width, self.height = int(header.get("width") or 0), int(header.get("height") or 0)
        if self.width <= 0 or self.height <= 0:
            raise WordMaskError("unreadable", "This picture's capture has no size.")
        self.strengths = strengths(self.words)
        self.grids = {word: summed for word, summed, _p99 in self.words}
        self.picture_size = picture.size
        pic_w, pic_h = picture.size
        crop = _crop(header)
        if crop is None and abs((pic_w / pic_h) / (self.width / self.height) - 1.0) > 0.02:
            raise WordMaskError("mismatch", "The picture is not the shape its capture describes.")
        self.work_w, self.work_h = display_size(pic_w, pic_h, WORK_LONG_SIDE)
        self.scale = max(self.work_w, self.work_h) / REFERENCE_LONG_SIDE
        work = picture.convert("RGB")
        if work.size != (self.work_w, self.work_h):
            work = work.resize((self.work_w, self.work_h), Image.Resampling.LANCZOS)
        self.rgb8 = np.asarray(work, dtype=np.uint8)
        if crop is None:
            self.region = (0, 0, self.work_w, self.work_h)
            self.crop = None
        else:
            x, y, w, h, frame_w, frame_h = crop
            sx, sy = self.work_w / frame_w, self.work_h / frame_h
            rx, ry = max(0, round(x * sx)), max(0, round(y * sy))
            rw = max(1, min(self.work_w - rx, round(w * sx)))
            rh = max(1, min(self.work_h - ry, round(h * sy)))
            self.region = (rx, ry, rw, rh)
            self.crop = {"crop": [x, y, w, h], "frame": [frame_w, frame_h]}
        rx, ry, rw, rh = self.region
        self.region_rgb = self.rgb8[ry:ry + rh, rx:rx + rw].astype(np.float64) / 255.0
        self.predictor = None
        self.objects: dict[str, dict[str, Any]] = {}
        #: The background preparation's progress (M2b, `prepare`): steps done
        #: of the total (the embedding, then one per word), or an error.
        self.progress: dict[str, Any] = {"done": 0, "total": 1 + len(self.grids), "stage": "waiting"}
        self.preparing: threading.Thread | None = None

    # -- the word's maps, in region pixels --------------------------------
    def raw(self, word: str) -> Any:
        rx, ry, rw, rh = self.region
        return unit(upsample(self.grids[word], self.width, self.height, rw, rh))

    def placed(self, region_values: Any) -> Any:
        """Region values -> the whole working frame, zero outside it."""
        import numpy as np

        rx, ry, rw, rh = self.region
        frame = np.zeros((self.work_h, self.work_w), dtype=np.float32)
        frame[ry:ry + rh, rx:rx + rw] = region_values
        return frame

    def in_region(self, mask: Any) -> Any:
        """A whole-frame mask cut to the region (no data outside the box)."""
        rx, ry, rw, rh = self.region
        return self.placed(mask[ry:ry + rh, rx:rx + rw])

    def share(self, mask: Any) -> float:
        rx, ry, rw, rh = self.region
        return float(mask[ry:ry + rh, rx:rx + rw].mean())


# ------------------------------------------------------------------- SAM ----

#: SAM and each picture's masks: one at a time (torch on the CPU).
_LOCK = threading.RLock()
#: The picture cache and the start-once flag: never held across SAM work, so
#: a request is not kept waiting behind another picture's embedding.
_CACHE_LOCK = threading.Lock()
_MODEL: dict[str, Any] = {}
_PICTURES: "OrderedDict[str, _Picture]" = OrderedDict()


def model_path(app_root: Path | str | None = None) -> Path:
    base = Path(app_root) if app_root is not None else Path(__file__).resolve().parent.parent
    return base / MODEL_RELATIVE


def _new_predictor(app_root: Path | str | None) -> Any:
    """A SAM predictor on the CPU sharing the process's one model. Refuses
    a missing weights file: ultralytics would download it."""
    from . import ultralytics_boundary

    path = model_path(app_root)
    if not path.is_file():
        raise WordMaskError("no-model", "MobileSAM's weights are not installed (models/sam/mobile_sam.pt).")
    predictor_class = ultralytics_boundary.import_sam(app_root=app_root)
    predictor = predictor_class(overrides=dict(conf=0.0, task="segment", mode="predict", imgsz=1024,
                                               model=str(path), device="cpu", verbose=False, save=False))
    shared = _MODEL.get(str(path))
    predictor.setup_model(model=shared, verbose=False)
    _MODEL[str(path)] = predictor.model
    return predictor


def _sam(state: _Picture, app_root: Path | str | None) -> Any:
    if state.predictor is None:
        predictor = _new_predictor(app_root)
        predictor.set_image(state.rgb8[..., ::-1].copy())   # ultralytics reads BGR, as cv2 does
        state.predictor = predictor
    return state.predictor


def _candidates(predictor: Any, **prompt: Any) -> list[Any]:
    """SAM's masks for a prompt, as bool arrays at the working size."""
    result = predictor(**prompt)[0]
    if result.masks is None or len(result.masks.data) == 0:
        return []
    return [m for m in (result.masks.data.cpu().numpy() > 0.5)]


def _object(state: _Picture, word: str, app_root: Path | str | None) -> dict[str, Any]:
    """Variant A, and the size switch's choice, for one word (cached)."""
    import cv2
    import numpy as np

    found = state.objects.get(word)
    if found is not None:
        return found
    rx, ry, rw, rh = state.region
    raw = state.raw(word)
    seed = _filtered(state.region_rgb, raw, OBJECT_SEED.get(state.family, OBJECT_SEED["sdxl"]), state.scale)
    px, py = _argmax(seed)
    box = _bbox(seed >= SEED_THRESHOLD) or [0, 0, rw - 1, rh - 1]
    peak = [px + rx, py + ry]
    box = [box[0] + rx, box[1] + ry, box[2] + rx, box[3] + ry]
    predictor = _sam(state, app_root)
    masks = _candidates(predictor, bboxes=[box], points=[[peak]], labels=[[1]])
    a = state.in_region(masks[0].astype(np.float32)) if masks else state.placed(np.zeros((rh, rw), np.float32))
    blurred = cv2.GaussianBlur(raw, (0, 0), PEAK_SIGMA * state.scale)
    extent = float((blurred >= EXTENT_LEVEL * float(blurred.max())).mean()) if blurred.max() > 0 else 0.0
    chosen, method = a, "object"
    # The research ran the switch on object words only; without kinds, A must
    # be thing-sized (a scene word's A is most of the picture) and the part
    # must be smaller than A (a point alone can also find something larger).
    if extent > 0 and SWITCH_RATIO * extent < state.share(a) <= SUBJECT_MAX_SHARE:
        sx, sy = _argmax(blurred)
        small = [state.in_region(m.astype(np.float32))
                 for m in _candidates(predictor, points=[[[sx + rx, sy + ry]]], labels=[[1]],
                                      multimask_output=True)]
        inside = [m for m in small if m[sy + ry, sx + rx] > 0 and m.sum() < a.sum()]
        if inside:
            chosen, method = min(inside, key=lambda m: float(m.sum())), "object-small"
    found = {"a": a, "mask": chosen, "method": method, "extent": extent, "peak": peak, "box": box}
    state.objects[word] = found
    return found


# ---------------------------------------------------------------- parts ----
# S4 (NAMED-MASKS-DEFERRED-SPEC.md, the owner: "following your rec"): the
# owner steps through SAM's own candidates -- whole, part, subpart -- by
# sight. Nothing picks a part automatically: the research showed that breaks
# whole objects (FINDINGS 3.6, P-small: "woman" 1.2 %).
#: Two candidates this alike are one shape.
PART_SAME_IOU = 0.95


def _scored(predictor: Any, **prompt: Any) -> list[tuple[Any, float]]:
    """SAM's masks for a prompt with its own predicted quality each."""
    result = predictor(**prompt)[0]
    if result.masks is None or len(result.masks.data) == 0:
        return []
    masks = result.masks.data.cpu().numpy() > 0.5
    scores = result.boxes.conf.cpu().numpy() if result.boxes is not None else [0.0] * len(masks)
    return [(m, float(s)) for m, s in zip(masks, scores)]


def _ordered(state: _Picture, candidates: list[Any]) -> list[Any]:
    """Distinct, non-empty shapes (inside the region), largest first."""
    import numpy as np

    kept: list[Any] = []
    for mask in sorted((state.in_region(np.asarray(c, dtype=np.float32)) for c in candidates),
                       key=lambda m: -float(m.sum())):
        if mask.sum() <= 0:
            continue
        on = mask > 0.5
        if any(float((on & (k > 0.5)).sum()) / max(1.0, float((on | (k > 0.5)).sum())) >= PART_SAME_IOU
               for k in kept):
            continue
        kept.append(mask)
    return kept


def _index_of(parts: list[Any], mask: Any) -> int:
    on = mask > 0.5
    best, index = -1.0, 0
    for i, k in enumerate(parts):
        union = float((on | (k > 0.5)).sum())
        iou = float((on & (k > 0.5)).sum()) / union if union else 0.0
        if iou > best:
            best, index = iou, i
    return index


def _word_parts(state: _Picture, word: str, app_root: Path | str | None) -> tuple[list[Any], int]:
    """A word's shapes, largest first, and where its Object mask sits."""
    found = _object(state, word, app_root)
    if "parts" not in found:
        predictor = _sam(state, app_root)
        candidates = [found["mask"], found["a"]]
        candidates += [m for m, _s in _scored(predictor, bboxes=[found["box"]], points=[[found["peak"]]],
                                              labels=[[1]], multimask_output=True)]
        candidates += [m for m, _s in _scored(predictor, points=[[found["peak"]]], labels=[[1]],
                                              multimask_output=True)]
        found["parts"] = _ordered(state, candidates)
        found["part_default"] = _index_of(found["parts"], found["mask"])
    return found["parts"], found["part_default"]


def _point_parts(state: _Picture, point: tuple[int, int], app_root: Path | str | None) -> tuple[list[Any], int]:
    """The shapes at a clicked point (work pixels), largest first, and SAM's
    own best of them."""
    predictor = _sam(state, app_root)
    x, y = point
    scored = [(m, s) for m, s in _scored(predictor, points=[[[x, y]]], labels=[[1]], multimask_output=True)
              if m[y, x]]
    if not scored:
        return [], 0
    parts = _ordered(state, [m for m, _s in scored])
    best = max(scored, key=lambda t: t[1])[0]
    return parts, _index_of(parts, best)


# ---------------------------------------------------------------- public ----

def _state(digest: str, sidecar: Path, picture: Any) -> _Picture:
    with _CACHE_LOCK:
        state = _PICTURES.get(digest)
        if state is not None:
            _PICTURES.move_to_end(digest)
            return state
    from .attention_maps import read

    try:
        header, arrays = read(sidecar)
    except (OSError, ValueError, KeyError):
        raise WordMaskError("unreadable", "This picture's word data could not be read.") from None
    state = _Picture(header, arrays, picture)
    with _CACHE_LOCK:
        # Two first requests for one picture: the first one built keeps it.
        if digest in _PICTURES:
            return _PICTURES[digest]
        _PICTURES[digest] = state
        while len(_PICTURES) > CACHE_PICTURES:
            _PICTURES.popitem(last=False)
    return state


def _prepare(state: _Picture, app_root: Path | str | None) -> None:
    """The picture's slow part, ahead of the owner's first word: SAM's
    embedding, then every word's Object mask (Everything else needs them
    all). The lock is taken per step, so a word asked for meanwhile is served
    between steps."""
    total = 1 + len(state.grids)
    try:
        state.progress = {"done": 0, "total": total, "stage": "reading"}
        with _LOCK:
            _sam(state, app_root)
        for done, word in enumerate(state.grids, start=1):
            state.progress = {"done": done, "total": total, "stage": "words"}
            with _LOCK:
                _object(state, word, app_root)
        state.progress = {"done": total, "total": total, "stage": "ready"}
    except WordMaskError as error:
        state.progress = {"done": 0, "total": total, "stage": "error", "reason": error.code}
    except Exception:  # noqa: BLE001 - a failed preparation is reported, the route retries per word
        state.progress = {"done": 0, "total": total, "stage": "error", "reason": "failed"}


def prepare(state: _Picture, app_root: Path | str | None = None) -> None:
    """Start `_prepare` in the background, once per picture."""
    with _CACHE_LOCK:
        if state.preparing is not None:
            return
        state.preparing = threading.Thread(target=_prepare, args=(state, app_root),
                                           name="word-masks-prepare", daemon=True)
    state.preparing.start()


def progress(digest: str) -> dict[str, Any]:
    """How far the picture's preparation has got, for the page's bar."""
    state = _PICTURES.get(digest)
    if state is None:
        return {"available": False, "reason": "not-started"}
    return {"available": True, **state.progress}


def words(digest: str, sidecar: Path, picture: Any, app_root: Path | str | None = None) -> dict[str, Any]:
    """The picture's words and strengths (the Gallery's Words panel's), for
    the page's list; the slow part starts in the background (M2b, the
    owner's ask: show the first word's wait)."""
    state = _state(digest, sidecar, picture)
    prepare(state, app_root)
    reply = {"available": True, "family": state.family,
             "width": state.picture_size[0], "height": state.picture_size[1],
             "words": [{"word": word, "strength": strength}
                       for (word, _summed, _p99), strength in zip(state.words, state.strengths)]}
    reply["progress"] = dict(state.progress)
    if state.crop:
        reply.update(state.crop)
    return reply


def _chosen(state: _Picture, words_: Any) -> list[str]:
    """The asked-for words, lower-cased, in the order asked, each once."""
    asked = [words_] if isinstance(words_, str) else list(words_ or [])
    chosen = []
    for word in asked:
        word = str(word or "").strip().lower()
        if word and word not in chosen:
            chosen.append(word)
    if not chosen or any(word not in state.grids for word in chosen):
        raise WordMaskError("no-word", "That word is not in this picture's prompt.")
    return chosen


def _masks(state: _Picture, chosen: list[str], app_root: Path | str | None) -> dict[str, Any]:
    """The three masks (float, working size) for one or more words, M2b (the
    owner, 2026-10-15: several words build one mask): Object is the union of
    the words' Object masks, Area the largest of their maps, and Everything
    else is 1 - the union of the picture's OTHER things (the research's
    inverse: the chosen words name what is left, as "room" or "background"
    do)."""
    import numpy as np

    with _LOCK:
        found = {word: _object(state, word, app_root) for word in chosen}
        subject_words, union = [], np.zeros((state.work_h, state.work_w), dtype=np.float32)
        for other in state.grids:
            if other in chosen:
                continue
            a = _object(state, other, app_root)["a"]
            if state.share(a) <= SUBJECT_MAX_SHARE:
                subject_words.append(other)
                union = np.maximum(union, a)
    rx, ry, rw, rh = state.region
    area_setting = AREA.get(state.family, AREA["sdxl"])
    guide = GuidedFilter(state.region_rgb, max(1, round(area_setting[1] * state.scale)), area_setting[2])
    area = np.zeros((state.work_h, state.work_w), dtype=np.float32)
    objects = np.zeros((state.work_h, state.work_w), dtype=np.float32)
    for word in chosen:
        mapped = levelled(sharpen(upsample(state.grids[word], state.width, state.height, rw, rh), guide))
        area = np.maximum(area, state.placed(mapped))
        objects = np.maximum(objects, found[word]["mask"])
    return {"object": objects, "area": area, "everything": state.in_region(1.0 - union),
            "methods": {word: found[word]["method"] for word in chosen}, "subject": subject_words}


def previews(digest: str, sidecar: Path, picture: Any, words_: Any,
             app_root: Path | str | None = None, part: int = 0,
             point: tuple[float, float] | None = None) -> dict[str, Any]:
    """The three previews for one word or several (`words_`, a word or a
    list), as PNGs at the picture's size.

    S4: `point` (picture pixels) makes Object the shape SAM finds there; `part`
    steps from the default shape to smaller (+) or larger (-) candidates --
    the word's own (one word chosen) or the point's."""
    import numpy as np
    from PIL import Image

    state = _state(digest, sidecar, picture)
    chosen = _chosen(state, words_)
    masks = _masks(state, chosen, app_root)
    pic_w, pic_h = state.picture_size
    parts_info = None
    with _LOCK:
        parts, default, method = [], 0, None
        if point is not None:
            px = int(round(point[0] * state.work_w / pic_w))
            py = int(round(point[1] * state.work_h / pic_h))
            rx, ry, rw, rh = state.region
            if rx <= px < rx + rw and ry <= py < ry + rh:
                parts, default = _point_parts(state, (px, py), app_root)
                method = "object-click"
        elif len(chosen) == 1:
            parts, default = _word_parts(state, chosen[0], app_root)
            method = "object-part" if part else None
        if parts:
            index = max(0, min(len(parts) - 1, default + int(part)))
            parts_info = {"count": len(parts), "index": index, "default": default}
            if method is not None:
                masks["object"] = parts[index]
                masks["methods"] = {word: method for word in chosen}

    def encoded(values: Any) -> tuple[str, float]:
        bytes_ = np.clip(np.asarray(values, dtype=np.float32) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if (pic_w, pic_h) != (state.work_w, state.work_h):
            bytes_ = np.asarray(Image.fromarray(bytes_, mode="L").resize((pic_w, pic_h),
                                                                         Image.Resampling.BILINEAR))
        return _png(bytes_), round(float(bytes_.mean()) / 255.0 * 100.0, 2)

    methods = set(masks["methods"].values())
    object_method = methods.pop() if len(methods) == 1 else "object"
    reply: dict[str, Any] = {"available": True, "word": " + ".join(chosen), "words": chosen,
                             "family": state.family, "width": pic_w, "height": pic_h, "previews": {}}
    for key, method in (("object", object_method), ("area", "area"), ("everything", "everything")):
        png, coverage = encoded(masks[key])
        reply["previews"][key] = {"png": png, "method": method, "label": METHODS[method], "coverage": coverage}
    reply["previews"]["object"]["methods"] = masks["methods"]
    if parts_info is not None:
        reply["previews"]["object"]["parts"] = parts_info
    reply["previews"]["everything"]["subject"] = masks["subject"]
    if state.crop:
        reply.update(state.crop)
    return reply


def object_mask(digest: str, sidecar: Path, picture: Any, words_: Any,
                app_root: Path | str | None = None) -> tuple[Any, list[str], list[str]]:
    """Word Detail (S8b): the Object mask alone for one or more words, as a
    binary L image at the picture's size, for Auto Detail -- `previews`
    without the Area and Everything-else views (the latter runs SAM for every
    word in the prompt). Returns (mask or None, the words used, the words the
    capture does not have). A word the capture lacks is reported, never
    guessed; with none left there is no mask."""
    import numpy as np
    from PIL import Image

    state = _state(digest, sidecar, picture)
    asked = [words_] if isinstance(words_, str) else list(words_ or [])
    wanted: list[str] = []
    for word in asked:
        word = str(word or "").strip().lower()
        if word and word not in wanted:
            wanted.append(word)
    used = [word for word in wanted if word in state.grids]
    missing = [word for word in wanted if word not in state.grids]
    if not used:
        return None, used, missing
    with _LOCK:
        union = np.zeros((state.work_h, state.work_w), dtype=np.float32)
        for word in used:
            union = np.maximum(union, _object(state, word, app_root)["mask"])
    bytes_ = np.clip(union * 255.0 + 0.5, 0, 255).astype(np.uint8)
    image = Image.fromarray(bytes_, mode="L")
    if image.size != state.picture_size:
        image = image.resize(state.picture_size, Image.Resampling.BILINEAR)
    # Binary, as a detector's segmentation mask is: Auto Detail's own
    # dilate / erode and mask blur shape the edge from here.
    return image.point(lambda value: 255 if value >= 128 else 0), used, missing


def masks_for_test(digest: str, sidecar: Path, picture: Any, words_: Any,
                   app_root: Path | str | None = None) -> dict[str, Any]:
    """The previews' float masks at the working size, for measuring against
    the research's references. Not used by the route."""
    state = _state(digest, sidecar, picture)
    chosen = _chosen(state, words_)
    masks = _masks(state, chosen, app_root)
    first = state.objects[chosen[0]]
    return {"object": masks["object"], "a": first["a"], "method": first["method"],
            "everything": masks["everything"], "area": masks["area"], "size": (state.work_w, state.work_h)}


def forget() -> None:
    """Drop every cached picture (tests; a model swap would not need it)."""
    with _CACHE_LOCK:
        _PICTURES.clear()


__all__ = ("AREA", "METHODS", "MODEL_RELATIVE", "OBJECT_SEED", "SUBJECT_MAX_SHARE", "WordMaskError",
           "forget", "masks_for_test", "model_path", "prepare", "previews", "progress", "words")
