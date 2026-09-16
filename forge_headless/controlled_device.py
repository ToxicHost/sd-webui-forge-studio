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
    """Initialize CUDA and enforce the authorization's VRAM ceiling.

    Raises rather than degrading: if CUDA is unavailable, or the ceiling cannot
    be enforced, no checkpoint should be opened, so this is the place to stop.
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

    ceiling = int(authorization.vram_ceiling_bytes)
    notes: list[str] = []

    # Enforcement, not intention. `set_per_process_memory_fraction` makes the
    # allocator refuse beyond the fraction, so exceeding the ceiling raises an
    # OOM instead of quietly consuming the card.
    if total <= 0:
        raise HeadlessError(
            "CONTROLLED_LOAD_CEILING_NOT_ENFORCEABLE",
            "Total device memory could not be established.",
        )
    if ceiling >= total:
        # The card is smaller than the ceiling, so the hardware enforces it.
        fraction = 1.0
        mechanism = "device_total_below_ceiling"
        notes.append(
            "ceiling exceeds total device memory; the device itself is the bound"
        )
        effective = total
    else:
        fraction = ceiling / total
        mechanism = "torch.cuda.set_per_process_memory_fraction"
        effective = ceiling

    try:
        torch.cuda.set_per_process_memory_fraction(fraction, device)
    except (RuntimeError, ValueError, AttributeError) as exc:
        raise HeadlessError(
            "CONTROLLED_LOAD_CEILING_NOT_ENFORCEABLE",
            "The VRAM ceiling could not be enforced before opening a checkpoint.",
        ) from exc

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
        ceiling_bytes=effective,
        ceiling_enforced=True,
        ceiling_mechanism=mechanism,
        notes=tuple(notes),
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
