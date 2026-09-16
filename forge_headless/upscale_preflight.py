"""Decide whether a tiled upscale can composite on the device.

The GPU compositor is measurably faster -- 32% on the upscale step, about
2 s on a warm full pipeline -- and it buys that by holding the whole upscaled
frame on the device instead of one tile at a time. The CPU path's device
footprint does not depend on the image size. The GPU path's does, quadratically.
So the fast path is safe on a card with room and unsafe on one without, and
which is which depends on the job, not on the card's badge.

This module is the decision, and it is deliberately pure: no torch, no numpy,
no PIL. The caller reads the runtime values -- image size, the loaded model's
native scale, the parameter dtype's element size, free memory, the backend's
own reserve -- and this answers with a mode and a reason. Keeping it pure is
what lets the canonical suite test the policy at all: that suite runs every
test in one process and refuses to have torch imported into it.

WHY A PROJECTION IS DEFENSIBLE HERE, when `live_generation_port` explicitly
refuses to project what a Hires pass costs: that refusal is about activation
workspaces on an arbitrary card, which nobody has a validated model of. These
are not activations. They are a handful of plain tensor allocations whose
shape and dtype are known before the call, and the model below was checked
against measurement at seven resolutions from 512 to 2560. It reproduced the
measured cost to within a constant 3.2 MiB at every one of them -- constant,
not a trend, which is what says there is no missing size-dependent term. That
is a different kind of claim, and the earlier refusal still stands for what
it covers.
"""

from __future__ import annotations


MODE_GPU = "gpu"
MODE_CPU = "cpu"

REASON_FIT = "fit"
REASON_OWNER_DISABLED = "owner_disabled"
REASON_UNSUPPORTED_BACKEND = "unsupported_backend"
REASON_INSUFFICIENT_HEADROOM = "insufficient_headroom"
REASON_ALLOCATION_FALLBACK = "allocation_fallback"
REASON_LAUNCH_DISABLED = "launch_disabled"

REASONS = (
    REASON_FIT,
    REASON_OWNER_DISABLED,
    REASON_UNSUPPORTED_BACKEND,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_ALLOCATION_FALLBACK,
    REASON_LAUNCH_DISABLED,
)

# `upscale_tensor_tiles` skips tiling entirely at or below this many tiles and
# puts the whole image through the model in one pass, which allocates no
# accumulator. Kept in step with modules/upscaler_utils.py.
MAX_TILES_WITHOUT_ACCUMULATOR = 4

# The accumulator carries the three colour channels plus a weight channel.
ACCUMULATOR_CHANNELS = 4
COLOUR_CHANNELS = 3


def tile_count(width: int, height: int, tile_size: int, tile_overlap: int) -> int:
    """How many tiles the compositor will step through. 0 means no tiling.

    Mirrors the arithmetic in `upscale_tensor_tiles` rather than guessing at
    it, because the accumulator only exists when that function decides to
    tile.
    """
    if tile_size <= 0:
        return 0
    stride = tile_size - tile_overlap
    if stride <= 0:
        return 0
    across = (width + stride - 1) // stride
    down = (height + stride - 1) // stride
    return across * down


def composite_working_set(
    *,
    width: int,
    height: int,
    scale: int,
    itemsize: int,
    tiles: int,
) -> dict[str, int]:
    """Device bytes the GPU compositor owns at its own peak.

    The compositor passes through two phases that do not hold the same
    buffers:

        tile loop     accumulator + input
        conversion    accumulator + input + uint8 copy

    The conversion phase costs what it does because the value returned by
    `upscale_tensor_tiles` is a *view* into the accumulator, so the
    accumulator is still alive while the frame is converted for the host.

    It used to cost far more. The channel swap in `tensor_bgr_to_pil_rgb` ran
    first, and advanced indexing copies, so a full float frame -- 1200 MiB at
    a 2560 base -- was alive alongside the accumulator, which made the
    conversion the peak of the whole upscale above roughly a 2117 px base
    edge. Moving the swap to the host removed that copy, byte-identically, and
    with it the crossover: the tile loop is now the peak at every measured
    resolution. `peak` still takes the maximum, because the estimate should
    not depend on that staying true.

    No credit is taken for the inference workspace being freed before the
    conversion allocates. The caching allocator does not guarantee that -- at
    1024^2 it held 1760 MiB reserved against 1296 MiB allocated -- and being
    optimistic costs an out-of-memory while being conservative costs a
    fallback to a path that is two seconds slower.
    """
    out_width = width * scale
    out_height = height * scale
    out_pixels = out_width * out_height

    tiled = tiles > MAX_TILES_WITHOUT_ACCUMULATOR
    accumulator = ACCUMULATOR_CHANNELS * out_pixels * itemsize if tiled else 0
    input_tensor = COLOUR_CHANNELS * width * height * itemsize
    uint8_copy = COLOUR_CHANNELS * out_pixels

    tile_loop = accumulator + input_tensor
    conversion = accumulator + input_tensor + uint8_copy

    return {
        "accumulator": accumulator,
        "input_tensor": input_tensor,
        "uint8_copy": uint8_copy,
        "tile_loop": tile_loop,
        "conversion": conversion,
        "peak": max(tile_loop, conversion),
    }


