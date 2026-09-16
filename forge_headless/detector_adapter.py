"""Detection for Auto Detail: boxes and masks, adapted rather than inherited.

The functional surface upstream calls `ultralytics_predict`, `PredictOutput`,
`create_mask_from_bbox`, `ensure_pil_image` and `mask.py`'s filtering. That is
the whole of what Studio needs and the whole of what is adapted here.

WHAT IS DELIBERATELY NOT ADAPTED
================================

`get_models`, `download_models` and `hf_download` live in the SAME FILE as
`PredictOutput`, `create_mask_from_bbox` and `ensure_pil_image` -- upstream's
`adetailer/common.py`, which imports `huggingface_hub` at module scope. Simply
importing that module to reach the primitives would acquire the download
machinery, so "do not inherit automatic downloads" can only mean copying the
primitives out. That is why this file exists rather than a thin re-export.

`get_models` is three policies welded together: it scans directories AND
reaches HuggingFace AND injects four MediaPipe entries whether or not
MediaPipe is installed. Studio owns the catalogue instead
(`detector_catalogue.py`), so the policy is one readable rule.

Also not adapted: `apply_classes` (only meaningful for `-world` models, which
Studio does not bundle and which require a download), Gradio callbacks,
extension registration, A1111 command-option assumptions, and font fetching.

`Results.plot()` IS NOT CALLED
==============================

Upstream builds a `preview` image with `pred[0].plot()` and puts it on the
result. Studio must not: `plot()` renders labels with a font ultralytics will
FETCH if it is missing, and drawing an annotated debug image per detection is
work nobody asked for.

That has a consequence upstream would not survive, and it is the one real
adaptation in this file rather than a transcription:

```text
filter_by_ratio      reads pred.preview.size
sort CENTER_TO_EDGE  reads pred.preview.size
```

Both derive the image dimensions FROM THE ARTEFACT WE MUST NOT PRODUCE. With
`plot()` gone, `preview` is None and both raise `AttributeError` on the first
detection they are asked to filter. So `DetectionResult` carries `image_size`
explicitly, taken from the image that was actually passed in, and every
filter reads it from there.

Nothing here imports ultralytics, torch, cv2 or numpy at module scope. The
detector runtime arrives through `ultralytics_boundary`, which is the only
door and configures the offline environment before the first import.

This module is in `forge_headless` rather than `forge_studio` because
`OwnedPackagePurityTests` bans torch, torchvision and numpy from the owned
package at ANY scope, and that ban is aimed at precisely this: a lazy import
still makes the module the torch boundary. Policy stays in
`forge_studio/detector_catalogue.py`, which imports nothing heavier than
hashlib; the runtime lives here, beside `preview_frame.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

#: CPU by default, and that is a product decision rather than a placeholder.
#: Detection is a few megabytes of YOLO over one frame; it runs in well under
#: a second on a CPU, and the alternative is contending for the card that is
#: mid-generation with the image the detection is FOR.
DEFAULT_DEVICE = "cpu"

#: Loaded detectors, by (path, device). Upstream constructs a fresh `YOLO` on
#: every call; measured here, that is ~4.7 s of load against ~0.2 s of actual
#: inference, so a three-slot job would spend fourteen seconds re-reading
#: weights it already had.
#:
#: Bounded, because a detector root the owner fills with twenty files must not
#: become twenty resident models. Eviction is oldest-first rather than LRU:
#: a job uses at most three detectors and re-uses them in the same order every
#: time, so recency and insertion order say the same thing and a plain dict is
#: honest about it.
_DETECTORS: dict[tuple[str, str], Any] = {}
DETECTOR_CACHE_LIMIT = 4


class SortBy(IntEnum):
    NONE = 0
    LEFT_TO_RIGHT = 1
    CENTER_TO_EDGE = 2
    AREA = 3


class MergeInvert(IntEnum):
    NONE = 0
    MERGE = 1
    MERGE_INVERT = 2


@dataclass
class DetectionResult:
    """What one detector found in one image.

    `image_size` is the field upstream does not have. It reads dimensions off
    `preview`, the annotated debug image from `Results.plot()`; Studio does not
    produce one, so the size travels as itself. See the module docstring.
    """

    bboxes: list[list[float]] = field(default_factory=list)
    masks: list[Any] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    #: (width, height) of the image detection ran on.
    image_size: tuple[int, int] = (0, 0)

    def __len__(self) -> int:
        return len(self.bboxes)

    def describe(self) -> dict[str, Any]:
        """The safe projection: counts and confidences, never pixels.

        Metadata and logs get this. It carries no image, no path and no
        prompt, so it is safe wherever a job record goes.
        """

        return {
            "count": len(self.bboxes),
            "confidences": [round(float(c), 4) for c in self.confidences],
            "image_size": list(self.image_size),
        }


# ------------------------------------------------------------------ primitives


def ensure_pil_image(image: Any, mode: str = "RGB") -> Any:
    """Adapted from upstream `common.ensure_pil_image`."""

    from PIL import Image

    if not isinstance(image, Image.Image):
        from torchvision.transforms.functional import to_pil_image

        image = to_pil_image(image)
    if image.mode != mode:
        image = image.convert(mode)
    return image


def create_mask_from_bbox(
    bboxes: list[list[float]], shape: tuple[int, int]
) -> list[Any]:
    """One filled white rectangle per box, on black. Upstream, unchanged.

    `shape` is (width, height), matching PIL rather than numpy. Getting that
    backwards produces a mask of the right area and the wrong orientation,
    which on a square test image looks correct.
    """

    from PIL import Image, ImageDraw

    masks = []
    for bbox in bboxes:
        mask = Image.new("L", shape, 0)
        ImageDraw.Draw(mask).rectangle(bbox, fill=255)
        masks.append(mask)
    return masks


def mask_to_pil(masks: Any, shape: tuple[int, int]) -> list[Any]:
    """Segmentation masks (N, H, W) to PIL, resized to the original image.

    Upstream's version, minus its docstring's assumption that the caller knows
    the tensor is 0/1 rather than 0-255: `.float()` then `to_pil_image` handles
    both, which is what upstream relies on in practice.
    """

    from torchvision.transforms.functional import to_pil_image

    masks = masks.float()
    return [to_pil_image(masks[i], mode="L").resize(shape) for i in range(masks.shape[0])]


# -------------------------------------------------------------------- geometry


def bbox_area(bbox: list[float]) -> float:
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def is_all_black(image: Any) -> bool:
    import numpy as np

    return not np.any(np.asarray(ensure_pil_image(image, "L")))


def dilate_erode(image: Any, value: int) -> Any:
    """Positive dilates, negative erodes, zero is a no-op.

    Upstream reaches for `cv2.getStructuringElement` and `cv2.dilate`. PIL's
    MaxFilter/MinFilter are the same square-kernel morphology on a single
    channel and cost this file an opencv import it otherwise does not need --
    the mask is an 8-bit L image either way, and the kernel is square in both.
    """

    if value == 0:
        return image

    from PIL import ImageFilter

    size = abs(int(value))
    if size % 2 == 0:
        # PIL requires an odd kernel; opencv does not. Rounding UP keeps a
        # dilation from silently doing less than asked.
        size += 1
    filt = ImageFilter.MaxFilter(size) if value > 0 else ImageFilter.MinFilter(size)
    return ensure_pil_image(image, "L").filter(filt)


def offset(image: Any, x: int = 0, y: int = 0) -> Any:
    """Upstream's sign convention: x is right, y is UP."""

    from PIL import ImageChops

    return ImageChops.offset(image, x, -y)


