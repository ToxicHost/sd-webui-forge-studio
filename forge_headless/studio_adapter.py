"""`BackendAdapter` implementation backed by the headless Forge runtime.

This lives in `forge_headless/`, not `forge_studio/`, because `forge_studio` is
purity-locked: an AST test asserts no module under it imports `modules`,
`modules_forge`, `backend`, `torch`, `torchvision`, `gradio`, or `numpy`. The
dependency therefore points this way -- `forge_headless` may import Studio
contracts, never the reverse.

Phase 2A implements the catalogue and residency reads. Generation and result
delivery still refuse, and `load_model` refuses *after* full validation.
"""

from __future__ import annotations

from forge_studio.backend import BackendAdapter
from forge_studio.contracts import (
    BackendStatus,
    CancellationResult,
    GeneratedResult,
    GenerationJobIdentity,
    GenerationRequest,
    ModelCapability,
    ModelResidency,
    ModelSummary,
    ProgressEvent,
    StructuredError,
)

from .contracts import HeadlessError, LoadSupport, ModelCandidate, RuntimeState


class HeadlessBackendError(Exception):
    """Structured, path-free failure crossing into the Studio layer."""

    def __init__(self, error: StructuredError, *, http_status: int = 400) -> None:
        super().__init__(error.message)
        self.error = error
        self.http_status = http_status


def project_candidate(candidate: ModelCandidate) -> ModelSummary:
    """Project a catalogue entry onto the existing Studio wire contract.

    Deliberately drops `relative_location`: the model route must not carry a
    location of any kind. `description` states the format and how far the
    retained loader takes it, which is a backend fact rather than a guess.
    """
    if candidate.load_support is LoadSupport.LOAD_PLUMBED:
        support = "loadable by the retained backend once authorized"
    elif candidate.load_support is LoadSupport.RECOGNIZED_NOT_PLUMBED:
        support = "format recognized but not yet loadable"
    else:
        support = "format not supported"
    return ModelSummary(
        model_id=candidate.model_id,
        name=candidate.display_name,
        description=f"{candidate.format} checkpoint, {candidate.size_bytes} bytes; {support}",
        is_mock=False,
    )


def _no_residency() -> ModelResidency:
    """No model is ever resident in Phase 2A, and this says so plainly."""
    return ModelResidency(
        model_id=None,
        loaded=False,
        is_mock=False,
        message="No model is resident. Real model loading is not authorized in this phase.",
    )


