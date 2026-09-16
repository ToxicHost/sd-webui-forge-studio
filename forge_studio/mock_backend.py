"""Deterministic mock backend for the standalone Studio Alpha S0."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Callable

from .backend import BackendAdapter
from .contracts import (
    BackendStatus,
    CancellationResult,
    GeneratedResult,
    GenerationJobIdentity,
    GenerationRequest,
    AttentionBackend,
    DeviceType,
    DtypePolicy,
    JobState,
    ModelCapability,
    ModelResidency,
    ModelSummary,
    ProgressEvent,
    StructuredError,
    StudioError,
)


@dataclass
class _MockJob:
    request: GenerationRequest
    events: tuple[ProgressEvent, ...]
    started_at: float
    terminal_event: ProgressEvent | None = None
    result: GeneratedResult | None = None


class MockBackend(BackendAdapter):
    """Predictable in-memory lifecycle with optional workspace persistence."""

    MODELS = (
        ModelSummary(
            model_id="studio-mock-illustration-v1",
            name="Studio Mock Illustration",
            description="Deterministic Alpha S0 illustration placeholder.",
            is_mock=True,
        ),
        ModelSummary(
            model_id="studio-mock-photo-v1",
            name="Studio Mock Photo",
            description="Deterministic Alpha S0 photographic placeholder.",
            is_mock=True,
        ),
    )

    def __init__(
        self,
        *,
        result_directory: Path | None = None,
        clock: Callable[[], float] | None = None,
        event_interval_seconds: float = 0.1,
    ) -> None:
        if event_interval_seconds <= 0:
            raise ValueError("event_interval_seconds must be greater than zero")
        self._result_directory = result_directory
        self._clock = clock or monotonic
        self._wait_for_progress = clock is None
        self._event_interval_seconds = float(event_interval_seconds)
        self._jobs: dict[str, _MockJob] = {}
        self._next_job = 1
        self._resident_model_id: str | None = None
        self._stopped = False
        self._lock = Lock()

    SUPPORTED_GENERATION_PARAMETERS = frozenset()

    def get_backend_status(self) -> BackendStatus:
        with self._lock:
            stopped = self._stopped
            resident = self._resident_model_id
        return BackendStatus(
            backend_id="studio-alpha-mock",
            state="stopped" if stopped else "ready",
            ready=not stopped,
            is_mock=True,
            message=(
                "Deterministic mock backend stopped."
                if stopped
                else "Deterministic mock backend ready."
            ),
            model_loaded=resident is not None,
            cuda_initialized=False,
            network_access=False,
        )

    def list_models(self) -> tuple[ModelSummary, ...]:
        return self.MODELS

    def supported_generation_parameters(self) -> frozenset[str]:
        return self.SUPPORTED_GENERATION_PARAMETERS

    def load_model(self, model_id: str) -> ModelResidency:
        known = {model.model_id for model in self.MODELS}
        if not isinstance(model_id, str) or model_id not in known:
            raise StudioError(
                StructuredError(
                    code="MODEL_NOT_AVAILABLE",
                    message="That Studio model is unavailable.",
                    field="model_id",
                )
            )
        with self._lock:
            if self._stopped:
                raise StudioError(
                    StructuredError(
                        code="BACKEND_STOPPED",
                        message="The Studio backend has shut down.",
                    )
                )
            self._resident_model_id = model_id
        return self._residency(model_id, "Mock model loaded.")

    def unload_model(self) -> ModelResidency:
        with self._lock:
            self._resident_model_id = None
        return self._residency(None, "No mock model is loaded.")

    def get_current_model(self) -> ModelResidency:
        with self._lock:
            resident = self._resident_model_id
        return self._residency(
            resident,
            "Mock model loaded." if resident else "No mock model is loaded.",
        )

    def get_capability(
        self,
        model_id: str,
        operation: str,
    ) -> ModelCapability:
        known = {model.model_id for model in self.MODELS}
        if not isinstance(model_id, str) or model_id not in known:
            raise StudioError(
                StructuredError(
                    code="MODEL_NOT_AVAILABLE",
                    message="That Studio model is unavailable.",
                    field="model_id",
                )
            )
        if operation != "txt2img":
            raise StudioError(
                StructuredError(
                    code="OPERATION_NOT_SUPPORTED",
                    message="The mock backend only supports txt2img.",
                    field="operation",
                )
            )
        # The mock imposes no alignment and no maximum, and never rounds. This
        # is its real behaviour, not a placeholder.
        #
        # The device values are equally honest: the mock renders SVG strings
        # with no tensor library, so it runs on the CPU at fp32 with no
        # attention implementation of its own. It reports CPU because that is
        # true, not because of the host it happens to run on.
        return ModelCapability(
            model_id=model_id,
            operation=operation,
            dimension_alignment=1,
            minimum_dimension=1,
            maximum_dimension=None,
            maximum_pixels=None,
            normalizes_dimensions=False,
            is_mock=True,
            device_type=DeviceType.CPU,
            dtype_policy=DtypePolicy.FP32,
            attention_backend=AttentionBackend.BACKEND_DEFAULT,
        )

    def shutdown(self) -> None:
        with self._lock:
            self._stopped = True
            self._resident_model_id = None
            for job in self._jobs.values():
                if job.terminal_event is None:
                    job.terminal_event = ProgressEvent(
                        job.events[0].job_id,
                        JobState.CANCELLED,
                        len(job.events),
                        0,
                        "Studio backend shut down.",
                    )
            self._jobs.clear()

    @staticmethod
    def _residency(model_id: str | None, message: str) -> ModelResidency:
        return ModelResidency(
            model_id=model_id,
            loaded=model_id is not None,
            is_mock=True,
            message=message,
        )

    def submit_generation(
        self,
        request: GenerationRequest,
    ) -> GenerationJobIdentity:
        with self._lock:
            job_id = f"mock-job-{self._next_job:04d}"
            self._next_job += 1
            events = self._events_for(job_id, request)
            self._jobs[job_id] = _MockJob(
                request=request,
                events=events,
                started_at=self._clock(),
            )
            return GenerationJobIdentity(job_id, JobState.QUEUED)

    def poll_or_stream_progress(self, job_id: str) -> ProgressEvent:
        wait_seconds = 0.0
        with self._lock:
            job = self._job(job_id)
            if job.terminal_event is not None:
                return job.terminal_event
            now = self._clock()
            event = self._event_at(job, now)
            if self._wait_for_progress and event.state not in {
                JobState.COMPLETED,
                JobState.CANCELLED,
                JobState.FAILED,
            }:
                next_transition = (
                    job.started_at
                    + (event.sequence + 1) * self._event_interval_seconds
                )
                wait_seconds = max(0.0, next_transition - now)
        if wait_seconds:
            sleep(wait_seconds)
        with self._lock:
            job = self._job(job_id)
            if job.terminal_event is not None:
                return job.terminal_event
            return self._event_at(job, self._clock())

    def cancel_generation(self, job_id: str) -> CancellationResult:
        with self._lock:
            job = self._job(job_id)
            if job.terminal_event is not None:
                return CancellationResult(
                    job_id=job_id,
                    cancelled=False,
                    state=job.terminal_event.state,
                    message="Job is already terminal.",
                )
            current = self._event_at(job, self._clock())
            if current.state in {
                JobState.COMPLETED,
                JobState.CANCELLED,
                JobState.FAILED,
            }:
                return CancellationResult(
                    job_id=job_id,
                    cancelled=False,
                    state=current.state,
                    message="Job is already terminal.",
                )
            cancelled = ProgressEvent(
                job_id=job_id,
                state=JobState.CANCELLED,
                sequence=current.sequence + 1,
                progress=current.progress,
                message="Mock generation cancelled.",
            )
            job.terminal_event = cancelled
            return CancellationResult(
                job_id=job_id,
                cancelled=True,
                state=JobState.CANCELLED,
                message=cancelled.message,
            )

    def get_result(self, job_id: str) -> GeneratedResult:
        with self._lock:
            job = self._job(job_id)
            current = (
                job.terminal_event
                if job.terminal_event is not None
                else self._event_at(job, self._clock())
            )
            if current.state is JobState.COMPLETED and job.result is None:
                job.result = self._build_result(job_id, job.request)
            if job.result is None:
                raise StudioError(
                    StructuredError(
                        code="RESULT_NOT_AVAILABLE",
                        message=(
                            f"Job is {current.state.value}; "
                            "no result is available."
                        ),
                    )
                )
            return job.result

    def _event_at(self, job: _MockJob, now: float) -> ProgressEvent:
        elapsed = max(0.0, now - job.started_at)
        index = min(
            int(elapsed / self._event_interval_seconds),
            len(job.events) - 1,
        )
        return job.events[index]

    def _job(self, job_id: str) -> _MockJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise StudioError(
                StructuredError(
                    code="JOB_NOT_FOUND",
                    message="The requested Studio job does not exist.",
                )
            ) from exc

    @staticmethod
    def _step_of(percent: int, total_steps: int) -> int:
        """Map a progress percent onto this request's own step counter."""

        return max(0, min(total_steps, round(percent * total_steps / 100)))

    @classmethod
    def _events_for(
        cls,
        job_id: str,
        request: GenerationRequest,
    ) -> tuple[ProgressEvent, ...]:
        total = request.steps

        def event(
            sequence: int,
            state: JobState,
            percent: int,
            message: str,
            error: StructuredError | None = None,
        ) -> ProgressEvent:
            return ProgressEvent(
                job_id,
                state,
                sequence,
                percent,
                message,
                error,
                step=cls._step_of(percent, total),
                total_steps=total,
            )

        base = (
            event(0, JobState.QUEUED, 0, "Queued."),
            event(
                1,
                JobState.RUNNING,
                12,
                "Preparing deterministic canvas.",
            ),
        )
        if request.positive_prompt == "__mock_fail__":
            error = StructuredError(
                code="MOCK_CONTROLLED_FAILURE",
                message="Controlled mock backend failure.",
                field="positive_prompt",
            )
            return base + (
                event(2, JobState.FAILED, 25, error.message, error),
            )
        return base + (
            event(2, JobState.RUNNING, 38, "Composing mock geometry."),
            event(3, JobState.RUNNING, 68, "Applying deterministic color."),
            event(4, JobState.RUNNING, 92, "Finalizing metadata."),
            event(5, JobState.COMPLETED, 100, "Mock generation completed."),
        )

    def _build_result(
        self,
        job_id: str,
        request: GenerationRequest,
    ) -> GeneratedResult:
        request_json = json.dumps(
            request.to_dict(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        request_digest = sha256(request_json.encode("utf-8")).hexdigest()
        colors = (
            f"#{request_digest[0:6]}",
            f"#{request_digest[6:12]}",
            f"#{request_digest[12:18]}",
        )
        prompt = escape(request.positive_prompt.strip()[:72])
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{request.width}" height="{request.height}" '
            f'viewBox="0 0 {request.width} {request.height}">'
            "<defs><linearGradient id=\"g\" x1=\"0\" y1=\"0\" "
            "x2=\"1\" y2=\"1\">"
            f'<stop stop-color="{colors[0]}"/>'
            f'<stop offset=".55" stop-color="{colors[1]}"/>'
            f'<stop offset="1" stop-color="{colors[2]}"/>'
            "</linearGradient></defs>"
            f'<rect width="100%" height="100%" fill="url(#g)"/>'
            '<rect x="6%" y="8%" width="88%" height="84%" rx="28" '
            'fill="#0e0e11" fill-opacity=".72" stroke="#ffffff" '
            'stroke-opacity=".2"/>'
            '<text x="10%" y="20%" fill="#ffffff" font-size="28" '
            'font-family="sans-serif" font-weight="700">'
            "FORGE STUDIO · ALPHA S0</text>"
            '<text x="10%" y="30%" fill="#d8d6e2" font-size="20" '
            f'font-family="sans-serif">{prompt}</text>'
            '<text x="10%" y="86%" fill="#b8b2c2" font-size="16" '
            f'font-family="monospace">seed {request.seed} · '
            f'{request.width}×{request.height} · mock only</text>'
            "</svg>"
        )
        image_bytes = svg.encode("utf-8")
        image_digest = sha256(image_bytes).hexdigest()
        metadata: dict[str, object] = {
            "schema_version": "studio-alpha-result/v1",
            "backend_id": "studio-alpha-mock",
            "fixture_kind": "deterministic-programmatic-svg",
            "job_id": job_id,
            "model_id": request.model_id,
            "seed": request.seed,
            "steps": request.steps,
            "cfg_scale": float(request.cfg_scale),
            "width": request.width,
            "height": request.height,
            "request_sha256": f"sha256:{request_digest}",
            "image_sha256": f"sha256:{image_digest}",
        }
        metadata_path, output_path = self._persist_result(
            job_id,
            image_bytes,
            metadata,
        )
        return GeneratedResult(
            job_id=job_id,
            state=JobState.COMPLETED,
            mime_type="image/svg+xml",
            image_data_url=(
                "data:image/svg+xml;base64,"
                + base64.b64encode(image_bytes).decode("ascii")
            ),
            metadata=metadata,
            output_path=output_path,
            metadata_path=metadata_path,
        )

    def _persist_result(
        self,
        job_id: str,
        image_bytes: bytes,
        metadata: dict[str, object],
    ) -> tuple[str | None, str | None]:
        if self._result_directory is None:
            return None, None
        self._result_directory.mkdir(parents=True, exist_ok=True)
        metadata_target = self._result_directory / f"{job_id}.json"
        image_target = self._result_directory / f"{job_id}.svg"
        image_target.write_bytes(image_bytes)
        metadata_target.write_text(
            json.dumps(
                metadata,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        # Backend ownership records: real resolved filesystem paths. These are
        # never browser URLs -- the presentation strips them, and delivery goes
        # through an opaque handle minted by the application.
        return (
            str(metadata_target.resolve()),
            str(image_target.resolve()),
        )