# ------------------------------------------------------------------- filtering
#
# Every one of these reads `result.image_size` where upstream read
# `pred.preview.size`. See the module docstring: preview is the artefact this
# product must not create.


def filter_by_ratio(
    result: DetectionResult, low: float, high: float
) -> DetectionResult:
    if not result.bboxes:
        return result
    width, height = result.image_size
    area = float(width * height)
    if area <= 0:
        return result
    keep = [
        index for index, bbox in enumerate(result.bboxes)
        if low <= bbox_area(bbox) / area <= high
    ]
    return _select(result, keep)


def filter_k_largest(result: DetectionResult, k: int = 0) -> DetectionResult:
    if not result.bboxes or k <= 0:
        return result
    order = sorted(
        range(len(result.bboxes)),
        key=lambda i: bbox_area(result.bboxes[i]),
        reverse=True,
    )
    return _select(result, order[:k])


def filter_k_most_confident(result: DetectionResult, k: int = 0) -> DetectionResult:
    if not result.bboxes or not result.confidences or k <= 0:
        return result
    order = sorted(
        range(len(result.confidences)),
        key=lambda i: result.confidences[i],
        reverse=True,
    )
    return _select(result, order[:k])


def sort_bboxes(result: DetectionResult, order: int | SortBy = SortBy.NONE) -> DetectionResult:
    if order == SortBy.NONE or len(result.bboxes) <= 1:
        return result
    if order == SortBy.LEFT_TO_RIGHT:
        key = lambda i: result.bboxes[i][0]  # noqa: E731
    elif order == SortBy.CENTER_TO_EDGE:
        width, height = result.image_size
        centre = (width / 2, height / 2)

        def key(i: int) -> float:
            bbox = result.bboxes[i]
            middle = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            return ((centre[0] - middle[0]) ** 2 + (centre[1] - middle[1]) ** 2) ** 0.5
    elif order == SortBy.AREA:
        key = lambda i: -bbox_area(result.bboxes[i])  # noqa: E731
    else:
        raise ValueError(f"unknown sort order: {order!r}")
    return _select(result, sorted(range(len(result.bboxes)), key=key))


