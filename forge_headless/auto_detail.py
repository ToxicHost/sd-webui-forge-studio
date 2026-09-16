"""Studio-owned Auto Detail: detect, mask, detail -- up to three times, in order.

The canonical pipeline the book fixes is

```text
BASE -> optional HIRES -> AD1 -> AD2 -> AD3 -> PUBLISH
```

as ONE public job, one queue item, one cancellation surface. This module owns
the middle of that line, and only the middle: slot order, detection filtering,
mask construction, stage reporting, cancellation boundaries and the safe
facts a result may record about what happened.

WHAT THIS MODULE DELIBERATELY DOES NOT OWN
==========================================

The two things that need Neo are INJECTED rather than imported:

```text
detect(detector_path, image, confidence)  -> DetectionResult
detail(image, mask, slot)                 -> image
```

`detail` is the Neo-compatible inpaint pass, and it stays in
`live_generation_port` where the engine and the base `StableDiffusionProcessing`
already are. Keeping it out of here is not tidiness: it means every rule below
-- three slots in order, a disabled slot costing nothing, no detections being a
SUCCESS, a cancelled slot stopping the ones after it -- is provable without a
GPU, a model, or torch. The rules are the part that can be silently wrong.

That split is also what keeps this file importable under `-I -S -B`. Nothing
here imports Pillow, numpy or torch at module scope; the filters it uses from
`detector_adapter` are pure list arithmetic, and only `mask_preprocess` with a
non-zero kernel reaches Pillow, at call time.

THE RULES, STATED
=================

```text
order            slots run 1, 2, 3. A slot always sees what the slot before
                 it produced, never the original frame.
disabled         a true no-op. No detector is resolved, nothing is loaded, no
                 pass runs, and no outcome is recorded -- which is what keeps
                 "three slots" from costing three detector loads for an owner
                 using one.
no detections    a truthful SUCCESSFUL outcome. Not a failure, not a fake
                 inpaint. The pipeline continues. There was nothing of that
                 kind in the picture, which is a fact about the picture.
no regions       same, and it can happen with detections present: a mask
                 eroded out of existence is not a region, and handing an
                 all-black mask to an inpaint is a no-op that reports success.
cancellation     checked before each slot AND between regions within a slot,
                 so a three-slot job cancels in bounded time rather than
                 after the pass it is in.
publication      nothing here publishes. Intermediate AD passes are not
                 results; only `_publish` may produce one.
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .contracts import HeadlessError

#: The pipeline is three slots because the BOOK fixes it at three, not because
#: three was a convenient number. A fourth would change the pipeline contract.
MAX_SLOTS = 3

DETECTOR_UNRESOLVED = "GENERATION_ADETAILER_DETECTOR_UNRESOLVED"
CANCELLED = "GENERATION_CANCELLED"


@dataclass(frozen=True)
class SlotSpec:
    """One Auto Detail pass, already resolved and validated.

    `detector` is the catalogue NAME and is the only one of the two that may
    reach a log, a metadata record or a console line. `detector_path` is the
    resolved filesystem path and exists solely because `detector_adapter.detect`
    requires one -- its own docstring is explicit that callers hold names and
    that only the catalogue may turn an admitted name into a path.

    Resolution and admission both happen BEFORE this object exists. An
    unresolved path reaching here is a programming error, and is refused rather
    than allowed to fail later inside a detector load that has already cost the
    base pass.
    """

    index: int
    enabled: bool = False
    detector: str = ""
    detector_path: str = ""
    confidence: float = 0.3
    top_k: int = 0
    min_ratio: float = 0.0
    max_ratio: float = 1.0
    dilate_erode: int = 4
    mask_blur: int = 6
    denoising_strength: float = 0.30
    prompt: str = ""
    negative_prompt: str = ""
    inpaint_padding: int = 32
    steps: int = 0
    cfg: float = 0.0


@dataclass(frozen=True)
class SlotOutcome:
    """What one slot actually did. Safe to record anywhere a job record goes.

    Counts and a name. No pixels, no path, no prompt -- the same discipline as
    `DetectionResult.describe`, and for the same reason: this travels into
    result metadata and the console.

    `candidates` and `regions` are deliberately BOTH kept. They answer different
    questions and their difference is the only evidence the owner's filtering
    settings did anything: "the detector found four faces and your ratio filter
    kept one" is actionable, and a single number cannot say it.
    """

    index: int
    detector: str
    candidates: int
    regions: int
    detailed: bool

    def describe(self) -> dict[str, Any]:
        return {
            "slot": self.index,
            "detector": self.detector,
            "candidates": self.candidates,
            "regions": self.regions,
            "detailed": self.detailed,
        }


def _never_cancelled() -> bool:
    return False


def run_auto_detail(
    images: Sequence[Any],
    slots: Sequence[SlotSpec],
    *,
    detect: Callable[..., Any],
    detail: Callable[[Any, Any, SlotSpec], Any],
    on_slot_start: Callable[[SlotSpec], None] | None = None,
    cancelled: Callable[[], bool] = _never_cancelled,
) -> tuple[list[Any], tuple[SlotOutcome, ...]]:
    """Run the enabled slots over `images`, in order. Returns the new images.

    The images are replaced, not accumulated: slot 2 details what slot 1
    produced. That is what makes the three slots a pipeline rather than three
    independent edits of the same frame.
    """

    from .detector_adapter import (
        SortBy,
        filter_by_ratio,
        filter_k_largest,
        mask_preprocess,
        sort_bboxes,
    )

    current = list(images)
    outcomes: list[SlotOutcome] = []

    for slot in sorted(slots, key=lambda item: item.index):
        if not slot.enabled:
            # A true no-op, structurally. Nothing is resolved, nothing is
            # loaded, and no outcome is recorded -- an outcome for a slot that
            # did not run would put a detector name in the metadata of a
            # result it never touched.
            continue
        if not slot.detector_path:
            raise HeadlessError(
                DETECTOR_UNRESOLVED,
                f"Auto Detail slot {slot.index} names a detector that was "
                "not resolved to an admitted file.",
            )
        _check_cancelled(cancelled)
        if on_slot_start is not None:
            on_slot_start(slot)

        candidates = 0
        regions = 0
        detailed = False
        for position, image in enumerate(current):
            found = detect(
                slot.detector_path, image, confidence=slot.confidence
            )
            candidates += len(found)

            found = filter_by_ratio(found, slot.min_ratio, slot.max_ratio)
            # Sorted BEFORE the k-filter, matching upstream: "keep the largest
            # two" is meaningless unless the order is defined first.
            found = sort_bboxes(found, SortBy.AREA)
            found = filter_k_largest(found, slot.top_k)

            masks = mask_preprocess(
                list(found.masks), kernel=slot.dilate_erode
            )
            regions += len(masks)
            for mask in masks:
                _check_cancelled(cancelled)
                image = detail(image, mask, slot)
                detailed = True
            current[position] = image

        outcomes.append(
            SlotOutcome(
                index=slot.index,
                detector=slot.detector,
                candidates=candidates,
                regions=regions,
                detailed=detailed,
            )
        )

    return current, tuple(outcomes)


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise HeadlessError(
            CANCELLED, "The job was cancelled during Auto Detail."
        )


def describe_outcomes(outcomes: Sequence[SlotOutcome]) -> dict[str, Any]:
    """The result-metadata section, or nothing at all.

    Absent when no slot ran, exactly as `_hires_metadata` is absent when no
    second pass ran. Presence is then the answer to "was this detailed?", and
    a base-only result stays byte-identical to a pre-P0.8 one.
    """

    if not outcomes:
        return {}
    return {
        "auto_detail": {
            "slots": [outcome.describe() for outcome in outcomes],
        }
    }


__all__ = (
    "CANCELLED",
    "DETECTOR_UNRESOLVED",
    "MAX_SLOTS",
    "SlotOutcome",
    "SlotSpec",
    "describe_outcomes",
    "run_auto_detail",
)
