"""Telling two pictures apart, and telling two copies of one picture together.

Duplicate detection was the Gallery's one genuinely blocked feature: the
Extension needs `imagehash`, which drags in scipy and PyWavelets and is not
installed. TrackImage 3.94 had already solved that -- it computes the same
hash itself with numpy and drops all three dependencies. This is that
improvement, taken one step further so it needs no numpy either.

**Bit-identical to `imagehash.phash(hash_size=16)`**, and therefore to 3.94's.
Same pipeline: grayscale, resize to 64x64 with LANCZOS, 2-D DCT-II, keep the
top-left 16x16 low-frequency block, threshold at its median. An owner's
existing hashes stay valid, which is the whole reason to match rather than
invent.

WHY PURE PYTHON. `forge_studio` may not import numpy -- it sits beside torch
and gradio in the owned-package purity rule, because the presentation layer
must not drag engine weight into the process. Two observations make that free
rather than costly:

* only the top-left 16x16 of the transform is ever read, so the matrices are
  16x64 and 64x16 rather than 64x64. That is ~82,000 multiply-adds, single-digit
  milliseconds -- against the 50-200ms it takes Pillow to decode the file in the
  first place. The DCT is not the expensive part and never was;
* the Hamming distance needs no matrix at all. A 256-bit hash is one Python
  int, and `(a ^ b).bit_count()` is a single C call.

WHAT IS DELIBERATELY NOT HERE is the tile signature, and it is worth writing
down so nobody adds it back. TrackImage introduced an 8x8 "region score" in
v3.73 to separate "the same image with a watermark added" from "a different
image with a similar composition", and its code comment explains the idea
persuasively. Its author removed it in v3.93, and said why:

```text
They promised something the method could not deliver -- a watermark scored
around 95% and never showed up, while genuinely different images scored low.
The tile signatures stay in the database and are still written on import;
nothing reads them any more.
```

So 3.94 still COMPUTES and STORES one, and nothing consults it. A first pass
here copied that state faithfully -- a decode-time computation and 64 bytes a
row, read by nobody -- on the strength of the v3.73 comment, without noticing
the v3.93 entry that retracted it. Reading the comment is not the same as
reading the changelog.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from .gallery_index import ImagingUnavailable

#: 16x16 bits = a 256-bit hash, 64 hex characters.
HASH_SIZE = 16

#: The image is resized to HASH_SIZE * HIGH_FREQUENCY_FACTOR square first, so
#: the low-frequency block that survives is a quarter of it per side.
HIGH_FREQUENCY_FACTOR = 4

#: Below this many differing bits, two pictures are the same picture. 3.94's
#: default, and the Extension's.
DEFAULT_THRESHOLD = 10

#: The most bits that can differ.
MAX_DISTANCE = HASH_SIZE * HASH_SIZE

_MATRICES: dict[tuple[int, int], list[list[float]]] = {}


def _dct_matrix(size: int, keep: int) -> list[list[float]]:
    """The first `keep` rows of an unnormalised DCT-II matrix of `size`.

    Only `keep` rows, because only the top-left block of the transform is
    read. Cached: it depends on nothing but its two numbers.
    """

    found = _MATRICES.get((size, keep))
    if found is None:
        found = [
            [2.0 * math.cos(math.pi * k * (2 * i + 1) / (2 * size))
             for i in range(size)]
            for k in range(keep)
        ]
        _MATRICES[(size, keep)] = found
    return found


def _grayscale_pixels(source: Any, size: int) -> list[list[float]]:
    """The square grayscale array both signatures are built from."""

    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ImagingUnavailable(
            "Duplicate detection cannot read images because Pillow is not "
            "installed."
        ) from error

    def prepare(image: Any) -> list[list[float]]:
        # Orientation applied first: a photograph rotated by EXIF is a
        # DIFFERENT picture to the transform, and two copies of one image
        # tagged differently would otherwise never match.
        image = ImageOps.exif_transpose(image)
        image = image.convert("L").resize((size, size), Image.LANCZOS)
        # `tobytes()` on an "L" image is one byte per pixel in row order --
        # the same numbers `getdata()` yields, without the deprecation and
        # without building a list of boxed integers first.
        data = image.tobytes()
        return [
            [float(v) for v in data[row * size:(row + 1) * size]]
            for row in range(size)
        ]

    if isinstance(source, (str, Path)):
        with Image.open(source) as image:
            return prepare(image)
    return prepare(source)


def fingerprint(source: Any, hash_size: int = HASH_SIZE) -> str | None:
    """The perceptual hash of one picture. None if it cannot be read."""

    size = hash_size * HIGH_FREQUENCY_FACTOR
    try:
        pixels = _grayscale_pixels(source, size)
    except ImagingUnavailable:
        raise
    except (OSError, ValueError, TypeError):
        return None

    matrix = _dct_matrix(size, hash_size)
    # M @ px, keeping only the rows that survive: hash_size x size.
    rows = [
        [sum(m[i] * column[i] for i in range(size)) for column in zip(*pixels)]
        for m in matrix
    ]
    # ... @ M.T, keeping only those columns: hash_size x hash_size.
    low = [
        [sum(row[i] * m[i] for i in range(size)) for m in matrix]
        for row in rows
    ]

    flat = [value for row in low for value in row]
    median = _median(flat)
    value = 0
    for entry in flat:
        value = (value << 1) | int(entry > median)
    digits = hash_size * hash_size // 4
    return f"{value:0{digits}x}"


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def distance(first: str, second: str) -> int:
    """How many bits differ. 0 is the same picture.

    `int.bit_count()` -- one C call on a 256-bit integer. This is why
    duplicate detection needs no numeric library at all.
    """

    if not first or not second:
        return MAX_DISTANCE
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except ValueError:
        return MAX_DISTANCE


def near(target: str, candidates: Iterable[tuple[int, str]],
         threshold: int = DEFAULT_THRESHOLD) -> list[tuple[int, int]]:
    """`(image_id, distance)` for every candidate within `threshold`.

    Sorted closest first, which is the order a page wants to show them in.
    """

    found = [
        (image_id, distance(target, other))
        for image_id, other in candidates
        if other
    ]
    return sorted(
        [(image_id, gap) for image_id, gap in found if gap <= threshold],
        key=lambda pair: pair[1],
    )


def group(pairs: Iterable[tuple[int, int]]) -> list[list[int]]:
    """Collapse pairs into groups of images that are all the same picture.

    Union-find rather than "everything within N of the first one": similarity
    is not transitive at a fixed threshold, and an owner looking at a duplicate
    group wants ONE group per picture rather than one per comparison.
    """

    parent: dict[int, int] = {}

    def root(item: int) -> int:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in pairs:
        a, b = root(left), root(right)
        if a != b:
            parent[a] = b

    groups: dict[int, list[int]] = {}
    for item in parent:
        groups.setdefault(root(item), []).append(item)
    return [sorted(members) for members in groups.values() if len(members) > 1]


__all__ = (
    "DEFAULT_THRESHOLD",
    "HASH_SIZE",
    "MAX_DISTANCE",
    "distance",
    "fingerprint",
    "group",
    "near",
)
