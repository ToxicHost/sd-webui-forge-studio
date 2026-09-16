"""One product-owned terminal allocator release per completed unload.

The owner-trial live smoke measured the gap this closes. With the
verifier's own references dropped before the unload click, allocated VRAM
fell from 6,046,776,320 to 9,568,256 bytes -- the product released
everything it owned -- while **reserved** stayed at 6,111,100,928 against
a 26,214,400 ceiling. All twelve cache clears observed in that run came
from Forge's own `soft_empty_cache` during load, generation, and per-job
release: every one of them BEFORE the final reference release. Nothing
cleared the caching allocator afterwards, so the freed blocks stayed
reserved by the process.

This module owns the missing step, and only that step:

```text
one clear, at the end of a close that actually released a session
after the last Studio-owned reference is dropped
before the lifecycle publishes NO_MODEL
never per job
```

Design constraints, all load-bearing:

* **No new clear site.** The product already owns exactly one sanctioned
  cache clear -- :func:`forge_headless.controlled_device.release_cuda_cache`,
  written to be called only after owned references are released, and
  pinned as the single allowed site by the Tier-0 boundary guard. This
  module supplies the missing ORDER, not a second primitive, and it
  reaches no Forge memory-manager symbol.
* **No eager import.** Nothing here imports torch at module scope, and
  nothing imports it at call time either: the runtime state is *read*
  from ``sys.modules``, so a process that never loaded finds nothing and
  the call is a reported no-op. Startup purity is unchanged.
* **Never raises.** A failing clear returns a scalar report. Callers have
  already released ownership by the time they reach here; resurrecting an
  exception at this point would turn a cleanup hiccup into a failed
  unload while the memory stays freed anyway.
* **No ownership decisions.** This module does not unload models, touch
  the registry, or change Forge offload behaviour. It clears an allocator
  whose blocks are already unreferenced.
"""

from __future__ import annotations

import sys
from typing import Any

#: Report reasons, stable scalars for evidence and tests.
RELEASED = "released"
TORCH_NOT_IMPORTED = "torch_not_imported"
CUDA_NOT_INITIALIZED = "cuda_not_initialized"
CLEAR_FAILED = "clear_failed"

#: The one sanctioned product-owned clear, named for accounting.
STUDIO_RELEASE_CUDA_CACHE = "studio_release_cuda_cache"


def _cuda_is_live(torch_module: Any) -> bool:
    """True only when a CUDA context actually exists in this process."""

    cuda = getattr(torch_module, "cuda", None)
    if cuda is None:
        return False
    try:
        if not cuda.is_available():
            return False
        # `is_initialized` is the difference between "a device exists" and
        # "this process built a context on it". A CPU-only run, and a run
        # that never loaded, must both no-op.
        is_initialized = getattr(cuda, "is_initialized", None)
        if callable(is_initialized) and not is_initialized():
            return False
    except BaseException:  # noqa: BLE001 - a refusing probe is a no-op
        return False
    return True


def release_terminal_cache() -> dict[str, Any]:
    """Clear the allocator once, after ownership has been released.

    Returns a scalar report; never raises. ``called`` is True only when a
    clear actually ran, so a caller can count terminal clears without
    counting the no-ops.
    """

    report: dict[str, Any] = {
        "terminal": True,
        "called": False,
        "primitive": None,
        "reason": TORCH_NOT_IMPORTED,
    }

    torch_module = sys.modules.get("torch")
    if torch_module is None:
        return report

    if not _cuda_is_live(torch_module):
        report["reason"] = CUDA_NOT_INITIALIZED
        return report

    # The one sanctioned Studio clear. Imported here, not at module
    # scope, so this module stays free of device machinery; the helper
    # itself only reaches torch, never Forge's memory manager.
    from .controlled_device import release_cuda_cache

    report["primitive"] = STUDIO_RELEASE_CUDA_CACHE
    try:
        release_cuda_cache()
    except BaseException as exc:  # noqa: BLE001 - reported, never raised
        report["reason"] = CLEAR_FAILED
        report["error"] = type(exc).__name__
        return report
    report["called"] = True
    report["reason"] = RELEASED
    # Recorded only when a clear actually ran. A process with no CUDA context
    # returns above, and its absence from the record is the truth about it.
    from .unload_events import TERMINAL_CACHE_CLEAR, record

    record(TERMINAL_CACHE_CLEAR)
    return report


__all__ = (
    "CLEAR_FAILED",
    "CUDA_NOT_INITIALIZED",
    "RELEASED",
    "STUDIO_RELEASE_CUDA_CACHE",
    "TORCH_NOT_IMPORTED",
    "release_terminal_cache",
)
