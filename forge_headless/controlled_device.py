"""Controlled device initialization for one authorized model load.

Importing this module is free: Torch is imported inside the functions, so the
module can be inspected, tested, and included in the package without touching a
device. Nothing here runs unless an authorization object is handed in.

`initialize_device` is the one entry point a load uses. It initializes the
device the ENGINE chose -- Neo's `backend/memory_management.get_torch_device()`
when that module is already imported, which it is after `bootstrap_backend` --
so the report describes the device generation will actually run on:

    cuda  -> `initialize_cuda`, unchanged since VC1;
    mps   -> `initialize_mps` (Apple Silicon, MAC1);
    other -> refused, with a sentence an owner can act on.

XPU and DirectML are refused rather than initialized: Neo can drive them, but
nothing in Studio has been checked on them, and reporting otherwise would be
untrue.

No memory fraction, ceiling or watermark is set on ANY device (VC1). No
synthetic benchmark is run. Everything reported is read from device properties
or memory counters.

Source review: Evidence/source-review/MAC1-mps-device.md.
"""

from __future__ import annotations

import sys

from .contracts import HeadlessError
from .load_authorization import ControlledLoadAuthorization, DeviceReport


CUDA = "cuda"
MPS = "mps"
CPU = "cpu"

#: The macOS release from which Neo stores and computes in bfloat16 on MPS
#: (`backend/memory_management.should_use_bf16`: `mac_version() < (14,)` is
#: False). Mirrored for the report only; Neo makes the real decision.
MPS_BF16_MACOS = (14,)

NO_ACCELERATOR_MESSAGE = (
    "Studio found no NVIDIA graphics card (CUDA) and no Apple Silicon GPU "
    "(MPS) on this computer, so it cannot generate images. Running on the "
    "processor alone is not supported: one picture would take many minutes."
)


def engine_device_kind() -> str | None:
    """The device type Neo's memory manager selected, or None if not loaded.

    Read from `sys.modules`, never imported: importing `memory_management`
    initialises the device, and a question must not do that.
    """

    memory_management = sys.modules.get("backend.memory_management")
    if memory_management is None:
        return None
    try:
        return str(memory_management.get_torch_device().type)
    except Exception:  # noqa: BLE001 - an engine that cannot answer is silent
        return None


def probe_device_kind() -> str:
    """`cuda`, `mps` or `cpu` from Torch alone, in Neo's order of preference.

    Neo checks MPS before CUDA (`memory_management.py:120-129`); no machine
    has both, so the order only matters for being faithful.
    """

    import torch

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    try:
        if mps is not None and mps.is_available():
            return MPS
    except Exception:  # noqa: BLE001 - a refusing probe means "not MPS"
        pass
    if torch.cuda.is_available():
        return CUDA
    return CPU


def initialize_device(authorization: ControlledLoadAuthorization) -> DeviceReport:
    """Initialize the engine's device and report it. Fences nothing.

    Raises rather than degrading when there is no supported accelerator: no
    checkpoint should be opened, so this is the place to stop.
    """

    try:
        import torch  # noqa: F401
    except ImportError as exc:  # pragma: no cover - Torch is present in the venv
        raise HeadlessError(
            "CONTROLLED_LOAD_TORCH_UNAVAILABLE",
            "Torch is not importable in this runtime.",
        ) from exc

    kind = engine_device_kind() or probe_device_kind()
    if kind == CUDA:
        return initialize_cuda(authorization)
    if kind == MPS:
        return initialize_mps(authorization)
    if kind == CPU:
        raise HeadlessError("CONTROLLED_LOAD_NO_ACCELERATOR", NO_ACCELERATOR_MESSAGE)
    raise HeadlessError(
        "CONTROLLED_LOAD_DEVICE_UNSUPPORTED",
        f"The engine selected a {kind!r} device. Studio supports NVIDIA (CUDA) "
        "and Apple Silicon (MPS) only.",
    )


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


def macos_version() -> tuple[int, ...] | None:
    """`platform.mac_ver()` as a tuple, as Neo's `mac_version()` reads it."""

    import platform

    try:
        return tuple(int(n) for n in platform.mac_ver()[0].split("."))
    except Exception:  # noqa: BLE001 - not macOS, or an unreadable answer
        return None


