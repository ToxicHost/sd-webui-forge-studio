"""Stage timing and VRAM telemetry for one controlled load.

Both exist because the first Phase 2B attempt recorded neither on the failure
path: peaks were read only after a successful construction, the exception routed
around it, and the numbers were gone for good once the attempt was spent.

So the rule here is that every measurement is collected in guaranteed terminal
handling -- success, exception, and timeout alike -- and a stage that never ran
reports `not_reached` rather than `0.0`, which would read as "instant".

Neither class imports Torch at module scope.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


NOT_REACHED = "not_reached"

#: Every stage the controlled load can pass through, in order. Declared up front
#: so a stage that never ran is visibly absent rather than silently missing.
STAGES: tuple[str, ...] = (
    "authorization_validation",
    "header_preflight",
    "options_bootstrap",
    "cuda_initialization",
    "checkpoint_read",
    "text_encoder_read",
    "vae_read",
    "engine_construction",
    "cleanup",
    "total_worker_runtime",
)


class StageTimer:
    """Monotonic stage durations. A stage never entered stays `not_reached`."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._durations: dict[str, float] = {}
        self._marks: dict[str, float] = {}

    def start(self, stage: str) -> None:
        self._marks[stage] = time.monotonic()

    def stop(self, stage: str) -> None:
        started = self._marks.pop(stage, None)
        if started is not None:
            self._durations[stage] = time.monotonic() - started

    def mark_instant(self, stage: str) -> None:
        """Record the moment a stage happened, for stages with no duration."""
        self._durations.setdefault(stage, time.monotonic() - self._started)

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for stage in STAGES:
            if stage == "total_worker_runtime":
                out[stage] = round(self.elapsed(), 3)
            elif stage in self._durations:
                out[stage] = round(self._durations[stage], 3)
            else:
                out[stage] = NOT_REACHED
        return out


@dataclass
class VramSample:
    allocated: int
    reserved: int

    def to_dict(self) -> dict[str, int]:
        return {"allocated_bytes": self.allocated, "reserved_bytes": self.reserved}


@dataclass
class VramTelemetry:
    """Four samples that together distinguish a leak from a warm cache.

    `after_release` is the load-bearing one: taken **before** any cache
    clearing, it is what shows owned references were dropped rather than hidden
    behind an `empty_cache()` call.
    """

    before_load: VramSample | None = None
    peak: VramSample | None = None
    after_release: VramSample | None = None
    after_cache_clear: VramSample | None = None
    ceiling_bytes: int = 0
    ceiling_exceeded: bool | str = NOT_REACHED
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def render(sample: VramSample | None) -> Any:
            return sample.to_dict() if sample is not None else NOT_REACHED

        return {
            "before_load": render(self.before_load),
            "peak": render(self.peak),
            "after_release_before_cache_clear": render(self.after_release),
            "after_cache_clear": render(self.after_cache_clear),
            "ceiling_bytes": self.ceiling_bytes,
            "ceiling_exceeded": self.ceiling_exceeded,
            "notes": list(self.notes),
        }


def sample_current() -> VramSample | None:
    """Current allocated/reserved, or `None` when there is no CUDA to ask."""
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    return VramSample(
        allocated=int(torch.cuda.memory_allocated()),
        reserved=int(torch.cuda.memory_reserved()),
    )


def sample_peak() -> VramSample | None:
    """Peak allocated/reserved since the last reset, or `None` without CUDA."""
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    return VramSample(
        allocated=int(torch.cuda.max_memory_allocated()),
        reserved=int(torch.cuda.max_memory_reserved()),
    )


def reset_peak() -> bool:
    """Reset peak counters immediately before payload access. Idempotent."""
    try:
        import torch
    except ImportError:
        return False
    if not torch.cuda.is_available():
        return False
    torch.cuda.reset_peak_memory_stats()
    return True


class PayloadWatch:
    """Records each tensor-payload open, attributes a role, and consumes once.

    The single-attempt boundary is defined as "any tensor payload is accessed",
    so this is where the authorization is spent -- at the observed event rather
    than at a hopeful moment beforehand.
    """

    def __init__(self, role_by_path: dict[str, str], timer: StageTimer) -> None:
        self._role_by_path = {key.casefold(): value for key, value in role_by_path.items()}
        self._timer = timer
        self.opened: list[str] = []
        self.on_first_open = None
        self._installed = None
        self._module = None

    def role_for(self, filename: str) -> str:
        return self._role_by_path.get(str(filename).casefold(), "unauthorized")

    def install(self) -> None:
        import safetensors

        # Hold the module itself, not just the function: restore must not depend
        # on an import succeeding later, and under an isolated interpreter the
        # re-import can fail after the original call site is gone.
        self._module = safetensors
        self._installed = safetensors.safe_open
        real = self._installed
        watch = self

        def recording(filename, *args, **kwargs):
            role = watch.role_for(filename)
            if not watch.opened and watch.on_first_open is not None:
                watch.on_first_open()
            watch.opened.append(role)
            stage = f"{role}_read"
            watch._timer.start(stage)
            try:
                return real(filename, *args, **kwargs)
            finally:
                watch._timer.stop(stage)

        safetensors.safe_open = recording  # type: ignore[assignment]

    def restore(self) -> None:
        if self._installed is None or self._module is None:
            return
        self._module.safe_open = self._installed  # type: ignore[attr-defined]
        self._installed = None
        self._module = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_payload_opened": "checkpoint" in self.opened,
            "text_encoder_payload_opened": "text_encoder" in self.opened,
            "vae_payload_opened": "vae" in self.opened,
            "unauthorized_payload_opened": "unauthorized" in self.opened,
            "open_sequence": list(self.opened),
            "tensor_boundary_crossed": bool(self.opened),
        }
