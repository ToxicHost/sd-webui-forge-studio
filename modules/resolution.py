"""Resolution step rounding, with no UI, Gradio, Torch, or backend imports.

`sRound` used to live in `modules/ui.py`, which meant `modules/processing.py` --
the inference core -- reached the entire Gradio UI tree, including the twelve
Gradio monkeypatches installed as an import side effect of
`modules/gradio_extensions.py`, in order to call five lines of arithmetic.

The step comes from `opts.res_step`, so it cannot be resolved at import time
without importing `modules.shared` (which imports `backend.memory_management`
and therefore Torch). It is resolved on first use instead and then cached, which
keeps the original read-once semantics: `modules/ui.py` froze the value at its
own import time, and the first `sRound` call also happens after options are
loaded.
"""

from __future__ import annotations

import math

_step: int | None = None


def resolution_step() -> int:
    """Return the configured resolution step, reading `opts` once."""
    global _step
    if _step is None:
        from modules.shared import opts

        _step = int(opts.res_step)
    return _step


def sRound(val: int | float) -> int:
    """Round to the nearest multiple of the configured resolution step."""
    step = resolution_step()
    return math.floor(val / step + 0.5) * step


def __getattr__(name: str):
    # `_STEP` was the public-by-accident name in `modules/ui.py`; keep it
    # working for legacy callers without freezing it at import time.
    if name == "_STEP":
        return resolution_step()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
