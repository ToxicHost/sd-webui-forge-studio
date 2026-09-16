"""Dual-mode Studio composition root: lifecycle, hosts, failures, import safety.

Studio already owned its application service, backend port, result delivery and
a stdlib-only transport. What it did not own was the seam that assembles them:
`presentation._create_mock_presentation` built `StudioApplication(MockBackend())`
inline, and `backend_selection` returned a backend *name* with no factory. Two
inline arrangements existed and a third host would have added another.

These tests pin the composition root that replaced them, and -- the point of the
milestone -- prove that the extension-hosted and standalone arrangements share
one implementation rather than two that agree today.

No model is loaded. No CUDA is initialized. No socket is bound. No image is
generated: the only bytes written are a contained synthetic PNG built in test
code.

SCOPE: STATIC_IMPORT_SCOPE and MINIMAL_RUNTIME_SCOPE.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
APP_ROOT = TEST_ROOT.parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

COMPOSITION_SOURCE = APP_ROOT / "forge_studio" / "composition.py"
ADAPTER_SOURCE = APP_ROOT / "forge_studio" / "source_api_adapter.py"
PRESENTATION_SOURCE = APP_ROOT / "forge_studio" / "presentation.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_COMPOSITION_TESTS = 55

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

from forge_studio import (  # noqa: E402
    BackendAdapter,
    BackendStatus,
    CancellationResult,
    ExtensionHost,
    GeneratedResult,
    GenerationJobIdentity,
    GenerationRequest,
    JobState,
    ModelCapability,
    ModelResidency,
    ModelSummary,
    ProgressEvent,
    StandaloneHost,
    StructuredError,
    StudioComposition,
    StudioError,
    build_extension,
    build_standalone,
)
from forge_studio.composition import (  # noqa: E402
    DEFAULT_TERMINAL_WAIT,
    EXTENSION,
    HOST_NAMES,
    STANDALONE,
    TERMINAL_STATES,
    TIMED_OUT,
    InlineExecutor,
    StudioRuntimeServices,
    run_to_terminal,
)


#: A minimal valid PNG (1x1, truecolour). Built here, never generated.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8ffff3f0005fe02fea735cd900000000049454e"
    "44ae426082"
)

MODEL_ID = "fake-model"


def _request(**overrides: object) -> GenerationRequest:
    fields: dict[str, object] = {
        "model_id": MODEL_ID,
        "positive_prompt": "a contained synthetic subject",
        "negative_prompt": "",
        "seed": 7,
        "steps": 4,
        "cfg_scale": 7.0,
        "width": 64,
        "height": 64,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)  # type: ignore[arg-type]


class FakeBackend(BackendAdapter):
    """Deterministic backend driven by an injected executor. No model, no CUDA.

    Progress is advanced only by explicit `advance()` calls, so every test
    controls the timeline exactly. Nothing sleeps.
    """

    MODELS = (
        ModelSummary(
            model_id=MODEL_ID,
            name="Fake",
            description="Deterministic test double.",
            is_mock=True,
        ),
    )

    def __init__(
        self,
        services: StudioRuntimeServices,
        *,
        fail_on_submit: bool = False,
        fail_on_load: bool = False,
        fail_generation: bool = False,
        omit_output: bool = False,
    ) -> None:
        self._services = services
        self._fail_on_submit = fail_on_submit
        self._fail_on_load = fail_on_load
        self._fail_generation = fail_generation
        self._omit_output = omit_output
        self._jobs: dict[str, dict[str, object]] = {}
        self._resident: str | None = None
        self._stopped = False
        self._counter = 0
        self.shutdown_calls = 0

    # -- status ------------------------------------------------------------

    def get_backend_status(self) -> BackendStatus:
        return BackendStatus(
            backend_id="fake-backend",
            state="stopped" if self._stopped else "ready",
            ready=not self._stopped,
            is_mock=True,
            message="Fake backend.",
            model_loaded=self._resident is not None,
            cuda_initialized=False,
            network_access=False,
        )

    def list_models(self) -> tuple[ModelSummary, ...]:
        return self.MODELS

    def load_model(self, model_id: str) -> ModelResidency:
        if self._fail_on_load:
            raise StudioError(
                StructuredError(code="MODEL_LOAD_FAILED", message="Load refused.")
            )
        self._resident = model_id
        return ModelResidency(
            model_id=model_id, loaded=True, is_mock=True, message="Loaded."
        )

    def unload_model(self) -> ModelResidency:
        self._resident = None
        return ModelResidency(
            model_id=None, loaded=False, is_mock=True, message="Unloaded."
        )

    def get_current_model(self) -> ModelResidency:
        return ModelResidency(
            model_id=self._resident,
            loaded=self._resident is not None,
            is_mock=True,
            message="Fake residency.",
        )

    def get_capability(self, model_id: str, operation: str) -> ModelCapability:
        return ModelCapability(
            model_id=model_id,
            operation=operation,
            dimension_alignment=8,
            minimum_dimension=8,
            maximum_dimension=None,
            maximum_pixels=None,
            normalizes_dimensions=False,
            is_mock=True,
        )

    # -- jobs --------------------------------------------------------------

    def submit_generation(self, request: GenerationRequest) -> GenerationJobIdentity:
        if self._fail_on_submit:
            raise StudioError(
                StructuredError(code="BACKEND_SUBMIT_FAILED", message="Refused.")
            )
        self._counter += 1
        job_id = f"fake-{self._counter:04d}"
        record: dict[str, object] = {
            "state": JobState.QUEUED,
            "sequence": 0,
            "progress": 0,
            "request": request,
            "result": None,
        }
        self._jobs[job_id] = record

        # The executor is a real injection point, not decoration: the job's
        # first transition runs through it.
        def _start() -> None:
            record["state"] = JobState.RUNNING

        self._services.executor.submit(_start)
        return GenerationJobIdentity(job_id=job_id, state=JobState.QUEUED)

    def advance(self, job_id: str, *, to_completion: bool = False) -> None:
        record = self._job(job_id)
        if record["state"] in (JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED):
            return
        record["sequence"] = int(record["sequence"]) + 1  # type: ignore[arg-type]
        record["progress"] = min(100, int(record["progress"]) + 50)  # type: ignore[arg-type]
        if not to_completion:
            record["state"] = JobState.RUNNING
            return
        if self._fail_generation:
            record["state"] = JobState.FAILED
            return
        record["state"] = JobState.COMPLETED
        record["progress"] = 100
        record["result"] = self._make_result(job_id, record["request"])  # type: ignore[arg-type]

    def _make_result(self, job_id: str, request: GenerationRequest) -> GeneratedResult:
        root = self._services.result_root
        if root is None or self._omit_output:
            return GeneratedResult(
                job_id=job_id,
                state=JobState.COMPLETED,
                mime_type="image/png",
                image_data_url="data:image/png;base64,",
                metadata={"schema_version": "fake/v1"},
            )
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{job_id}.png"
        path.write_bytes(TINY_PNG)
        return GeneratedResult(
            job_id=job_id,
            state=JobState.COMPLETED,
            mime_type="image/png",
            output_path=str(path),
            metadata={"schema_version": "fake/v1", "seed": request.seed},
        )

    def poll_or_stream_progress(self, job_id: str) -> ProgressEvent:
        record = self._job(job_id)
        return ProgressEvent(
            job_id=job_id,
            state=record["state"],  # type: ignore[arg-type]
            sequence=int(record["sequence"]),  # type: ignore[arg-type]
            progress=int(record["progress"]),  # type: ignore[arg-type]
            message="fake",
            error=(
                StructuredError(code="FAKE_GENERATION_FAILED", message="Failed.")
                if record["state"] is JobState.FAILED
                else None
            ),
        )

    def cancel_generation(self, job_id: str) -> CancellationResult:
        record = self._job(job_id)
        if record["state"] in (JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED):
            return CancellationResult(
                job_id=job_id,
                cancelled=False,
                state=record["state"],  # type: ignore[arg-type]
                message="Job is already terminal.",
            )
        record["state"] = JobState.CANCELLED
        return CancellationResult(
            job_id=job_id,
            cancelled=True,
            state=JobState.CANCELLED,
            message="Cancelled.",
        )

    def get_result(self, job_id: str) -> GeneratedResult:
        record = self._job(job_id)
        result = record["result"]
        if result is None:
            raise StudioError(
                StructuredError(code="RESULT_NOT_READY", message="No result yet.")
            )
        return result  # type: ignore[return-value]

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self._stopped = True
        self._jobs.clear()

    def _job(self, job_id: str) -> dict[str, object]:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise StudioError(
                StructuredError(code="JOB_NOT_FOUND", message="Unknown job.")
            ) from None


def _factory(**kwargs: object):
    def build(services: StudioRuntimeServices) -> FakeBackend:
        return FakeBackend(services, **kwargs)  # type: ignore[arg-type]

    return build


class _Temp(unittest.TestCase):
    """Every composition in this suite gets its own contained result root."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "results"

    def standalone(self, **kwargs: object) -> StudioComposition:
        composition = build_standalone(
            result_root=self.root, backend_factory=_factory(**kwargs)
        )
        self.addCleanup(composition.shutdown)
        return composition

    def extension(self, host_services: object | None = None, **kwargs: object):
        composition = build_extension(
            host_services, result_root=self.root, backend_factory=_factory(**kwargs)
        )
        self.addCleanup(composition.shutdown)
        return composition


