"""Owned, Gradio-free progress and cancellation for one headless generation.

Forge drives progress through `modules.shared.state`, a process-global singleton
built for a single-user UI: it holds the current image, live-preview counters,
and server restart commands alongside the step counter. Studio needs the step
counter and a cancellation flag, per job, without the rest.

So this module owns the progress model, and a deliberately narrow bridge exposes
only the attributes retained Forge actually touches during generation. The bridge
writes through to the owned model; the owned model never depends on the bridge.

Three rules the design enforces rather than documents:

* **no fake progress.** `fraction` is `None` until the backend has reported a
  real total. A spinner that invents 37% is worse than one that says "unknown";
* **terminal states are terminal.** Once completed, cancelled, or failed, a job
  cannot re-enter a running state -- an out-of-order backend callback cannot
  resurrect a cancelled job;
* **cancellation is cooperative and explicit.** Requesting it sets a flag the
  sampler loop observes at a step boundary. Nothing is killed mid-tensor.

Standard library only: no Gradio, no Torch, no UI singleton.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class JobState(str, Enum):
    """Lifecycle of one generation job."""

    QUEUED = "queued"
    LOADING = "loading"
    CONDITIONING = "conditioning"
    SAMPLING = "sampling"
    #: The Hires second pass. TWO states rather than one, because the
    #: preparation -- upscaling the latent, or decoding and re-encoding
    #: through an image upscaler -- is where a 4x pass spends real time
    #: with the step counter frozen. One state would leave the owner
    #: watching a still bar and reasonably concluding it had hung.
    HIRES_PREPARING = "hires_preparing"
    HIRES_SAMPLING = "hires_sampling"
    #: One state per Auto Detail slot, not one shared state with a counter.
    #:
    #: The slots are separate passes over separate detectors, and a job that
    #: says "Auto Detail" for ninety seconds tells the owner nothing about
    #: whether it is stuck. Three states also make the ALLOWED_TRANSITIONS
    #: table state the pipeline order rather than leave it to a variable.
    AUTO_DETAIL_1 = "auto_detail_1"
    AUTO_DETAIL_2 = "auto_detail_2"
    AUTO_DETAIL_3 = "auto_detail_3"
    DECODING = "decoding"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_STATES = frozenset({JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED})

#: The only forward transitions allowed. Anything else is a programming error,
#: not a state to be silently accepted.
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.LOADING, JobState.CONDITIONING, JobState.CANCELLED, JobState.FAILED}),
    JobState.LOADING: frozenset({JobState.CONDITIONING, JobState.CANCELLED, JobState.FAILED}),
    JobState.CONDITIONING: frozenset({JobState.SAMPLING, JobState.CANCELLED, JobState.FAILED}),
    # SAMPLING may go straight to DECODING (Hires off) or through the second
    # pass. Both are forward moves; neither is a state the other can skip
    # backwards into.
    # Any enabled Auto Detail slot may follow the last sampling pass, and any
    # of them may be SKIPPED -- an owner running only slot 2 goes straight
    # there, and slot 3 alone goes straight to it. So each sampling state
    # reaches all three, and each slot reaches the ones after it. The table
    # states the pipeline order; nothing may move backwards through it.
    JobState.SAMPLING: frozenset({JobState.HIRES_PREPARING, JobState.AUTO_DETAIL_1, JobState.AUTO_DETAIL_2, JobState.AUTO_DETAIL_3, JobState.DECODING, JobState.CANCELLED, JobState.FAILED}),
    JobState.HIRES_PREPARING: frozenset({JobState.HIRES_SAMPLING, JobState.CANCELLED, JobState.FAILED}),
    JobState.HIRES_SAMPLING: frozenset({JobState.AUTO_DETAIL_1, JobState.AUTO_DETAIL_2, JobState.AUTO_DETAIL_3, JobState.DECODING, JobState.CANCELLED, JobState.FAILED}),
    JobState.AUTO_DETAIL_1: frozenset({JobState.AUTO_DETAIL_2, JobState.AUTO_DETAIL_3, JobState.DECODING, JobState.CANCELLED, JobState.FAILED}),
    JobState.AUTO_DETAIL_2: frozenset({JobState.AUTO_DETAIL_3, JobState.DECODING, JobState.CANCELLED, JobState.FAILED}),
    JobState.AUTO_DETAIL_3: frozenset({JobState.DECODING, JobState.CANCELLED, JobState.FAILED}),
    JobState.DECODING: frozenset({JobState.PUBLISHING, JobState.CANCELLED, JobState.FAILED}),
    JobState.PUBLISHING: frozenset({JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED}),
    JobState.COMPLETED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.FAILED: frozenset(),
}

STAGE_LABELS: dict[JobState, str] = {
    JobState.QUEUED: "Queued",
    JobState.LOADING: "Loading model",
    JobState.CONDITIONING: "Encoding prompt",
    JobState.SAMPLING: "Sampling",
    JobState.HIRES_PREPARING: "Preparing Hires pass",
    JobState.HIRES_SAMPLING: "Hires sampling",
    JobState.AUTO_DETAIL_1: "Auto Detail 1",
    JobState.AUTO_DETAIL_2: "Auto Detail 2",
    JobState.AUTO_DETAIL_3: "Auto Detail 3",
    JobState.DECODING: "Decoding",
    JobState.PUBLISHING: "Publishing result",
    JobState.COMPLETED: "Completed",
    JobState.CANCELLED: "Cancelled",
    JobState.FAILED: "Failed",
}


#: Seconds between two decoded preview frames, at the most.
#:
#: The decode runs on the SAMPLING thread, between two steps of the owner's
#: real image, so its cost is paid out of the thing they are waiting for. Neo
#: throttles its own preview by STEP count (`show_progress_every_n_steps`),
#: which is the wrong unit here: a step is milliseconds at 512x512 and seconds
#: at 2048x2048, so a step-based budget is either useless or ruinous depending
#: on the size. Wall clock is the same cost everywhere.
PREVIEW_MIN_INTERVAL_SECONDS = 0.25

#: States in which a preview frame means anything. Both SAMPLING states, not
#: just the first: the Hires second pass is exactly where a job looks hung, and
#: `preview_available` reported False through the whole of it.
PREVIEW_STATES = frozenset({JobState.SAMPLING, JobState.HIRES_SAMPLING})


class ProgressError(Exception):
    """An illegal transition. Raised rather than absorbed."""


@dataclass(frozen=True)
class ProgressSnapshot:
    """One non-consuming observation. Reading never advances anything."""

    job_id: str
    state: JobState
    step: int
    total_steps: int | None
    fraction: float | None
    stage_label: str
    preview_available: bool
    cancellation_requested: bool
    terminal: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "step": self.step,
            "total_steps": self.total_steps,
            "fraction": self.fraction,
            "stage_label": self.stage_label,
            "preview_available": self.preview_available,
            "cancellation_requested": self.cancellation_requested,
            "terminal": self.terminal,
            "error": self.error,
        }


class HeadlessProgress:
    """Thread-safe progress for one job."""

    def __init__(self, job_id: str, *, preview_enabled: bool = False) -> None:
        self._job_id = job_id
        self._state = JobState.QUEUED
        self._step = 0
        self._total: int | None = None
        self._cancel = False
        self._error: str | None = None
        #: Set by the port when the job asked for a second pass. Nothing
        #: transitions into the Hires states without it.
        self._hires_expected = False
        self._preview_enabled = bool(preview_enabled)
        #: The most recently decoded frame as a data URL, its id, and when it
        #: was taken. The frame is NOT part of `ProgressSnapshot`: that
        #: structure is small, loggable and serialised into every job record,
        #: and a 25 kB base64 string does not belong in any of those. It
        #: travels on the progress socket alone, through `preview_frame()`.
        self._preview_frame: str | None = None
        self._preview_id = 0
        self._preview_at = 0.0
        self._started = time.monotonic()
        self._lock = threading.RLock()

    # -- reads -------------------------------------------------------------

    @property
    def job_id(self) -> str:
        return self._job_id

    @property
    def state(self) -> JobState:
        with self._lock:
            return self._state

    @property
    def terminal(self) -> bool:
        with self._lock:
            return self._state in TERMINAL_STATES

    @property
    def cancellation_requested(self) -> bool:
        with self._lock:
            return self._cancel

    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            fraction: float | None = None
            if self._total:
                fraction = round(min(1.0, max(0.0, self._step / self._total)), 4)
            elif self._state is JobState.COMPLETED:
                fraction = 1.0
            return ProgressSnapshot(
                job_id=self._job_id,
                state=self._state,
                step=self._step,
                total_steps=self._total,
                fraction=fraction,
                stage_label=STAGE_LABELS[self._state],
                # A frame that EXISTS, not merely one that was permitted.
                # This used to read `enabled and SAMPLING and step > 0`, which
                # was true for the whole of every job whose owner had the
                # toggle on -- including all the jobs where no frame was ever
                # produced, because nothing produced any.
                preview_available=self._preview_frame is not None,
                cancellation_requested=self._cancel,
                terminal=self._state in TERMINAL_STATES,
                error=self._error,
            )

    def preview_frame(self) -> tuple[int, str | None]:
        """The latest decoded frame and its id, for the progress socket only.

        The id increments per frame so a client can tell a new one from a
        retransmitted message. Deliberately not on the snapshot: see the
        `_preview_frame` note in `__init__`.
        """

        with self._lock:
            return self._preview_id, self._preview_frame

    # -- writes ------------------------------------------------------------

    def offer_latent(self, latent: Any) -> None:
        """Decode a preview frame from the in-flight latent, at most so often.

        Called from the state bridge every time Neo publishes a latent, which
        is once per sampler step. Almost every call returns immediately.

        The throttle slot is CLAIMED under the lock and the decode runs
        outside it. Holding the lock across the decode would block
        `snapshot()`, which the HTTP thread calls to answer `/api/jobs` --
        the progress route would stall behind the very frame it exists to
        report on.
        """

        if latent is None:
            return
        now = time.monotonic()
        with self._lock:
            if not self._preview_enabled:
                return
            if self._state not in PREVIEW_STATES:
                return
            if now - self._preview_at < PREVIEW_MIN_INTERVAL_SECONDS:
                return
            self._preview_at = now

        from .preview_frame import encode_preview

        frame = encode_preview(latent)
        if frame is None:
            return
        with self._lock:
            # Terminal is terminal here too. A cancel or a failure can land
            # while a decode is in flight, and a job that has stopped must not
            # start showing frames again afterwards.
            if self._state in TERMINAL_STATES:
                return
            self._preview_frame = frame
            self._preview_id += 1

    def _release_preview(self) -> None:
        """Drop the retained frame. Called on every terminal transition.

        Held frames are ~25 kB each and job records outlive their jobs, so a
        long session would accumulate one stale preview per generation for a
        picture nobody is waiting for any more.
        """

        self._preview_frame = None

    def advance_to(self, state: JobState) -> None:
        """Move to `state`, or raise. Terminal states never regress."""
        with self._lock:
            if state is self._state:
                return
            if self._state in TERMINAL_STATES:
                raise ProgressError(
                    f"job {self._job_id} is {self._state.value} and cannot become {state.value}"
                )
            if state not in ALLOWED_TRANSITIONS[self._state]:
                raise ProgressError(
                    f"illegal transition {self._state.value} -> {state.value}"
                )
            self._state = state
            if state is JobState.SAMPLING:
                self._step = 0
            if state in TERMINAL_STATES:
                self._release_preview()

    def expect_hires_pass(self) -> None:
        """Declare that a SECOND sampling pass is coming.

        Set by the port when `enable_hr` is on, so a base-only job can never
        transition into the Hires states by accident. Without it the signal
        below -- "a new step budget arrived while sampling" -- would be a guess
        about what the engine meant.
        """

        with self._lock:
            self._hires_expected = True

    def set_total_steps(self, total: int) -> None:
        """Declare the real step count. Only a backend may call this.

        Neo calls this at the start of EVERY pass (`Sampler.launch_sampling`
        writes `state.sampling_steps`), so on a Hires job it arrives twice.
        The second arrival is the second pass beginning, and it is where the
        step counter has to be rewound -- see the comment below.
        """

        with self._lock:
            if total < 1:
                raise ProgressError("total steps must be at least one")
            if self._hires_expected and self._state is JobState.HIRES_PREPARING:
                # `report_step` is monotonic ON PURPOSE: an out-of-order
                # callback within one pass must not rewind the bar. Across
                # passes that same rule is wrong. The Hires pass reports steps
                # 0..N, every one of them below the base pass's final step, so
                # every one is DISCARDED and the fraction stays clamped at 1.0.
                # The owner would watch a full bar for the entire second pass,
                # which on a 4x target is most of the job.
                #
                # Note the budget SHRINKS rather than grows, which is what
                # makes this easy to misread as "already finished": Neo calls
                # launch_sampling(t_enc + 1) where t_enc is
                # denoising_strength * steps, so 0.5 over 6 steps budgets 4.
                self._step = 0
                self.advance_to(JobState.HIRES_SAMPLING)
            self._total = int(total)

    def report_step(self, step: int) -> None:
        """Record a real completed step. Monotonic; never fabricated."""
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            value = int(step)
            if value < self._step:
                return  # out-of-order callback; keep the furthest point reached
            if self._total is not None:
                value = min(value, self._total)
            self._step = value
            # The base pass has finished sampling and the upscale is about to
            # run. That gap -- latent resize, or a full decode/upscale/encode
            # through an image model -- is real time with no step callbacks at
            # all, and it is the one place a Hires job looks hung. Naming it is
            # the whole reason HIRES_PREPARING exists as a state rather than
            # being folded into HIRES_SAMPLING.
            if (
                self._hires_expected
                and self._state is JobState.SAMPLING
                and self._total is not None
                and value + 1 >= self._total
            ):
                # `value + 1`, not `value`. Neo reports sampling steps
                # ZERO-BASED: an 8-step pass writes 0..7 and never writes 8,
                # so `value >= total` was unsatisfiable for every step count
                # and this transition had never fired in production.
                #
                # HIRES_PREPARING was therefore unreachable, and because
                # `set_total_steps` only rewinds the counter when it is
                # ALREADY in HIRES_PREPARING, the second pass never entered
                # HIRES_SAMPLING either. A Hires job stayed in SAMPLING from
                # start to finish while the bar sat full for the entire
                # second pass.
                #
                # Forty unit tests covered this state machine and all passed:
                # they called `report_step(total)` directly, which satisfies
                # the old condition and is a value Neo never sends. The state
                # machine was correct and nothing drove it -- found on the
                # first live re-run, exactly as the handoff predicted.
                #
                # `+ 1` reads as "no further step can arrive in this budget",
                # and is true whether the source counts from zero or from one.
                self.advance_to(JobState.HIRES_PREPARING)

    def request_cancellation(self) -> bool:
        """Ask the job to stop at its next safe boundary. Idempotent."""
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False
            self._cancel = True
            return True

    def mark_cancelled(self) -> None:
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            self._state = JobState.CANCELLED
            self._release_preview()

    def mark_failed(self, error: str) -> None:
        """Terminal failure with a sanitized message -- never a traceback."""
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            self._state = JobState.FAILED
            self._error = str(error)[:200]
            self._release_preview()

    def mark_completed(self) -> None:
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            self._state = JobState.COMPLETED
            if self._total is not None:
                self._step = self._total
            self._release_preview()

    @property
    def elapsed_seconds(self) -> float:
        return round(time.monotonic() - self._started, 3)


class ForgeStateBridge:
    """The narrowest `shared.state` retained Forge actually uses in generation.

    Thirteen attributes were found by AST scan of the generation closure. This
    exposes exactly those, writing through to the owned progress model, so Forge
    code runs unmodified while Studio keeps one source of truth.

    Deliberately absent: `current_image`, `id_live_preview`, `server_command`,
    `need_restart`, and the rest of the UI-facing surface. If retained code ever
    reaches for one, the `AttributeError` names it rather than silently doing
    nothing.
    """

    #: Exactly what the generation closure touches, from the AST scan.
    SUPPORTED = (
        "current_latent",
        "interrupted",
        "job",
        "job_count",
        "job_no",
        "job_timestamp",
        "preview_step",
        "processing_has_refined_job_count",
        "sampling_step",
        "sampling_steps",
        "skipped",
        "stopping_generation",
        "textinfo",
        "time_start",
    )

    def __init__(self, progress: HeadlessProgress) -> None:
        object.__setattr__(self, "_progress", progress)
        object.__setattr__(self, "_values", {
            "current_latent": None,
            "job": "",
            "job_count": 1,
            "job_no": 0,
            "job_timestamp": time.strftime("%Y%m%d%H%M%S"),
            "preview_step": 0,
            "processing_has_refined_job_count": False,
            "textinfo": None,
            "time_start": time.time(),
        })

    # -- Forge reads -------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        progress: HeadlessProgress = object.__getattribute__(self, "_progress")
        if name == "interrupted":
            return progress.cancellation_requested
        if name == "stopping_generation":
            return progress.cancellation_requested
        if name == "skipped":
            return False
        if name == "sampling_step":
            return progress.snapshot().step
        if name == "sampling_steps":
            return progress.snapshot().total_steps or 0
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        raise AttributeError(
            f"the headless state bridge does not provide {name!r}; retained "
            "Forge reached for a UI-facing field this generation path should "
            "not need"
        )

    # -- Forge writes ------------------------------------------------------

    def __setattr__(self, name: str, value: Any) -> None:
        progress: HeadlessProgress = object.__getattribute__(self, "_progress")
        if name == "sampling_step":
            progress.report_step(int(value))
            return
        if name == "sampling_steps":
            if value:
                progress.set_total_steps(int(value))
            return
        if name in ("interrupted", "stopping_generation"):
            if value:
                progress.request_cancellation()
            return
        object.__getattribute__(self, "_values")[name] = value
        if name == "current_latent":
            # Neo writes this on every sampler step, through
            # `sd_samplers_common.store_latent`. It was stored and never read
            # by anything except `failure_cleanup`, which exists to drop it --
            # so the pixels behind Live Preview were arriving here, once per
            # step, and being thrown away while the page showed a toggle for
            # them.
            #
            # Offered rather than decoded: `offer_latent` throttles, and does
            # nothing at all unless this job asked for previews. The tensor is
            # NOT retained beyond this call -- it is already stored in
            # `_values` above, and holding a second reference to a live CUDA
            # tensor is the leak `_BRIDGE_TENSOR_FIELDS` was added to clean up.
            progress.offer_latent(value)

    # -- Forge calls -------------------------------------------------------

    def nextjob(self) -> None:
        values = object.__getattribute__(self, "_values")
        values["job_no"] = values.get("job_no", 0) + 1
        values["preview_step"] = 0

    def skip(self) -> None:
        """Skipping is not supported for a one-image job; cancel instead."""
        object.__getattribute__(self, "_progress").request_cancellation()

    def interrupt(self) -> None:
        object.__getattribute__(self, "_progress").request_cancellation()

    def stop_generating(self) -> None:
        object.__getattribute__(self, "_progress").request_cancellation()

    def begin(self, job: str = "(unknown)") -> None:
        values = object.__getattribute__(self, "_values")
        values["job"] = job
        values["job_no"] = 0
        values["preview_step"] = 0
        values["time_start"] = time.time()

    def end(self) -> None:
        values = object.__getattribute__(self, "_values")
        values["job"] = ""

    def set_current_image(self) -> None:
        """No live preview on the headless path. Explicitly a no-op."""

    def dict(self) -> dict[str, Any]:
        return object.__getattribute__(self, "_progress").snapshot().to_dict()


__all__ = (
    "ALLOWED_TRANSITIONS",
    "STAGE_LABELS",
    "TERMINAL_STATES",
    "ForgeStateBridge",
    "HeadlessProgress",
    "JobState",
    "ProgressError",
    "ProgressSnapshot",
)
