"""Resolve randomized dimensions BEFORE the job is admitted. WP1.5.

Ported from the Studio Extension's `scripts/studio_ar.py` at
`4.10.0-public-beta` / `b316a4d8`, read in full and recorded in
`Evidence/source-review/WP1.5-base-controls.md`. The composition rule below is
the Extension's, not an invention.

WHY THIS RUNS AT ADMISSION AND THE EXTENSION'S RUNS AT GENERATION

`studio_generation.py:3250` calls `randomize_dimensions` inside the per-image
loop, immediately before building the processing object, and mutates
`gp.width/height`. That is correct for the Extension: its generate route is one
synchronous call that produces the image it was asked for.

Studio queues. A job that rolled its dimensions at execution would have no
reproducible record of what it chose -- the recipe would say "randomize" and the
result would say 1024x576, with nothing tying them together, and re-running the
recipe would produce a different size. So the rolls happen here, at submission,
and the concrete answers are frozen onto the request. Owner decision, recorded
in the WP1.5 handoff.

WHAT MOVES EARLIER IS *WHEN*, NOT *HOW MANY*. Upstream rolls once per image
inside its batch loop, and that is preserved exactly: `resolve_series` draws a
separate roll for every image in the submission and chains them the way
upstream's `gp.width/height` write-back does. Resolving once and reusing the
answer across a batch would have turned a randomized batch into N copies of one
shape -- a behaviour change smuggled in under a timing decision. Owner
correction, applied here.

TXT2IMG ONLY, which is the Extension's rule and easy to miss. The call site is
guarded by `if is_txt2img:`. An img2img or inpaint job takes its geometry from
the source image, and rolling a random shape for one would resize the owner's
picture to something nobody asked for.

NO CEILING. `_round8` has a floor of 8 and no upper bound, exactly as upstream.
Studio imposes no maximum width, height or pixel count -- binding product
policy. This module must never grow one.

THE SEED IS NOT INVOLVED. The chooser is injected and defaults to
`random.choice`; nothing here reads, writes or derives from the diffusion seed,
so a fixed-seed job stays reproducible and a re-roll of dimensions cannot
perturb sampling.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, Sequence

#: Canonical sets, matching the Extension's and the frontend's.
ALL_BASES: tuple[int, ...] = (512, 640, 768, 896, 1024)
ALL_RATIOS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "1:1"),
    (5, 4, "5:4"),
    (4, 3, "4:3"),
    (3, 2, "3:2"),
    (16, 9, "16:9"),
    (2, 1, "2:1"),
    (239, 100, "2.39:1"),
)

_RATIO_MAP = {label: (a, b) for a, b, label in ALL_RATIOS}

#: Below this the ratio is treated as square and orientation does not apply.
#: The Extension's own epsilon (`studio_ar.py:129`, `ratio_val > 1.001`).
_SQUARE_EPSILON = 1.001


class AspectRefused(ValueError):
    """A randomizer input Studio will not guess at. Carries a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ResolvedAspect:
    """The concrete answer, plus how it was reached.

    Both halves are kept because the recipe needs both: `width`/`height` are
    what executes, and the rest is what the owner asked for. A recipe carrying
    only the result cannot be re-read as "this was a random roll", and one
    carrying only the request cannot reproduce the image.
    """

    width: int
    height: int
    base: int
    ratio_label: str
    orientation: str
    randomized: bool


def _parse_ratio(label: Any) -> tuple[int, int]:
    """One ratio label into integers, or a named refusal.

    DIVERGENCE, deliberate. The Extension returns None for an unparseable label
    and `_resolve_ratio_pool` then drops it silently, falling back to the full
    set if nothing survives. Studio refuses by name instead: a pool the owner
    chose being quietly replaced with every ratio is precisely the
    silently-ignored field this product forbids, and the owner would see
    dimensions they never selected with nothing to explain it.
    """

    if not isinstance(label, str) or not label.strip():
        raise AspectRefused("ASPECT_RATIO_INVALID",
                            "A ratio label must be a non-empty string.")
    label = label.strip()
    if label in _RATIO_MAP:
        return _RATIO_MAP[label]
    parts = label.split(":")
    if len(parts) != 2:
        raise AspectRefused("ASPECT_RATIO_INVALID",
                            f"{label!r} is not a ratio like 16:9.")
    try:
        a, b = float(parts[0]), float(parts[1])
    except ValueError:
        raise AspectRefused("ASPECT_RATIO_INVALID",
                            f"{label!r} is not a ratio like 16:9.") from None
    if a <= 0 or b <= 0:
        raise AspectRefused("ASPECT_RATIO_INVALID",
                            f"{label!r} has a side that is not positive.")
    # The Extension's own trick for decimal labels such as `2.39:1`: scale both
    # sides by 100 so the pair stays integral.
    if a != int(a) or b != int(b):
        a, b = round(a * 100), round(b * 100)
    return int(a), int(b)