def apple_chip_name() -> str:
    """`Apple M2 Pro` and the like, or "" when it cannot be read.

    A chip name is hardware, not identity, exactly as a GPU model name is.
    """

    import shutil
    import subprocess

    tool = shutil.which("sysctl")
    if tool is None:
        return ""
    try:
        done = subprocess.run([tool, "-n", "machdep.cpu.brand_string"],
                              capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "").strip() if done.returncode == 0 else ""


def initialize_mps(authorization: ControlledLoadAuthorization) -> DeviceReport:
    """Initialize Apple's Metal device (MPS) and report it. Fences nothing.

    An Apple Silicon GPU has no memory of its own: it shares the system's RAM
    ("unified memory"). Neo plans from that, reading `psutil` for both total
    and free on MPS (`memory_management.get_total_memory` / `get_free_memory`),
    so the report reads the same numbers rather than inventing a "VRAM" size.

    No `torch.mps.set_per_process_memory_fraction` and no
    `PYTORCH_MPS_HIGH_WATERMARK_RATIO`: as on CUDA (VC1), Studio sets no
    ceiling. PyTorch's own default watermark stays whatever PyTorch ships.
    """

    del authorization
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - Torch is present in the venv
        raise HeadlessError(
            "CONTROLLED_LOAD_TORCH_UNAVAILABLE",
            "Torch is not importable in this runtime.",
        ) from exc

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is None or not mps.is_available():
        built = bool(mps is not None and getattr(mps, "is_built", lambda: False)())
        raise HeadlessError(
            "CONTROLLED_LOAD_MPS_UNAVAILABLE",
            "This Mac's GPU (MPS) is not available to PyTorch: "
            + ("macOS reports no Metal device, or macOS is older than 12.3."
               if built else "this PyTorch was built without MPS support."),
        )

    import psutil

    memory = psutil.virtual_memory()
    total = int(memory.total)
    free = int(memory.available)
    version = macos_version()
    supports_bf16 = bool(version is not None and version >= MPS_BF16_MACOS)

    notes = [
        "unified memory: the GPU shares system RAM, so these are RAM figures",
        "no memory ceiling; Neo's memory manager plans from system RAM",
        "macOS " + (".".join(str(n) for n in version) if version else "UNKNOWN"),
    ]
    recommended = getattr(getattr(torch, "mps", None), "recommended_max_memory", None)
    if callable(recommended):
        try:
            notes.append(f"Metal recommended working set {int(recommended())} bytes")
        except Exception:  # noqa: BLE001 - an optional fact
            pass

    return DeviceReport(
        initialized=True,
        device_kind=MPS,
        device_name=apple_chip_name() or "Apple Silicon GPU (MPS)",
        # CUDA's "compute capability" has no Metal equivalent.
        compute_capability="UNKNOWN",
        torch_version=str(torch.__version__),
        torch_cuda_version="none",
        total_vram_bytes=total,
        free_vram_bytes=free,
        supports_fp16=True,
        supports_bf16=supports_bf16,
        ceiling_bytes=total,
        ceiling_enforced=False,
        ceiling_mechanism="none",
        notes=tuple(notes),
    )


def peak_memory(device_index: int = 0) -> tuple[int, int]:
    """Return `(peak_allocated, peak_reserved)` in bytes without allocating.

    CUDA only: MPS keeps no peak counter, and (0, 0) is what a probe gets
    without CUDA, as before.
    """

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
    """Return cached blocks to the driver (CUDA) or to macOS (MPS).

    Reported separately from owned-reference release, and only ever called
    *after* it: an emptied cache proves nothing about whether references were
    dropped, and using it as a substitute would hide a leak.

    The name predates MPS and is kept: the terminal release and its tests
    account for this one call by name. On a Mac the cache is system RAM, so
    returning it matters more, not less.
    """

    import torch

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        return
    mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
    mps = getattr(torch, "mps", None)
    if mps_backend is not None and mps is not None and mps_backend.is_available():
        mps.empty_cache()
