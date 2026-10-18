"""Controlled CUDA initialization for one authorized model load.

Importing this module is free: Torch is imported inside the functions, so the
module can be inspected, tested, and included in the package without touching a
device. Nothing here runs unless an authorization object is handed in.

Only CUDA is initialized. MPS, XPU, and DirectML are never touched -- not
because they are unsupported in principle, but because this phase authorizes one
device kind and claiming otherwise would be untrue.

No synthetic benchmark is run. Everything reported is read from device
properties or memory counters.
"""

from __future__ import annotations

from .contracts import HeadlessError
from .load_authorization import ControlledLoadAuthorization, DeviceReport


CUDA = "cuda"


def initialize_cuda(authorization: ControlledLoadAuthorization) -> DeviceReport:
    """Initialize CUDA and report the device. Fences nothing.

    VC1. This set `torch.cuda.set_per_process_memory_fraction` to a 14 GiB
    ceiling from the first controlled load (2026-07-26) until 2026-10-18. Neo
    sets no such fraction: its memory manager plans what stays on the card
    from the card's real free memory (`backend/memory_management.py`), which
    the fraction does not change, so the fence turned Neo's offload decisions
    into out-of-memory errors and left every larger card's memory unused.
    Studio now uses the whole card, as Neo does.

    Raises rather than degrading when CUDA is unavailable: no checkpoint
    should be opened, so this is the place to stop.
    """

    try:
        import torch
    except ImportError as exc:  # pragma: no cover - Torch is present in the venv
        raise HeadlessError(
            "CONTROLLED_LOAD_TORCH_UNAVAILABLE",
            "Torch is not importable in this runtime.",
        ) from exc

    if not torch.cuda.is_available():
        raise HeadlessError(
            "CONTROLLED_LOAD_CUDA_UNAVAILABLE",
            "CUDA is not available, and this phase authorizes no other device.",
        )

    device = torch.device(CUDA, torch.cuda.current_device())
    properties = torch.cuda.get_device_properties(device)
    total = int(properties.total_memory)
    free, _reported_total = torch.cuda.mem_get_info(device)

    torch.cuda.reset_peak_memory_stats(device)

    try:
        capability = "{}.{}".format(*torch.cuda.get_device_capability(device))
    except Exception:  # noqa: BLE001
        capability = "UNKNOWN"

    try:
        supports_bf16 = bool(torch.cuda.is_bf16_supported())
    except Exception:  # noqa: BLE001
        supports_bf16 = False

    return DeviceReport(
        initialized=True,
        device_kind=CUDA,
        # A GPU model name is hardware, not identity: it contains no user name
        # and no path, so it is safe to report and useful for reproducing.
        device_name=str(properties.name),
        compute_capability=capability,
        torch_version=str(torch.__version__),
        torch_cuda_version=str(torch.version.cuda or "UNKNOWN"),
        total_vram_bytes=total,
        free_vram_bytes=int(free),
        supports_fp16=True,
        supports_bf16=supports_bf16,
        # The report's shape is kept; what it says is that the card is the
        # only bound.
        ceiling_bytes=total,
        ceiling_enforced=False,
        ceiling_mechanism="none",
        notes=("no VRAM ceiling; Neo's memory manager uses the whole card",),
    )


def peak_memory(device_index: int = 0) -> tuple[int, int]:
    """Return `(peak_allocated, peak_reserved)` in bytes without allocating."""

    import torch

    if not torch.cuda.is_available():
        return (0, 0)
    return (
        int(torch.cuda.max_memory_allocated(device_index)),
        int(torch.cuda.max_memory_reserved(device_index)),
    )


def current_memory(device_index: int = 0) -> tuple[int, int]:
    """Return `(allocated, reserved)` in bytes without allocating."""

    import torch

    if not torch.cuda.is_available():
        return (0, 0)
    return (
        int(torch.cuda.memory_allocated(device_index)),
        int(torch.cuda.memory_reserved(device_index)),
    )


def release_cuda_cache() -> None:
    """Return cached blocks to the driver.

    Reported separately from owned-reference release, and only ever called
    *after* it: an emptied cache proves nothing about whether references were
    dropped, and using it as a substitute would hide a leak.
    """

    import torch

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