def _resolve_base_pool(pool: Any) -> tuple[int, ...]:
    """Empty means ALL -- the Extension's rule, kept.

    An entry outside the canonical set is refused rather than dropped, for the
    same reason `_parse_ratio` refuses.
    """

    if pool is None:
        return ALL_BASES
    if not isinstance(pool, (list, tuple)):
        raise AspectRefused("ASPECT_BASE_INVALID",
                            "base_pool must be a list of sizes.")
    if not pool:
        return ALL_BASES
    resolved = []
    for value in pool:
        if isinstance(value, bool) or not isinstance(value, int):
            raise AspectRefused("ASPECT_BASE_INVALID",
                                f"{value!r} is not a base size.")
        if value not in ALL_BASES:
            raise AspectRefused(
                "ASPECT_BASE_INVALID",
                f"{value} is not one of the base sizes "
                f"{', '.join(str(b) for b in ALL_BASES)}.")
        resolved.append(value)
    return tuple(resolved)


def _resolve_ratio_pool(pool: Any) -> tuple[tuple[int, int, str], ...]:
    """Empty means ALL. Every entry must parse."""

    if pool is None:
        return ALL_RATIOS
    if not isinstance(pool, (list, tuple)):
        raise AspectRefused("ASPECT_RATIO_INVALID",
                            "ratio_pool must be a list of labels.")
    if not pool:
        return ALL_RATIOS
    resolved = []
    for label in pool:
        a, b = _parse_ratio(label)
        resolved.append((a, b, str(label).strip()))
    return tuple(resolved)


def round8(value: float) -> int:
    """Nearest multiple of 8, floored at 8. The Extension's `_round8`.

    A FLOOR and no ceiling, exactly as upstream. This is dimension arithmetic
    for the VAE's alignment, not a size policy, and it must never acquire an
    upper bound -- Studio imposes no maximum resolution.
    """

    return max(8, round(value / 8) * 8)