# ------------------------------------------------------------------ lifecycle


class ApplicationLifecycleTests(_Temp):
    def test_readiness_is_established_without_loading_a_model(self) -> None:
        readiness = self.standalone().readiness()
        self.assertTrue(readiness.ready)
        self.assertFalse(
            readiness.model_loaded,
            "readiness must not load a model to answer",
        )
        self.assertEqual("fake-backend", readiness.backend_id)
        self.assertEqual(1, readiness.model_count)
        self.assertTrue(readiness.result_delivery_configured)

    def test_readiness_serialises_to_scalars_only(self) -> None:
        payload = self.standalone().readiness().to_dict()
        json.dumps(payload)  # would raise on any non-serialisable object
        for key in ("host", "session_id", "backend_id", "backend_state"):
            self.assertIsInstance(payload[key], str)

    def test_session_identifier_comes_from_the_injected_source(self) -> None:
        issued: list[str] = []

        def identifiers() -> str:
            issued.append(f"id-{len(issued)}")
            return issued[-1]

        composition = StudioComposition.build(
            StandaloneHost(backend_factory=_factory()),
            identifiers=identifiers,
            result_root=self.root,
        )
        self.addCleanup(composition.shutdown)
        self.assertEqual("id-0", composition.session_id)

    def test_complete_flow_reaches_a_result_through_an_opaque_handle(self) -> None:
        composition = self.standalone()
        application = composition.application
        job = application.submit_generation(_request())

        backend = composition.application._backend  # noqa: SLF001 - test double
        backend.advance(job.job_id)
        running = application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.RUNNING, running.state)

        backend.advance(job.job_id, to_completion=True)
        done = application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.COMPLETED, done.state)
        self.assertEqual(100, done.progress)

        asset = application.result_asset(job.job_id)
        self.assertIsNotNone(asset)
        self.assertTrue(asset.handle.startswith("studio-result/"))
        self.assertNotIn(str(self.root), asset.handle)
        payload = application.read_result_asset(asset.handle)
        self.assertEqual(TINY_PNG, payload.content)
        self.assertEqual("image/png", payload.media_type)

    def test_progress_is_ordered_and_never_regresses(self) -> None:
        composition = self.standalone()
        application = composition.application
        job = application.submit_generation(_request())
        backend = application._backend  # noqa: SLF001

        observed = [application.poll_or_stream_progress(job.job_id)]
        backend.advance(job.job_id)
        observed.append(application.poll_or_stream_progress(job.job_id))
        backend.advance(job.job_id, to_completion=True)
        observed.append(application.poll_or_stream_progress(job.job_id))

        sequences = [event.sequence for event in observed]
        progresses = [event.progress for event in observed]
        self.assertEqual(sorted(sequences), sequences)
        self.assertEqual(sorted(progresses), progresses)
        self.assertEqual(100, progresses[-1])

    def test_polling_does_not_advance_the_job(self) -> None:
        composition = self.standalone()
        application = composition.application
        job = application.submit_generation(_request())
        first = application.poll_or_stream_progress(job.job_id)
        for _ in range(5):
            repeat = application.poll_or_stream_progress(job.job_id)
        self.assertEqual(first.sequence, repeat.sequence)
        self.assertEqual(first.state, repeat.state)

    def test_shutdown_is_deterministic_and_idempotent(self) -> None:
        composition = self.standalone()
        backend = composition.application._backend  # noqa: SLF001
        composition.shutdown()
        composition.shutdown()
        composition.shutdown()
        self.assertTrue(composition.closed)
        self.assertEqual(
            1, backend.shutdown_calls, "the backend must be shut down exactly once"
        )

    def test_shutdown_with_a_pending_job_terminates_rather_than_waits(self) -> None:
        composition = self.standalone()
        application = composition.application
        job = application.submit_generation(_request())
        composition.shutdown()
        with self.assertRaises(StudioError) as raised:
            application.poll_or_stream_progress(job.job_id)
        self.assertEqual("JOB_NOT_FOUND", raised.exception.error.code)

    def test_readiness_after_shutdown_reports_not_ready_rather_than_raising(self) -> None:
        composition = self.standalone()
        composition.shutdown()
        readiness = composition.readiness()
        self.assertFalse(readiness.ready)
        self.assertEqual("stopped", readiness.backend_state)

    def test_context_manager_shuts_down_on_exit(self) -> None:
        with build_standalone(
            result_root=self.root, backend_factory=_factory()
        ) as composition:
            self.assertFalse(composition.closed)
        self.assertTrue(composition.closed)

    def test_the_executor_injection_point_is_actually_used(self) -> None:
        executor = InlineExecutor()
        composition = StudioComposition.build(
            StandaloneHost(backend_factory=_factory()),
            executor=executor,
            result_root=self.root,
        )
        self.addCleanup(composition.shutdown)
        self.assertEqual(0, executor.submitted)
        composition.application.submit_generation(_request())
        self.assertEqual(
            1, executor.submitted, "submission must run through the injected executor"
        )