class HeadlessBackendAdapter(BackendAdapter):
    """Adapter over `ForgeHeadlessRuntime`. Concurrency is the runtime's lock.

    Generation is opt-in. With no `generation` session injected the adapter
    behaves exactly as Phase 2A did -- every generation-side method refuses with
    `HEADLESS_BACKEND_NOT_READY` -- so the read-only contract, and the tests
    that pin it, are unchanged.

    With a session injected the adapter orchestrates through it. The session
    owns translation and the job table; the adapter owns the Studio port
    surface. Neither invents a request, job, progress, result, cancellation or
    error model: every value crossing this boundary is a
    `forge_studio.contracts` type.
    """

    def __init__(self, runtime: object, *, generation: object | None = None) -> None:
        self._runtime = runtime
        self._generation = generation

    # -- reads -------------------------------------------------------------

    def get_backend_status(self) -> BackendStatus:
        state = getattr(self._runtime, "state", RuntimeState.UNINITIALIZED)
        ready = state in {
            RuntimeState.READY_NO_MODEL,
            RuntimeState.CATALOGUE_READY_NO_MODEL,
        }
        residency = self.get_current_model()
        session = self._generation
        if session is not None and not session.closed:
            # A configured generation session can serve requests whatever the
            # catalogue runtime is doing, so readiness reports the session too
            # rather than only the catalogue.
            ready = ready or bool(residency.loaded)
        message = (
            "Headless Forge boundary. Catalogue reads only; real model "
            "loading is not authorized in this phase."
        )
        if session is not None:
            message = (
                "Headless Forge boundary with a generation session attached."
            )
        return BackendStatus(
            backend_id="forge-headless",
            state=str(getattr(state, "value", state)),
            ready=bool(ready),
            is_mock=False,
            message=message,
            model_loaded=bool(residency.loaded),
            # Neither is established: reading either truthfully requires
            # importing Torch, which this phase does not do.
            cuda_initialized=False,
            network_access=False,
        )

    def list_models(self) -> tuple[ModelSummary, ...]:
        if self._runtime is None:
            # A session can be injected without a catalogue runtime. Report the
            # resident session if there is one and an honest empty list
            # otherwise, rather than raising on a pure read.
            return self._session_models()
        try:
            candidates = self._runtime.list_models()
        except HeadlessError as exc:
            if exc.code == "HEADLESS_CATALOGUE_NOT_CONFIGURED":
                # An unconfigured catalogue is honestly empty, not an error:
                # the mock default must keep working and the canonical client
                # must render an empty list rather than a failure.
                return self._session_models()
            raise self._structured(exc) from None
        projected = tuple(project_candidate(item) for item in candidates)
        return projected if projected else self._session_models()

    def _session_models(self) -> tuple[ModelSummary, ...]:
        """The resident session as a catalogue row, when there is one."""

        if self._generation is None:
            return ()
        model = self._generation.resident_model
        if model is None or not getattr(model, "resident", False):
            return ()
        return (
            ModelSummary(
                model_id=str(model.model_id),
                name=str(model.model_id),
                description=(
                    f"{getattr(model, 'family', 'unknown')} session, resident"
                ),
                is_mock=False,
            ),
        )

    def get_current_model(self) -> ModelResidency:
        # This read never loads, selects, or mutates anything. With a session
        # injected it reports that session's residency; without one, residency
        # is unconditionally false, as in Phase 2A.
        if self._generation is not None:
            model = self._generation.resident_model
            if model is not None and getattr(model, "resident", False):
                return ModelResidency(
                    model_id=str(model.model_id),
                    loaded=True,
                    is_mock=False,
                    message="A headless model session is resident.",
                )
        return _no_residency()

    def get_capability(self, model_id: str, operation: str) -> ModelCapability:
        if self._generation is not None:
            return self._generation.capability_for(model_id, operation)
        del model_id, operation
        raise self._structured(
            HeadlessError(
                "HEADLESS_CAPABILITY_UNKNOWN",
                "Model capability requires a resident model.",
            )
        )

    def supported_generation_parameters(self) -> frozenset[str]:
        """What Studio can actually vary on this backend.

        Exactly the fields the owned Studio request currently carries, and
        nothing more. Backend-defaulted fields and pinned-off flags stay out;
        declaring either would tell the UI it can change something it cannot.
        """

        if self._generation is None:
            return frozenset()
        from .studio_generation import STUDIO_REQUEST_FIELDS

        return frozenset(STUDIO_REQUEST_FIELDS)

    # -- mutations ---------------------------------------------------------

    def load_model(self, model_id: str) -> ModelResidency:
        try:
            self._runtime.load_model(model_id)
        except HeadlessError as exc:
            raise self._structured(exc) from None
        # Unreachable in Phase 2A: the policy gate always raises. If a future
        # loader succeeds it must report residency itself rather than let this
        # fall through to an optimistic answer.
        raise self._structured(
            HeadlessError(
                "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED",
                "Loading a real model is not authorized in this phase.",
            )
        )

    def unload_model(self) -> ModelResidency:
        # Idempotent and truthful: nothing is ever resident to release.
        return _no_residency()

    def shutdown(self) -> None:
        """Release the session and the runtime. Idempotent.

        The session closes first so in-flight jobs reach a terminal state before
        the runtime goes away, and the runtime is released in a `finally` so a
        cleanup hook that raises cannot leave it running.
        """

        try:
            if self._generation is not None:
                self._generation.close()
        finally:
            shutdown = getattr(self._runtime, "shutdown", None)
            if callable(shutdown):
                shutdown()

    def use_detector_resolver(self, resolver: object | None) -> None:
        """Hold the name -> path resolver for Auto Detail.

        On the ADAPTER, not the session: `self._generation` is replaced on
        every model load, so a resolver stored there would work until the
        owner switched checkpoint and then silently stop -- which is the
        shape of half the defects this phase has found.
        """

        self._detector_resolver = resolver

    def submit_generation(self, request: GenerationRequest) -> GenerationJobIdentity:
        if self._generation is not None:
            from .studio_generation import _attach_detector_resolver

            _attach_detector_resolver(
                self._generation, getattr(self, "_detector_resolver", None)
            )
            return self._generation.submit(request)
        del request
        raise self._structured(
            HeadlessError(
                "HEADLESS_BACKEND_NOT_READY",
                "Generation requires a resident model.",
            )
        )

    def poll_or_stream_progress(self, job_id: str) -> ProgressEvent:
        if self._generation is not None:
            return self._generation.poll(job_id)
        del job_id
        raise self._structured(
            HeadlessError(
                "HEADLESS_BACKEND_NOT_READY",
                "No generation can be in flight in this phase.",
            )
        )

    def preview_frame(self, job_id: str) -> tuple[int, str | None]:
        """The latest decoded preview frame, or no frame.

        Never raises, unlike its neighbours here. A progress socket asking for
        a preview on a backend that is not ready has asked a reasonable
        question with a truthful answer -- there is no frame -- and turning
        that into a structured error would put an exception on a path that
        runs several times a second for the entire life of every job.
        """

        if self._generation is None:
            return (0, None)
        return self._generation.preview_frame(job_id)

    def cancel_generation(self, job_id: str) -> CancellationResult:
        if self._generation is not None:
            return self._generation.cancel(job_id)
        del job_id
        raise self._structured(
            HeadlessError(
                "HEADLESS_BACKEND_NOT_READY",
                "No generation can be in flight in this phase.",
            )
        )

    def get_result(self, job_id: str) -> GeneratedResult:
        if self._generation is not None:
            return self._generation.result(job_id)
        del job_id
        raise self._structured(
            HeadlessError(
                "HEADLESS_BACKEND_NOT_READY",
                "No generation can have produced a result in this phase.",
            )
        )

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _structured(exc: HeadlessError) -> HeadlessBackendError:
        status = 404 if exc.code == "HEADLESS_MODEL_UNKNOWN" else 400
        if exc.code in {
            "HEADLESS_MODEL_LOAD_NOT_AUTHORIZED",
            "HEADLESS_BACKEND_NOT_READY",
            "HEADLESS_CAPABILITY_UNKNOWN",
        }:
            status = 409
        return HeadlessBackendError(
            StructuredError(code=exc.code, message=exc.message),
            http_status=status,
        )
