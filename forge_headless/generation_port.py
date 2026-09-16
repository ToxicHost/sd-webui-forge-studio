"""The seam between a validated Studio request and real Forge generation.

Same shape as the Phase 2A loader port, for the same reason: validate
everything, then refuse at the last step, so a caller can tell "unsupported
dimensions" from "not authorized". A generic NOT_IMPLEMENTED after successful
validation throws away the information the validation just produced.

The port never accepts a filesystem path. It takes a frozen request and returns
a descriptor the Studio result registry turns into an opaque handle.

Nothing in this module imports Torch, Gradio, or a Forge module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import HeadlessError
from .generation_request import FirstImageRequest, ResidentModel, validate_request
from .headless_progress import HeadlessProgress, JobState


GENERATION_NOT_AUTHORIZED = "HEADLESS_GENERATION_NOT_AUTHORIZED"


@dataclass(frozen=True)
class GenerationOutcome:
    """What a real backend will return: a job, and where its result lives.

    `result_relative_location` is root-relative by construction. The absolute
    path never leaves the backend, and the Studio projection carries only the
    opaque handle the registry mints.
    """

    job_id: str
    request_id: str
    result_relative_location: str
    media_type: str
    width: int
    height: int
    seed: int
    #: What the upscale composite did, for section 9's requested/effective
    #: pair. Flat scalars with inert defaults: the result metadata is asserted
    #: to be scalar-valued, and a generation with no upscale must not have to
    #: invent a decision it never made.
    gpu_tile_composite_requested: bool = False
    upscale_composite_effective: str = ""
    upscale_composite_reason: str = ""
    #: What the Auto Detail slots actually did, as `SlotOutcome` values.
    #:
    #: Carried on the OUTCOME rather than read back off the port, because the
    #: port is per-session and outlives the job. Admission is serial today, so
    #: reading it there would work -- and would quietly start reporting the
    #: previous job's detections the day it is not. That is the exact shape of
    #: `_in_flight_job_id`, which did precisely that in production.
    #:
    #: Empty when no slot ran, so an untouched result carries no section.
    auto_detail: tuple[Any, ...] = ()
    #: The engine's own generation-parameter string for this result.
    #:
    #: Neo builds it inside `process_images_inner` and hands it back on the
    #: `Processed` (`processing.py:1118-1119`, `:1184`); Studio used to drop it
    #: on the floor, which cost the owner both the embedded file metadata and
    #: every page surface that reports what was generated -- the output info
    #: bar, Copy Seed and both Recycle buttons.
    #:
    #: Carried rather than rebuilt. A string assembled here would drift from
    #: the one the engine actually used the first time any extension added a
    #: field to it, and the whole point of the format is that other programs
    #: read it.
    #:
    #: Empty is legitimate: a backend that does not produce one embeds nothing.
    infotext: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "request_id": self.request_id,
            "result_relative_location": self.result_relative_location,
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            # Counts and names, never pixels or paths -- `SlotOutcome.describe`
            # is built for exactly this trip.
            "auto_detail": [
                outcome.describe() for outcome in self.auto_detail
            ],
            "infotext": self.infotext,
        }


class GenerationPort(Protocol):
    """What a real generation backend will have to implement."""

    def generate(
        self, request: FirstImageRequest, progress: HeadlessProgress
    ) -> GenerationOutcome:
        """Run one generation, or raise `HeadlessError`."""
        ...


class PolicyGatedGenerator:
    """The default backend: refuses, after everything else has passed.

    `authorized` is a class attribute so enabling real generation is a visible,
    deliberate edit rather than a state something can fall into.
    """

    authorized = False

    def __init__(self) -> None:
        self.refusals = 0

    def generate(
        self, request: FirstImageRequest, progress: HeadlessProgress
    ) -> GenerationOutcome:
        self.refusals += 1
        # The job is not left mid-flight: it goes to a terminal state before the
        # refusal propagates, so residency and progress stay consistent.
        progress.mark_failed("generation is not authorized in this phase")
        raise HeadlessError(
            GENERATION_NOT_AUTHORIZED,
            "Real generation is not authorized in this phase.",
        )


class GenerationGateway:
    """Validate, initialise progress, hand off to the port, report truthfully."""

    def __init__(self, port: object | None = None) -> None:
        self._port = port if port is not None else PolicyGatedGenerator()
        self._jobs: dict[str, HeadlessProgress] = {}

    @property
    def port(self) -> object:
        return self._port

    def progress_for(self, job_id: str) -> HeadlessProgress:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise HeadlessError(
                "GENERATION_JOB_UNKNOWN", "That job is not known."
            ) from None

    def submit(
        self,
        request: FirstImageRequest,
        model: ResidentModel,
        *,
        options_available: bool,
        progress_installed: bool = True,
        result_root_writable: bool = True,
        generation_authorized: bool = False,
    ) -> GenerationOutcome:
        """The full pipeline. Refusal is last, and only after everything passes."""

        result = validate_request(
            request,
            model,
            options_available=options_available,
            progress_installed=progress_installed,
            result_root_writable=result_root_writable,
            generation_authorized=generation_authorized,
        )
        blocking = [
            problem
            for problem in result.problems
            if problem["code"] != GENERATION_NOT_AUTHORIZED
        ]
        if blocking:
            # A real validation failure never reaches the port, and never
            # creates a job -- so nothing has to be cleaned up.
            raise HeadlessError(blocking[0]["code"], blocking[0]["detail"])

        progress = HeadlessProgress(
            request.request_id, preview_enabled=request.preview_enabled
        )
        self._jobs[request.request_id] = progress
        progress.advance_to(JobState.LOADING)
        progress.advance_to(JobState.CONDITIONING)

        if progress.cancellation_requested:
            progress.mark_cancelled()
            raise HeadlessError(
                "GENERATION_CANCELLED", "The job was cancelled before sampling."
            )

        return self._port.generate(request, progress)  # type: ignore[attr-defined]

    def cancel(self, job_id: str) -> bool:
        return self.progress_for(job_id).request_cancellation()


class RecordingGenerator:
    """Test-only backend. Records the request, drives progress, never generates.

    It can emit a *synthetic* descriptor so the result seam can be exercised end
    to end, but it computes no pixels: the PNG a test registers is a fixture
    written by the test and marked as such.
    """

    authorized = False

    def __init__(
        self,
        *,
        outcome: GenerationOutcome | None = None,
        cancel_at_step: int | None = None,
        total_steps: int = 12,
        fail_with: str | None = None,
    ) -> None:
        self.requests: list[FirstImageRequest] = []
        self.snapshots: list[dict[str, Any]] = []
        self.denoise_calls = 0
        self.decode_calls = 0
        self._outcome = outcome
        self._cancel_at = cancel_at_step
        self._total = total_steps
        self._fail_with = fail_with

    def generate(
        self, request: FirstImageRequest, progress: HeadlessProgress
    ) -> GenerationOutcome:
        self.requests.append(request)
        progress.set_total_steps(self._total)
        progress.advance_to(JobState.SAMPLING)

        for step in range(1, self._total + 1):
            if progress.cancellation_requested:
                progress.mark_cancelled()
                raise HeadlessError(
                    "GENERATION_CANCELLED", "The job was cancelled during sampling."
                )
            progress.report_step(step)
            self.snapshots.append(progress.snapshot().to_dict())
            if self._cancel_at is not None and step == self._cancel_at:
                progress.request_cancellation()

        if self._fail_with is not None:
            progress.mark_failed(self._fail_with)
            raise HeadlessError("GENERATION_BACKEND_FAILED", self._fail_with)

        progress.advance_to(JobState.DECODING)
        progress.advance_to(JobState.PUBLISHING)
        if self._outcome is None:
            progress.mark_failed("no synthetic outcome was configured")
            raise HeadlessError(
                "GENERATION_NO_RESULT", "The recording backend produced no result."
            )
        progress.mark_completed()
        return self._outcome


__all__ = (
    "GENERATION_NOT_AUTHORIZED",
    "GenerationGateway",
    "GenerationOutcome",
    "GenerationPort",
    "PolicyGatedGenerator",
    "RecordingGenerator",
)