def _select(result: DetectionResult, indices: list[int]) -> DetectionResult:
    """Reindex every parallel list together.

    One function rather than four copies because these lists are positionally
    joined: dropping a bbox without dropping its mask and its confidence
    produces a result that is internally inconsistent and still looks valid.
    """

    return DetectionResult(
        bboxes=[result.bboxes[i] for i in indices],
        masks=[result.masks[i] for i in indices] if result.masks else [],
        confidences=(
            [result.confidences[i] for i in indices] if result.confidences else []
        ),
        image_size=result.image_size,
    )


def mask_preprocess(
    masks: list[Any],
    *,
    kernel: int = 0,
    x_offset: int = 0,
    y_offset: int = 0,
    merge_invert: int | MergeInvert = MergeInvert.NONE,
) -> list[Any]:
    """Offset, grow/shrink, then optionally merge and invert."""

    if not masks:
        return []
    if x_offset or y_offset:
        masks = [offset(m, x_offset, y_offset) for m in masks]
    if kernel:
        masks = [dilate_erode(m, kernel) for m in masks]
        # A mask eroded out of existence is not a region, and passing an
        # all-black mask to an inpaint is a no-op that reports success.
        masks = [m for m in masks if not is_all_black(m)]
    return mask_merge_invert(masks, merge_invert)


def mask_merge(masks: list[Any]) -> list[Any]:
    from PIL import ImageChops

    if not masks:
        return []
    merged = masks[0]
    for mask in masks[1:]:
        merged = ImageChops.lighter(merged, mask)
    return [merged]


def mask_invert(masks: list[Any]) -> list[Any]:
    from PIL import ImageChops

    return [ImageChops.invert(m) for m in masks]


def mask_merge_invert(masks: list[Any], mode: int | MergeInvert) -> list[Any]:
    if mode == MergeInvert.NONE or not masks:
        return masks
    if mode == MergeInvert.MERGE:
        return mask_merge(masks)
    if mode == MergeInvert.MERGE_INVERT:
        return mask_invert(mask_merge(masks))
    raise ValueError(f"unknown merge/invert mode: {mode!r}")


# ------------------------------------------------------------------ prediction