# --------------------------------------------------------------- cancellation


class CancellationTests(_Temp):
    def test_cancel_before_execution(self) -> None:
        application = self.standalone().application
        job = application.submit_generation(_request())
        result = application.cancel_generation(job.job_id)
        self.assertTrue(result.cancelled)
        self.assertIs(JobState.CANCELLED, result.state)

    def test_cancel_during_execution(self) -> None:
        composition = self.standalone()
        application = composition.application
        job = application.submit_generation(_request())
        application._backend.advance(job.job_id)  # noqa: SLF001
        result = application.cancel_generation(job.job_id)
        self.assertTrue(result.cancelled)
        self.assertIs(
            JobState.CANCELLED, application.poll_or_stream_progress(job.job_id).state
        )

    def test_cancel_after_completion_is_refused_truthfully(self) -> None:
        composition = self.standalone()
        application = composition.application
        job = application.submit_generation(_request())
        application._backend.advance(job.job_id, to_completion=True)  # noqa: SLF001
        result = application.cancel_generation(job.job_id)
        self.assertFalse(result.cancelled)
        self.assertIs(JobState.COMPLETED, result.state)

    def test_duplicate_cancellation_is_idempotent(self) -> None:
        application = self.standalone().application
        job = application.submit_generation(_request())
        first = application.cancel_generation(job.job_id)
        second = application.cancel_generation(job.job_id)
        self.assertTrue(first.cancelled)
        self.assertFalse(second.cancelled)
        self.assertIs(JobState.CANCELLED, second.state)

    def test_cancellation_releases_the_delivery_handle(self) -> None:
        composition = self.standalone()
        application = composition.application
        job = application.submit_generation(_request())
        application._backend.advance(job.job_id, to_completion=True)  # noqa: SLF001
        asset = application.result_asset(job.job_id)
        self.assertIsNotNone(asset)
        application.cancel_generation(job.job_id)
        with self.assertRaises(StudioError):
            application.read_result_asset(asset.handle)