def resolve(width: int, height: int, *,
            randomize_base: bool = False,
            randomize_ratio: bool = False,
            randomize_orientation: bool = False,
            base_pool: Any = None,
            ratio_pool: Any = None,
            chooser: Callable[[Sequence[Any]], Any] = random.choice
            ) -> ResolvedAspect:
        # noqa: D401
    """Resolve one set of dimensions. `chooser` is injected so tests are exact.

    Mirrors `studio_ar.randomize_dimensions` (:94-157) step for step: derive the
    current state, pick or keep the base, pick or keep the ratio, pick or keep
    the orientation, then compose short and long sides through `round8`.
    """

    if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
        raise AspectRefused("ASPECT_DIMENSION_INVALID",
                            "width must be a positive whole number.")
    if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
        raise AspectRefused("ASPECT_DIMENSION_INVALID",
                            "height must be a positive whole number.")

    active = bool(randomize_base or randomize_ratio or randomize_orientation)
    # Pools are validated even when inactive, so a malformed selection is
    # reported when the owner made it rather than the first time they happen to
    # switch randomization on.
    bases = _resolve_base_pool(base_pool)
    ratios = _resolve_ratio_pool(ratio_pool)

    if not active:
        # Untouched. A fixed-dimension request must survive WP1.5 byte for
        # byte, including one whose stale pools are still selected in the page.
        return ResolvedAspect(
            width=width, height=height,
            base=min(width, height),
            ratio_label="", orientation="",
            randomized=False)

    current_short = min(width, height)
    current_long = max(width, height)
    current_portrait = height > width
    current_ratio = (current_long / current_short) if current_short else 1.0

    if randomize_base:
        base = chooser(bases)
    else:
        # The Extension snaps to the nearest canonical base rather than keeping
        # an arbitrary current short side, so a composed result stays on the
        # same grid the pools are drawn from.
        base = min(ALL_BASES, key=lambda candidate: abs(candidate - current_short))

    if randomize_ratio:
        a, b, ratio_label = chooser(ratios)
        ratio_value = max(a, b) / min(a, b)
    else:
        ratio_value = current_ratio
        ratio_label = ""

    if randomize_orientation:
        # A square has no orientation to randomize, and asking for one would
        # produce a coin flip that changes nothing while claiming it did.
        portrait = (chooser((True, False))
                    if ratio_value > _SQUARE_EPSILON else False)
    else:
        portrait = current_portrait

    short_side = round8(base)
    # `round8(round(...))`, not `round8(...)`. The inner round is upstream's
    # (`studio_ar.py:143`) and it is NOT redundant: Python rounds halves to
    # even, so pre-rounding can push a value across an eighth-boundary. Base
    # 768 at 2.39:1 is 1835.52 -- straight to `round8` gives 1832, upstream
    # gives 1840. A selectable pool combination, so dropping the inner round
    # was a real parity defect and not a stylistic difference.
    long_side = round8(round(base * ratio_value))
    if ratio_value <= _SQUARE_EPSILON:
        resolved_width, resolved_height = short_side, short_side
        orientation = "square"
    elif portrait:
        resolved_width, resolved_height = short_side, long_side
        orientation = "portrait"
    else:
        resolved_width, resolved_height = long_side, short_side
        orientation = "landscape"

    return ResolvedAspect(
        width=resolved_width, height=resolved_height, base=base,
        ratio_label=ratio_label or f"{ratio_value:.2f}",
        orientation=orientation, randomized=True)


def resolve_series(width: int, height: int, count: int, *,
                   randomize_base: bool = False,
                   randomize_ratio: bool = False,
                   randomize_orientation: bool = False,
                   base_pool: Any = None,
                   ratio_pool: Any = None,
                   chooser: Callable[[Sequence[Any]], Any] = random.choice
                   ) -> tuple[ResolvedAspect, ...]:
    """One roll PER IMAGE, in order, each frozen.

    The Extension rolls separately for every image in the batch --
    `studio_generation.py:3136` loops `for img_num in range(total_images)` and
    :3250 calls `randomize_dimensions` inside it, once per iteration. A single
    roll reused across a batch would make every image the same shape, which is
    the opposite of what the owner switched the randomizer on for.

    THE ROLLS CHAIN, and that is deliberate rather than an accident of porting.
    Upstream writes its answer back with `gp.width, gp.height = new_w, new_h`,
    so the NEXT iteration derives its current state from the PREVIOUS image's
    result, not from the submitted size. It is observable whenever a mode is
    off: with only the base randomized, each image inherits the last one's
    ratio, and that ratio is the rounded one. Feeding every roll the original
    dimensions instead would drift away from the Extension after image one.

    Studio resolves the whole series HERE, at admission, and freezes each
    image's width and height onto the job before it is enqueued. Execution and
    retry then read frozen integers -- no dimension randomness is drawn again,
    so a retried job produces the size its recipe records.
    """

    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise AspectRefused("ASPECT_COUNT_INVALID",
                            "A submission must cover at least one image.")

    rolls: list[ResolvedAspect] = []
    current_width, current_height = width, height
    for _ in range(count):
        rolled = resolve(current_width, current_height,
                         randomize_base=randomize_base,
                         randomize_ratio=randomize_ratio,
                         randomize_orientation=randomize_orientation,
                         base_pool=base_pool, ratio_pool=ratio_pool,
                         chooser=chooser)
        rolls.append(rolled)
        current_width, current_height = rolled.width, rolled.height
    return tuple(rolls)


__all__ = (
    "ALL_BASES",
    "ALL_RATIOS",
    "AspectRefused",
    "ResolvedAspect",
    "resolve",
    "resolve_series",
    "round8",
)
