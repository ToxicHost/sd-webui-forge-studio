"""Backend adapter boundary owned by Studio."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .contracts import (
    BackendStatus,
    CancellationResult,
    GeneratedResult,
    GenerationJobIdentity,
    GenerationRequest,
    ModelCapability,
    ModelResidency,
    ModelSummary,
    ProgressEvent,
)


class BackendAdapter(ABC):
    """Concurrent-safe boundary between Studio and an inference backend.

    Implementations must make every public method safe to call concurrently.
    A slow submission must not prevent unrelated status, progress, result, or
    cancellation calls from proceeding.
    Progress reads are observations, not acknowledgements: polling the same job
    repeatedly must not consume or advance its lifecycle, and one observer must
    not hide progress from another observer.
    """

    @abstractmethod
    def get_backend_status(self) -> BackendStatus:
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> tuple[ModelSummary, ...]:
        raise NotImplementedError

    @abstractmethod
    def submit_generation(
        self,
        request: GenerationRequest,
    ) -> GenerationJobIdentity:
        raise NotImplementedError

    @abstractmethod
    def poll_or_stream_progress(self, job_id: str) -> ProgressEvent:
        """Return an idempotent, non-consuming progress observation.

        Repeated observations may return the same event. Implementations must
        not require polling to advance, complete, cancel, fail, or make a result
        available.
        """
        raise NotImplementedError

    def preview_frame(self, job_id: str) -> tuple[int, str | None]:
        """The latest live-preview frame for one job, and its id.

        CONCRETE, not abstract, and deliberately so: a backend that cannot
        decode a preview is a normal backend, not an incomplete one, and
        making this abstract would force every implementation and every test
        double to write the same stub. The default is the truthful answer for
        such a backend -- no frame -- and the socket omits `preview` entirely
        rather than sending a placeholder.

        Kept off `ProgressEvent` on purpose. That structure is small,
        loggable, and serialised into every job record; a 25 kB base64 frame
        belongs on the progress socket and nowhere else.
        """

        return (0, None)

    @abstractmethod
    def cancel_generation(self, job_id: str) -> CancellationResult:
        raise NotImplementedError

    @abstractmethod
    def get_result(self, job_id: str) -> GeneratedResult:
        raise NotImplementedError

    @abstractmethod
    def load_model(self, model_id: str) -> ModelResidency:
        """Make one model resident. Explicit, idempotent, and fallible.

        Generation must never load implicitly. An unknown or unloadable model
        must raise a structured error rather than silently selecting another.
        """
        raise NotImplementedError

    @abstractmethod
    def unload_model(self) -> ModelResidency:
        """Release the resident model, if any. Idempotent."""
        raise NotImplementedError

    @abstractmethod
    def get_current_model(self) -> ModelResidency:
        """Return residency without loading, selecting, or mutating state."""
        raise NotImplementedError

    @abstractmethod
    def get_capability(
        self,
        model_id: str,
        operation: str,
    ) -> ModelCapability:
        """Report generation limits for one model and operation.

        Studio must not assume a universal alignment rule or safe maximum.
        Implementations report what the selected model actually supports.
        """
        raise NotImplementedError

    @abstractmethod
    def shutdown(self) -> None:
        """Release every backend resource. Idempotent.

        Must be safe to call with a job in flight: in-flight work is terminated
        rather than awaited. A real adapter releases the resident model, its
        CUDA context, and any owned thread here.
        """
        raise NotImplementedError

    def supported_generation_parameters(self) -> frozenset[str]:
        """Declare which optional generation parameters this backend honours.

        The default is empty: a backend supports nothing beyond the core
        ``GenerationRequest`` fields unless it says otherwise. Studio derives
        its ignored-setting notice from this, so a backend that gains sampler
        support stops being reported as ignoring it.
        """
        return frozenset()