# ------------------------------------------------------------- failure matrix


class FailureMatrixTests(_Temp):
    def _assert_scalar_record(self, error: StructuredError) -> None:
        self.assertIsInstance(error.code, str)
        self.assertIsInstance(error.message, str)
        self.assertTrue(error.field is None or isinstance(error.field, str))
        rendered = json.dumps(error.to_dict())
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("Traceback", rendered)

    def test_request_validation_failure(self) -> None:
        application = self.standalone().application
        with self.assertRaises(StudioError) as raised:
            application.submit_generation(_request(steps=0))
        error = raised.exception.error
        self.assertEqual("INVALID_GENERATION_REQUEST", error.code)
        self.assertEqual("steps", error.field)
        self._assert_scalar_record(error)

    def test_unknown_model_is_rejected_before_the_backend_is_called(self) -> None:
        application = self.standalone().application
        with self.assertRaises(StudioError) as raised:
            application.submit_generation(_request(model_id="not-a-model"))
        self.assertEqual("model_id", raised.exception.error.field)

    def test_backend_load_failure(self) -> None:
        application = self.standalone(fail_on_load=True).application
        with self.assertRaises(StudioError) as raised:
            application.load_model(MODEL_ID)
        self._assert_scalar_record(raised.exception.error)
        self.assertEqual("MODEL_LOAD_FAILED", raised.exception.error.code)

    def test_backend_submit_failure(self) -> None:
        application = self.standalone(fail_on_submit=True).application
        with self.assertRaises(StudioError) as raised:
            application.submit_generation(_request())
        self.assertEqual("BACKEND_SUBMIT_FAILED", raised.exception.error.code)

    def test_generation_failure_reaches_a_terminal_state_with_a_record(self) -> None:
        composition = self.standalone(fail_generation=True)
        application = composition.application
        job = application.submit_generation(_request())
        application._backend.advance(job.job_id, to_completion=True)  # noqa: SLF001
        event = application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.FAILED, event.state)
        self.assertIsNotNone(event.error)
        self._assert_scalar_record(event.error)

    def test_publication_failure_yields_no_handle_rather_than_a_broken_one(self) -> None:
        composition = self.standalone(omit_output=True)
        application = composition.application
        job = application.submit_generation(_request())
        application._backend.advance(job.job_id, to_completion=True)  # noqa: SLF001
        self.assertIsNone(
            application.result_asset(job.job_id),
            "a result with no output path must mint no handle",
        )

    def test_result_resolution_failure_for_an_unknown_handle(self) -> None:
        application = self.standalone().application
        with self.assertRaises(StudioError) as raised:
            application.read_result_asset("studio-result/" + "0" * 32 + ".png")
        self._assert_scalar_record(raised.exception.error)

    def test_result_not_found_for_an_unknown_job(self) -> None:
        application = self.standalone().application
        with self.assertRaises(StudioError) as raised:
            application.get_result("no-such-job")
        self.assertEqual("JOB_NOT_FOUND", raised.exception.error.code)

    def test_an_unknown_host_name_is_refused(self) -> None:
        class Rogue(StandaloneHost):
            name = "rogue"

        with self.assertRaises(StudioError) as raised:
            StudioComposition.build(Rogue(backend_factory=_factory()))
        self.assertEqual("UNKNOWN_STUDIO_HOST", raised.exception.error.code)

    def test_a_host_returning_a_non_backend_is_refused(self) -> None:
        with self.assertRaises(StudioError) as raised:
            StudioComposition.build(
                StandaloneHost(backend_factory=lambda services: object())  # type: ignore[arg-type,return-value]
            )
        self.assertEqual("INVALID_STUDIO_BACKEND", raised.exception.error.code)

    def test_an_unwired_backend_name_fails_loudly_instead_of_falling_back(self) -> None:
        with self.assertRaises(StudioError) as raised:
            StudioComposition.build(StandaloneHost(backend_name="forge-headless"))
        self.assertEqual("BACKEND_NOT_WIRED", raised.exception.error.code)