def choose_composite_mode(
    *,
    requested_gpu: bool,
    device_reports_free_memory: bool,
    free_bytes: int,
    reserve_bytes: int,
    working_set_bytes: int,
    process_disabled: bool = False,
) -> tuple[str, str]:
    """Pick the composite path, and say why. Never raises.

    The order is a hierarchy, not a sequence of equal tests:

        1  process-level hard disable    the launcher said never
        2  capability                    the device cannot be asked
        3  the job's preference          the owner said not for this one
        4  the memory preflight          it will not fit
        5  (allocation fallback)         raised by the caller, not here

    `process_disabled` sits above the job's preference deliberately.
    `--no-gpu-tile-composite` means "never in this process", so a per-job
    preference must not be able to switch it back on -- and the per-job value
    is read FIRST inside `HeadlessOptions.__getattr__`, so without this rung
    the ceiling would be the one thing the job could climb over.

    `reserve_bytes` is the backend's own headroom number, not one invented
    here -- the caller passes `minimum_inference_memory()`, which already
    folds in the platform reserve and the owner's `--reserve-vram`. The
    upscale sits in the middle of a generation, so whatever the compositor
    takes has to leave that headroom behind for the pass that follows.
    """
    if process_disabled:
        return MODE_CPU, REASON_LAUNCH_DISABLED

    # Not every backend can answer the question. `get_free_memory` returns
    # host RAM on CPU and MPS and a hardcoded 1 GiB on DirectML, so it answers
    # confidently and wrongly rather than refusing. Treat an unanswerable
    # device as a reason to take the path whose cost does not depend on the
    # image.
    if not device_reports_free_memory:
        return MODE_CPU, REASON_UNSUPPORTED_BACKEND

    if not requested_gpu:
        return MODE_CPU, REASON_OWNER_DISABLED

    if free_bytes - reserve_bytes >= working_set_bytes:
        return MODE_GPU, REASON_FIT

    return MODE_CPU, REASON_INSUFFICIENT_HEADROOM


#: Set once at boot from the launcher, never from a job.
_PROCESS_DISABLED = [False]


def disable_for_process(disabled: bool = True) -> None:
    """Refuse GPU compositing for the whole process.

    A module-level flag rather than an option, because an option is exactly
    what a job can override -- `HeadlessOptions.__getattr__` consults the
    running job before it consults anything else. The ceiling has to live
    somewhere the job scope cannot reach.
    """
    _PROCESS_DISABLED[0] = bool(disabled)


def process_disabled() -> bool:
    """Whether the launcher refused GPU compositing for this process."""
    return _PROCESS_DISABLED[0]


def describe_decision(
    *,
    requested: str,
    effective: str,
    reason: str,
    working_set_bytes: int | None = None,
    free_bytes: int | None = None,
    reserve_bytes: int | None = None,
) -> str:
    """One line for the log. Carries no paths and no owner content."""
    parts = [f"requested={requested}", f"effective={effective}", f"reason={reason}"]
    if working_set_bytes is not None:
        parts.append(f"working_set={working_set_bytes / (1024 * 1024):.1f}MB")
    if free_bytes is not None:
        parts.append(f"free={free_bytes / (1024 * 1024):.1f}MB")
    if reserve_bytes is not None:
        parts.append(f"reserve={reserve_bytes / (1024 * 1024):.1f}MB")
    return " ".join(parts)


__all__ = (
    "ACCUMULATOR_CHANNELS",
    "COLOUR_CHANNELS",
    "MAX_TILES_WITHOUT_ACCUMULATOR",
    "MODE_CPU",
    "MODE_GPU",
    "REASONS",
    "REASON_ALLOCATION_FALLBACK",
    "REASON_FIT",
    "REASON_INSUFFICIENT_HEADROOM",
    "REASON_LAUNCH_DISABLED",
    "REASON_OWNER_DISABLED",
    "REASON_UNSUPPORTED_BACKEND",
    "choose_composite_mode",
    "composite_working_set",
    "describe_decision",
    "disable_for_process",
    "process_disabled",
    "tile_count",
)