def load_detector(
    detector_path: Path | str,
    *,
    device: str = DEFAULT_DEVICE,
    app_root: Path | str | None = None,
) -> Any:
    """A resident detector for this path and device. Loads once, then reuses.

    Goes through `ultralytics_boundary.import_yolo`, which is the only door:
    it configures the offline environment BEFORE the first import and verifies
    afterwards that ultralytics agreed.
    """

    from .ultralytics_boundary import import_yolo

    key = (str(Path(detector_path)), str(device))
    cached = _DETECTORS.get(key)
    if cached is not None:
        return cached

    yolo_class = import_yolo(app_root=app_root)
    model = yolo_class(str(detector_path))
    while len(_DETECTORS) >= DETECTOR_CACHE_LIMIT:
        _DETECTORS.pop(next(iter(_DETECTORS)), None)
    _DETECTORS[key] = model
    return model


def release_detectors() -> int:
    """Drop every resident detector. Returns how many were released.

    Detector memory must not be permanently stranded across a job, and on a
    CUDA device the weights are VRAM the generation needs back. Dropping the
    references is the whole release: nothing here owns a context, a handle or
    a file, so there is no close to get wrong.
    """

    count = len(_DETECTORS)
    _DETECTORS.clear()
    return count


def resident_detectors() -> tuple[str, ...]:
    """Which detectors are resident, by NAME. Never a path.

    A diagnostic read that obeys the same rule as every other projection in
    this product: the catalogue offers names, so the report answers in names.
    """

    return tuple(sorted(Path(path).name for path, _device in _DETECTORS))


def detect(
    detector_path: Path | str,
    image: Any,
    *,
    confidence: float = 0.3,
    device: str = DEFAULT_DEVICE,
    app_root: Path | str | None = None,
) -> DetectionResult:
    """Run one detector over one image. Adapted from `ultralytics_predict`.

    Differences from upstream, each deliberate:

    ```text
    no preview          `Results.plot()` is not called. See the module
                        docstring -- it fetches a font and the filters that
                        needed its size now read `image_size`.
    no apply_classes    `-world` models only, which Studio does not bundle
                        and cannot obtain without a download.
    device defaults cpu detection must not contend for the card that is
                        mid-generation with the image being detected in.
    the boundary        the runtime arrives through `ultralytics_boundary`,
                        which configures offline BEFORE the first import.
    a PATH, not a name  callers hold NAMES; the catalogue turns an admitted
                        name into a path and nothing else may.
    ```
    """

    image = ensure_pil_image(image, "RGB")
    size = (image.width, image.height)

    model = load_detector(detector_path, device=device, app_root=app_root)
    prediction = model(image, conf=float(confidence), device=device, verbose=False)
    first = prediction[0]

    bboxes = first.boxes.xyxy.cpu().numpy()
    if bboxes.size == 0:
        # A truthful empty result, not a failure. "No detections" is a
        # successful slot outcome: there was nothing of that kind in the
        # picture, which is a fact about the picture.
        return DetectionResult(image_size=size)

    boxes = [[float(v) for v in row] for row in bboxes.tolist()]
    if first.masks is None:
        masks = create_mask_from_bbox(boxes, size)
    else:
        masks = mask_to_pil(first.masks.data, size)
    confidences = [float(c) for c in first.boxes.conf.cpu().numpy().tolist()]

    return DetectionResult(
        bboxes=boxes, masks=masks, confidences=confidences, image_size=size
    )


__all__ = (
    "DEFAULT_DEVICE",
    "DetectionResult",
    "MergeInvert",
    "SortBy",
    "bbox_area",
    "create_mask_from_bbox",
    "detect",
    "dilate_erode",
    "ensure_pil_image",
    "filter_by_ratio",
    "filter_k_largest",
    "filter_k_most_confident",
    "is_all_black",
    "mask_invert",
    "mask_merge",
    "mask_merge_invert",
    "mask_preprocess",
    "mask_to_pil",
    "offset",
    "sort_bboxes",
)