# ------------------------------------------------------------ host equivalence


class HostEquivalenceTests(_Temp):
    def _run_flow(self, composition: StudioComposition) -> dict[str, object]:
        application = composition.application
        job = application.submit_generation(_request())
        backend = application._backend  # noqa: SLF001
        events = [application.poll_or_stream_progress(job.job_id)]
        backend.advance(job.job_id)
        events.append(application.poll_or_stream_progress(job.job_id))
        backend.advance(job.job_id, to_completion=True)
        events.append(application.poll_or_stream_progress(job.job_id))
        asset = application.result_asset(job.job_id)
        payload = application.read_result_asset(asset.handle)
        return {
            "states": [event.state.value for event in events],
            "sequences": [event.sequence for event in events],
            "progress": [event.progress for event in events],
            "media_type": payload.media_type,
            "bytes": payload.content,
            "handle_prefix": asset.handle.split("/", 1)[0],
        }

    def test_both_hosts_are_recognised(self) -> None:
        self.assertEqual({STANDALONE, EXTENSION}, set(HOST_NAMES))
        self.assertEqual(STANDALONE, StandaloneHost().name)
        self.assertEqual(EXTENSION, ExtensionHost().name)

    def test_the_same_request_produces_the_same_semantics_on_both_hosts(self) -> None:
        standalone = self._run_flow(self.standalone())
        extension = self._run_flow(self.extension())
        self.assertEqual(standalone, extension)

    def test_cancellation_semantics_match_across_hosts(self) -> None:
        def cancel_flow(composition: StudioComposition) -> tuple[bool, str, bool]:
            application = composition.application
            job = application.submit_generation(_request())
            first = application.cancel_generation(job.job_id)
            second = application.cancel_generation(job.job_id)
            return first.cancelled, first.state.value, second.cancelled

        self.assertEqual(
            cancel_flow(self.standalone()), cancel_flow(self.extension())
        )

    def test_error_semantics_match_across_hosts(self) -> None:
        def failure(composition: StudioComposition) -> tuple[str, str | None]:
            with self.assertRaises(StudioError) as raised:
                composition.application.submit_generation(_request(width=0))
            return raised.exception.error.code, raised.exception.error.field

        self.assertEqual(failure(self.standalone()), failure(self.extension()))

    def test_only_host_metadata_differs(self) -> None:
        standalone = self.standalone().readiness().to_dict()
        extension = self.extension().readiness().to_dict()
        for key in (
            "ready",
            "backend_id",
            "backend_state",
            "backend_is_mock",
            "model_loaded",
            "result_delivery_configured",
            "model_count",
        ):
            self.assertEqual(standalone[key], extension[key], key)
        self.assertNotEqual(standalone["host"], extension["host"])
        self.assertNotEqual(standalone["host_metadata"], extension["host_metadata"])

    def test_both_hosts_construct_the_same_application_type(self) -> None:
        self.assertIs(
            type(self.standalone().application),
            type(self.extension().application),
        )

    def test_business_logic_lives_in_one_place(self) -> None:
        """Neither host subclasses or overrides application behaviour."""

        tree = ast.parse(COMPOSITION_SOURCE.read_text(encoding="utf-8"))
        hosts = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name in {"StandaloneHost", "ExtensionHost"}
        ]
        self.assertEqual(2, len(hosts))
        business = {
            "submit_generation",
            "poll_or_stream_progress",
            "cancel_generation",
            "get_result",
            "result_asset",
            "read_result_asset",
        }
        for host in hosts:
            defined = {
                node.name
                for node in host.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertEqual(
                set(), defined & business, f"{host.name} must not own job behaviour"
            )


class ExtensionHostInjectionTests(_Temp):
    def test_host_services_supply_the_backend(self) -> None:
        calls: list[str] = []

        class HostServices:
            def create_backend(self, services: StudioRuntimeServices) -> FakeBackend:
                calls.append("create_backend")
                return FakeBackend(services)

        composition = build_extension(HostServices(), result_root=self.root)
        self.addCleanup(composition.shutdown)
        self.assertEqual(["create_backend"], calls)
        self.assertTrue(composition.readiness().ready)

    def test_host_services_supply_a_result_root(self) -> None:
        root = self.root

        class HostServices:
            def result_root(self) -> Path:
                return root

        composition = build_extension(HostServices())
        self.addCleanup(composition.shutdown)
        self.assertEqual(root, composition.services.result_root)

    def test_host_metadata_is_merged_but_cannot_misreport_the_host(self) -> None:
        class HostServices:
            def describe(self) -> dict[str, object]:
                return {"host": STANDALONE, "neo_build": "x1", "ignored": object()}

        composition = build_extension(
            HostServices(), result_root=self.root, backend_factory=_factory()
        )
        self.addCleanup(composition.shutdown)
        metadata = composition.readiness().host_metadata
        self.assertEqual(EXTENSION, metadata["host"])
        self.assertEqual("x1", metadata["neo_build"])
        self.assertNotIn("ignored", metadata)

    def test_host_close_runs_on_shutdown(self) -> None:
        closed: list[bool] = []

        class HostServices:
            def close(self) -> None:
                closed.append(True)

        composition = build_extension(
            HostServices(), result_root=self.root, backend_factory=_factory()
        )
        composition.shutdown()
        self.assertEqual([True], closed)

    def test_an_extension_host_with_no_facilities_still_works(self) -> None:
        composition = build_extension(None, result_root=self.root)
        self.addCleanup(composition.shutdown)
        self.assertTrue(composition.readiness().ready)


# ----------------------------------------------------------- shared wait policy


class TerminalWaitTests(unittest.TestCase):
    def test_terminal_states_are_the_three_studio_terminal_states(self) -> None:
        self.assertEqual({"completed", "cancelled", "failed"}, set(TERMINAL_STATES))

    def test_returns_on_the_first_terminal_observation(self) -> None:
        states = iter(["running", "running", "completed"])
        polled: list[int] = []

        def poll() -> str:
            polled.append(1)
            return next(states)

        state, event = run_to_terminal(
            poll=poll,
            observe=lambda event: event,
            cancel=lambda: self.fail("cancel must not run on success"),
            clock=lambda: 0.0,
            sleep=lambda _seconds: None,
        )
        self.assertEqual("completed", state)
        self.assertEqual("completed", event)
        self.assertEqual(3, len(polled))

    def test_cancels_once_when_the_deadline_expires(self) -> None:
        ticks = iter([0.0, 0.0, 1000.0])
        cancelled: list[int] = []
        state, event = run_to_terminal(
            poll=lambda: "running",
            observe=lambda event: event,
            cancel=lambda: cancelled.append(1),
            clock=lambda: next(ticks),
            sleep=lambda _seconds: None,
        )
        self.assertEqual(TIMED_OUT, state)
        self.assertIsNone(event)
        self.assertEqual([1], cancelled)

    def test_the_policy_carries_the_values_the_adapter_always_used(self) -> None:
        self.assertEqual(120.0, DEFAULT_TERMINAL_WAIT.timeout_seconds)
        self.assertEqual(0.02, DEFAULT_TERMINAL_WAIT.poll_interval_seconds)

    def test_the_canonical_frontend_adapter_uses_the_shared_policy(self) -> None:
        tree = ast.parse(ADAPTER_SOURCE.read_text(encoding="utf-8"))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn(
            "run_to_terminal",
            calls,
            "the adapter must share the wait policy, not keep a copy of it",
        )


# -------------------------------------------------------------- import safety


def _subprocess_probe(body: str) -> dict[str, object]:
    """Import in a fresh interpreter and report what that import pulled in."""

    # Assembled at column zero rather than via an f-string inside an indented
    # template: interpolating a multi-line body into an indented placeholder
    # indents every line after the first and the probe dies on IndentationError.
    prologue = f"import json, sys\nsys.path.insert(0, {str(APP_ROOT)!r})\n"
    epilogue = (
        "names = sorted(sys.modules)\n"
        "print(json.dumps({\n"
        '    "gradio": [n for n in names if n == "gradio" or n.startswith("gradio.")],\n'
        '    "neo": [n for n in names if n in ("modules", "modules_forge", "webui")\n'
        '            or n.startswith(("modules.", "modules_forge.", "webui."))],\n'
        '    "torch": [n for n in names if n == "torch" or n.startswith("torch.")],\n'
        '    "cuda": [n for n in names if "cuda" in n.casefold()],\n'
        '    "socket_server": [n for n in names if n == "socketserver"],\n'
        "}))\n"
    )
    script = prologue + textwrap.dedent(body).strip() + "\n" + epilogue
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-I", "-S", "-B", "-c", script],
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-2000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


class StandaloneImportSafetyTests(unittest.TestCase):
    def test_importing_the_composition_root_pulls_in_no_gradio_neo_or_torch(self) -> None:
        report = _subprocess_probe("import forge_studio.composition")
        self.assertEqual([], report["gradio"])
        self.assertEqual([], report["neo"])
        self.assertEqual([], report["torch"])
        self.assertEqual([], report["cuda"])

    def test_building_a_standalone_composition_stays_clean(self) -> None:
        report = _subprocess_probe(
            "from forge_studio.composition import build_standalone\n"
            "c = build_standalone()\n"
            "c.readiness()\n"
            "c.shutdown()"
        )
        self.assertEqual([], report["gradio"])
        self.assertEqual([], report["neo"])
        self.assertEqual([], report["torch"])
        self.assertEqual([], report["cuda"])

    def test_building_an_extension_composition_stays_clean(self) -> None:
        report = _subprocess_probe(
            "from forge_studio.composition import build_extension\n"
            "c = build_extension()\n"
            "c.readiness()\n"
            "c.shutdown()"
        )
        self.assertEqual([], report["gradio"])
        self.assertEqual([], report["neo"])
        self.assertEqual([], report["torch"])
        self.assertEqual([], report["cuda"])

    def test_building_a_composition_binds_no_socket(self) -> None:
        report = _subprocess_probe(
            "from forge_studio.composition import build_standalone\n"
            "c = build_standalone()\n"
            "c.shutdown()"
        )
        self.assertEqual(
            [],
            report["socket_server"],
            "constructing the application must not pull in the server machinery",
        )

    def test_the_composition_module_imports_only_stdlib_and_siblings(self) -> None:
        tree = ast.parse(COMPOSITION_SOURCE.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.partition(".")[0])
        for forbidden in ("gradio", "modules", "modules_forge", "webui", "torch"):
            self.assertNotIn(forbidden, roots)

    def test_build_creates_exactly_the_configured_result_root(self) -> None:
        """And nothing else.

        The registry resolves its case policy by writing a probe file inside
        the root, so an absent root is INCONCLUSIVE and fails closed. Creating
        it is assembly; the inline arrangements this seam replaced only worked
        because the directory survived from an earlier run.
        """

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "outer"
            root = base / "results"
            composition = build_standalone(
                result_root=root, backend_factory=_factory()
            )
            self.addCleanup(composition.shutdown)
            self.assertTrue(root.is_dir())
            self.assertEqual([root.name], [child.name for child in base.iterdir()])

    def test_build_creates_nothing_when_no_result_root_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            composition = build_standalone(backend_factory=_factory())
            self.addCleanup(composition.shutdown)
            self.assertIsNone(composition.services.result_root)
            self.assertEqual([], list(Path(directory).iterdir()))


class WiringTests(unittest.TestCase):
    def test_presentation_builds_its_application_through_the_composition_root(self) -> None:
        tree = ast.parse(PRESENTATION_SOURCE.read_text(encoding="utf-8"))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("build_standalone", calls)
        constructed = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"MockBackend", "StudioApplication"}
        }
        self.assertEqual(
            set(),
            constructed,
            "presentation must not assemble an application inline any more",
        )

    def test_the_composition_symbols_are_exported(self) -> None:
        import forge_studio

        for name in (
            "StudioComposition",
            "StandaloneHost",
            "ExtensionHost",
            "build_standalone",
            "build_extension",
            "StudioRuntimeServices",
            "StudioReadiness",
        ):
            self.assertIn(name, forge_studio.__all__)
            self.assertTrue(hasattr(forge_studio, name))


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_COMPOSITION_TESTS,
            "Composition test count changed: update EXPECTED_COMPOSITION_TESTS "
            "deliberately, never to match",
        )

    def test_scope_is_declared(self) -> None:
        doc = sys.modules[__name__].__doc__ or ""
        for label in SCOPE_LABELS:
            self.assertIn(label, doc)


if __name__ == "__main__":
    unittest.main()
