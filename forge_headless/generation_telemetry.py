"""Where the time actually goes between Generate and a usable image.

Built before the optimisations it exists to judge, because this project has
twice drawn a confident conclusion from a number that turned out to mean
something else:

* Studio was "70% slower than Neo". The two runs were at different
  resolutions. Correcting for that left a 1.4x denoising difference and an
  overhead difference pointing the OTHER way.
* Auto Detail was "4.43x slower". It was the global denoising gap multiplied
  by 2.25x of pixels it should never have been denoising.

Both were visible in per-stage numbers and invisible in a wall-clock total.

**What this records that a progress bar cannot.** A stage's `it/s` covers the
sampler loop and nothing else. The time between two stages -- unloading a
component, moving another back to the device, decoding to pixels for an
upscaler, encoding back to latents -- appears in no bar and is where Studio's
measured advantage over Neo lives (5.31 s against 12.29 s). It has to be
attributable per boundary or it cannot be optimised.

**Effective work, not requested work.** Every stage records the dimensions it
ACTUALLY processed. Auto Detail asking for a face and denoising a 1536x1536
frame was invisible until those two numbers sat next to each other, and the
handoff is explicit that recording effective work is what prevents the next
false comparison.

**Counts, not only durations.** "Did this stage encode the prompt again?" and
"did the text encoder move again?" are the questions Package 1 and Package 2
turn on, and they are answered by a counter rather than a stopwatch.

Studio-owned. Import-safe: no torch at module scope, so a test can read a
record without a device.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

#: The stages of one generation, in the order they run. A stage that never
#: ran is reported as absent rather than as zero -- a zero is a measurement,
#: and "we did not do this" is not.
STAGES = (
    "queue",
    "dispatch",
    # NOT "base". The bracket is around inherited `process_images_inner`,
    # which runs the base denoise, its decode, the upscale AND the Hires
    # denoise as one call when Hires is enabled. Calling that row "base"
    # invites exactly the false conclusion this module exists to prevent --
    # someone would compare it against Neo's base-only `it/s` and find a
    # deficit that is mostly a Hires pass.
    "primary_pipeline",
    "prepare_detail",
    "detail",
    "publish",
)

#: Counted events. These answer "was the work redundant?", which no duration
#: can.
#:
#: NAMES ARE EXACT. `conditioning_setup_calls` counts the times Studio builds
#: a processing object that will run conditioning -- it does NOT count text
#: encoder executions, and it does not count cache misses. Neo's conditioning
#: cache is class-level (`modules/processing.py:251-254`) and Studio cannot
#: see through it without forking, so a name implying encoder executions would
#: be a measurement of something nobody measured.
COUNTERS = (
    "conditioning_setup_calls",
    "component_moves",
    "detail_regions",
)


@dataclass
class StageRecord:
    """One stage: how long, and on what."""

    name: str
    seconds: float
    #: What was ACTUALLY processed, not what was requested.
    width: int = 0
    height: int = 0
    steps: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"stage": self.name, "seconds": round(self.seconds, 3)}
        if self.width or self.height:
            out["size"] = f"{self.width}x{self.height}"
        if self.steps:
            out["steps"] = self.steps
            if self.seconds > 0:
                out["it_s"] = round(self.steps / self.seconds, 2)
        out.update(self.detail)
        return out


class GenerationTelemetry:
    """One job's timeline. Thread-safe; never raises into the generation.

    Every method is a no-op on failure. A telemetry bug must not cost an owner
    a picture, which is the same contract `record_generation` and
    `note_generation` carry.
    """

    def __init__(self, job_id: str = "") -> None:
        self.job_id = job_id
        self._lock = threading.Lock()
        self._began = time.monotonic()
        self._open: dict[str, float] = {}
        self._stages: list[StageRecord] = []
        self._counts: dict[str, int] = {name: 0 for name in COUNTERS}
        self._move_seconds = 0.0
        self._move_by_stage: dict[str, int] = {}
        self._moves: list[dict[str, Any]] = []

    # -- recording ---------------------------------------------------------

    def start(self, stage: str) -> None:
        try:
            with self._lock:
                self._open[stage] = time.monotonic()
        except Exception:  # noqa: BLE001
            return

    def stop(self, stage: str, *, width: int = 0, height: int = 0,
             steps: int = 0, **detail: Any) -> None:
        """Close a stage. Unopened stages are ignored, not invented."""

        try:
            with self._lock:
                began = self._open.pop(stage, None)
                if began is None:
                    return
                self._stages.append(StageRecord(
                    name=stage, seconds=time.monotonic() - began,
                    width=int(width or 0), height=int(height or 0),
                    steps=int(steps or 0), detail=dict(detail),
                ))
        except Exception:  # noqa: BLE001
            return

    def record_move(self, seconds: float, *, components: tuple = (),
                    requested_bytes: int = 0, free_before: int = 0,
                    free_after: int = 0, evicted: list | None = None) -> None:
        """One model move, attributed to whichever stage is open.

        Attribution matters more than the total: a move during
        `prepare_detail` is churn the retention plan can remove, and a move
        during `primary_pipeline` is Neo's own memory management doing its
        job.
        """

        try:
            with self._lock:
                self._counts["component_moves"] += 1
                self._move_seconds += float(seconds)
                stages = sorted(self._open)
                for stage in stages:
                    self._move_by_stage[stage] = (
                        self._move_by_stage.get(stage, 0) + 1)
                self._moves.append({
                    "index": len(self._moves) + 1,
                    "stages": stages,
                    "components": list(components),
                    "requested_mb": round(requested_bytes / 1024 ** 2, 1),
                    "free_before_mb": round(free_before / 1024 ** 2, 1),
                    "free_after_mb": round(free_after / 1024 ** 2, 1),
                    "seconds": round(float(seconds), 3),
                    # THE binding section 14 asks for: this request, and what
                    # it cost another component to satisfy.
                    "evicted": list(evicted or ()),
                })
        except Exception:  # noqa: BLE001
            return

    def count(self, name: str, amount: int = 1) -> None:
        try:
            with self._lock:
                if name in self._counts:
                    self._counts[name] += int(amount)
        except Exception:  # noqa: BLE001
            return

    # -- reading -----------------------------------------------------------

    def elapsed(self) -> float:
        return time.monotonic() - self._began

    def accounted(self) -> float:
        with self._lock:
            return sum(record.seconds for record in self._stages)

    def unaccounted(self) -> float:
        """Wall clock minus the stages. THE NUMBER THAT MATTERS.

        This is model moves, device transfers and anything else nobody put a
        stage around. Studio's measured advantage over Neo is here -- 5.31 s
        against 12.29 s -- so it is reported as a first-class figure rather
        than left to be derived by whoever reads the log.
        """

        return max(0.0, self.elapsed() - self.accounted())

    def describe(self) -> dict[str, Any]:
        """The whole timeline. Safe to log, safe to write to Evidence."""

        with self._lock:
            stages = [record.describe() for record in self._stages]
            counts = dict(self._counts)
        return {
            "job": self.job_id,
            "total_seconds": round(self.elapsed(), 3),
            "accounted_seconds": round(self.accounted(), 3),
            "unaccounted_seconds": round(self.unaccounted(), 3),
            "stages": stages,
            "counts": counts,
            "model_move_seconds": round(self._move_seconds, 3),
            "model_moves_by_stage": dict(self._move_by_stage),
            "moves": list(self._moves),
        }

    def summary(self) -> str:
        """One line per stage, for a console or a log. Aligned to be read."""

        described = self.describe()
        lines = [
            f"job={described['job'] or '-'} "
            f"total={described['total_seconds']:.2f}s "
            f"accounted={described['accounted_seconds']:.2f}s "
            f"unaccounted={described['unaccounted_seconds']:.2f}s"
        ]
        for stage in described["stages"]:
            extra = " ".join(
                f"{key}={value}" for key, value in stage.items()
                if key not in ("stage", "seconds")
            )
            lines.append(f"  {stage['stage']:20} {stage['seconds']:7.3f}s  {extra}")
        counts = " ".join(f"{k}={v}" for k, v in described["counts"].items() if v)
        if counts:
            lines.append(f"  {'counts':20} {counts}")
        for move in described["moves"]:
            where = "/".join(move["stages"]) or "-"
            what = ",".join(move["components"]) or "?"
            lines.append(
                f"  move {move['index']:<15} {move['seconds']:7.3f}s  "
                f"{where} {what} requested={move['requested_mb']}MB "
                f"free {move['free_before_mb']}->{move['free_after_mb']}MB"
            )
            for loss in move["evicted"]:
                lines.append(
                    f"       evicted {loss['component']} "
                    f"freed={loss['freed_mb']}MB "
                    f"remains={loss['remains_mb']}MB "
                    f"{'fully' if loss['fully'] else 'partial'}"
                )
        return "\n".join(lines)


def _component_names(models: Any) -> tuple[str, ...]:
    """The CLASS names of what a move was asked to bring in.

    Class names, never filenames: this reaches a log and an Evidence file,
    and the owner's checkpoint paths are private. `JointTextEncoder` and
    `KModel` identify the component without naming anything of theirs.
    """

    found: list[str] = []
    try:
        for model in (models if isinstance(models, (list, tuple)) else [models]):
            inner = getattr(model, "model", None)
            found.append(type(inner if inner is not None else model).__name__)
    except Exception:  # noqa: BLE001
        return ()
    return tuple(found)


def _residency(memory_management: Any) -> dict[str, int]:
    """What is resident on the device right now, by component, in bytes.

    Read from `memory_management.current_loaded_models` (:424), the backend's
    own list, using the identity it uses itself when it logs an eviction
    (`:599` -> `.model.model.__class__.__name__`). Diffing this across one
    `load_models_gpu` call BINDS a partial unload to the request that caused
    it, which section 14 requires and a log line alone cannot give: the
    "Unloaded partially" message names bytes but not which component lost
    them, and never which incoming request forced the choice.

    Defensive throughout. This walks inherited runtime state and must return a
    partial answer rather than take down the generation it is measuring.
    """

    resident: dict[str, int] = {}
    try:
        loaded = list(getattr(memory_management, "current_loaded_models", ()) or ())
    except Exception:  # noqa: BLE001
        return resident
    for entry in loaded:
        try:
            patcher = getattr(entry, "model", None)
            inner = getattr(patcher, "model", None)
            name = type(inner if inner is not None else patcher).__name__
            size = getattr(patcher, "loaded_size", None)
            resident[name] = resident.get(name, 0) + int(size() if callable(size) else 0)
        except Exception:  # noqa: BLE001
            continue
    return resident


def _evictions(before: dict[str, int], after: dict[str, int]) -> list[dict[str, Any]]:
    """Which components LOST resident bytes across one request."""

    lost: list[dict[str, Any]] = []
    for name, was in before.items():
        now = after.get(name, 0)
        if now < was:
            lost.append({
                "component": name,
                "freed_mb": round((was - now) / 1024 ** 2, 1),
                "remains_mb": round(now / 1024 ** 2, 1),
                "fully": now == 0,
            })
    return lost


def _free_memory(memory_management: Any) -> int:
    """Free VRAM as the BACKEND sees it, not as torch reports it.

    Deliberately the backend's own `get_free_memory`: that is the number its
    unload decisions are made against, and section 10 is explicit that a
    simpler metric must not be substituted for the signal actually used.
    """

    try:
        return int(memory_management.get_free_memory())
    except Exception:  # noqa: BLE001
        return 0


class ModelMoveObserver:
    """Counts the model moves a generation actually performs.

    Every move to the device passes through ONE function --
    `backend.memory_management.load_models_gpu` (:616), which is also what
    emits `Moving model(s) has taken N seconds` (:702-703). So the count and
    the cost are observable by wrapping that one name for the life of a
    generation.

    A WRAPPER, not a fork. The books forbid forking inherited modules where a
    wrapper suffices, and this needs nothing from the inside of the function:
    it needs to know how often it ran and for how long.

    This is the measurement the same-job retention work turns on. "Fewer
    seconds" is arguable; "the text encoder moved twice instead of four times"
    is not, and it survives a noisy machine.

    Restores on the way out, always, including on an exception -- a patched
    module left behind would silently attribute the NEXT generation's moves to
    a telemetry object nobody is reading.
    """

    def __init__(self, telemetry: "GenerationTelemetry") -> None:
        self._telemetry = telemetry
        self._module: Any = None
        self._original: Any = None

    def __enter__(self) -> "ModelMoveObserver":
        try:
            from backend import memory_management

            original = memory_management.load_models_gpu
        except Exception:  # noqa: BLE001 - no backend, nothing to observe
            return self

        telemetry = self._telemetry

        def observed(*args: Any, **kwargs: Any) -> Any:
            # WHAT was asked for, and under what pressure. Section 3 of the
            # churn-trace handoff wants the component, the requested memory
            # and the free memory either side -- so the question "was this
            # unload necessary?" is answered by evidence rather than by a
            # plausible story about memory management.
            models = args[0] if args else kwargs.get("models") or []
            requested = float(
                (args[1] if len(args) > 1 else kwargs.get("memory_required", 0))
                or 0)
            components = _component_names(models)
            free_before = _free_memory(memory_management)
            resident_before = _residency(memory_management)
            began = time.monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                telemetry.record_move(
                    time.monotonic() - began,
                    components=components,
                    requested_bytes=int(requested),
                    free_before=free_before,
                    free_after=_free_memory(memory_management),
                    evicted=_evictions(
                        resident_before, _residency(memory_management)),
                )

        try:
            memory_management.load_models_gpu = observed
        except Exception:  # noqa: BLE001
            return self
        self._module, self._original = memory_management, original
        return self

    def __exit__(self, *_exception: Any) -> None:
        if self._module is None or self._original is None:
            return
        try:
            self._module.load_models_gpu = self._original
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._module = self._original = None


__all__ = ("COUNTERS", "STAGES", "GenerationTelemetry", "ModelMoveObserver",
           "StageRecord")
