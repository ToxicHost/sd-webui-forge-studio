"""The real HeadlessBackendAdapter, wired through Studio, exercised non-live.

Phase 1 gave Studio a composition root and proved it with a fake backend. This
suite selects the **real** `HeadlessBackendAdapter` and drives it through the
real `StudioApplication`, the real `GenerationGateway`, the real
`HeadlessProgress` state machine, and the real `ResultRegistry`.

Only the things beneath the adapter are doubles, and they are the ones the
handoff names: the generation port, the resident-model description, the
executor, the clock, the identifier source, the result writer, and the cleanup
hook. The adapter itself is never replaced -- if it were, this suite would prove
nothing about the code that ships.

No model file is opened. No CUDA is initialized. No image is generated: the only
bytes written are a 70-byte synthetic PNG literal. No socket is bound; the
frontend-vocabulary tests run the transport adapters in process.

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

ADAPTER_SOURCE = APP_ROOT / "forge_headless" / "studio_adapter.py"
SESSION_SOURCE = APP_ROOT / "forge_headless" / "studio_generation.py"
COMPOSITION_SOURCE = APP_ROOT / "forge_studio" / "composition.py"

#: Declared so a loader error cannot silently hide this suite.
EXPECTED_HEADLESS_INTEGRATION_TESTS = 76

SCOPE_LABELS = ("STATIC_IMPORT_SCOPE", "MINIMAL_RUNTIME_SCOPE")

from forge_studio import (  # noqa: E402
    BACKEND_KINDS,
    HEADLESS_BACKEND,
    MOCK_BACKEND,
    GenerationRequest,
    JobState,
    StudioError,
    build_standalone,
)
from forge_studio.composition import InlineExecutor, StudioComposition  # noqa: E402
from forge_studio.presentation import StudioPresentation  # noqa: E402
from forge_studio.source_api_adapter import SourceFrontendAdapter  # noqa: E402
from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.generation_port import (  # noqa: E402
    GenerationGateway,
    GenerationOutcome,
    PolicyGatedGenerator,
    RecordingGenerator,
)
from forge_headless.generation_request import (  # noqa: E402
    DEFAULT_DISTILLED_CFG_SCALE,
    DEFAULT_SAMPLER,
    DEFAULT_SCHEDULER,
    ResidentModel,
)
from forge_headless.headless_progress import JobState as HeadlessJobState  # noqa: E402
from forge_headless.studio_adapter import (  # noqa: E402
    HeadlessBackendAdapter,
    HeadlessBackendError,
)
from forge_headless.studio_generation import (  # noqa: E402
    BACKEND_DEFAULTED_FIELDS,
    BACKEND_NOT_READY,
    BACKEND_SHUTDOWN,
    CLEANUP_FAILED,
    GENERATION_CANCELLED,
    GENERATION_FAILED,
    JOB_NOT_FOUND,
    MODEL_NOT_SELECTED,
    PINNED_OFF_FLAGS,
    REQUEST_UNSUPPORTED,
    RESULT_PUBLICATION_FAILED,
    STATE_PROJECTION,
    STUDIO_ERROR_CODES,
    STUDIO_REQUEST_FIELDS,
    HeadlessGenerationSession,
    project_state,
    studio_code_for,
    translate_request,
)


TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8ffff3f0005fe02fea735cd900000000049454e"
    "44ae426082"
)

MODEL_ID = "headless-session-model"
RESULT_NAME = "generated.png"


def _outcome(**overrides: object) -> GenerationOutcome:
    fields: dict[str, object] = {
        "job_id": "outcome-job",
        "request_id": "outcome-request",
        "result_relative_location": RESULT_NAME,
        "media_type": "image/png",
        "width": 768,
        "height": 768,
        "seed": 123456789,
    }
    fields.update(overrides)
    return GenerationOutcome(**fields)  # type: ignore[arg-type]


def _request(**overrides: object) -> GenerationRequest:
    fields: dict[str, object] = {
        "model_id": MODEL_ID,
        "positive_prompt": "a contained synthetic subject",
        "negative_prompt": "",
        "seed": 123456789,
        "steps": 4,
        "cfg_scale": 6.0,
        "width": 768,
        "height": 768,
    }
    fields.update(overrides)
    return GenerationRequest(**fields)  # type: ignore[arg-type]


class DeferringExecutor:
    """Capture submitted work so a test can cancel before it runs."""

    def __init__(self) -> None:
        self.pending: list = []

    def submit(self, work) -> None:
        self.pending.append(work)

    def run_all(self) -> int:
        count = 0
        while self.pending:
            self.pending.pop(0)()
            count += 1
        return count


class _Base(unittest.TestCase):
    """Every composition gets its own contained result root."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / "results"
        self.cleanup_calls: list[int] = []

    def session(
        self,
        *,
        generator: object | None = None,
        resident: bool = True,
        result_root: Path | None = -1,  # type: ignore[assignment]
        result_writer: object | None = -1,  # type: ignore[assignment]
        executor: object | None = None,
        cleanup: object | None = None,
        **kwargs: object,
    ) -> HeadlessGenerationSession:
        if generator is None:
            generator = RecordingGenerator(outcome=_outcome(), total_steps=4)
        root = self.root if result_root == -1 else result_root
        writer = (
            (lambda outcome, path: path.write_bytes(TINY_PNG))
            if result_writer == -1
            else result_writer
        )
        return HeadlessGenerationSession(
            gateway=GenerationGateway(generator),
            resident_model=ResidentModel(
                model_id=MODEL_ID, family="anima", resident=resident
            ),
            result_root=root,
            result_writer=writer,  # type: ignore[arg-type]
            executor=executor,
            cleanup=cleanup,
            **kwargs,  # type: ignore[arg-type]
        )

    def composition(self, session: HeadlessGenerationSession) -> StudioComposition:
        composition = build_standalone(
            backend_kind=HEADLESS_BACKEND,
            result_root=self.root,
            headless_generation=session,
        )
        self.addCleanup(lambda: self._safe_shutdown(composition))
        return composition

    @staticmethod
    def _safe_shutdown(composition: StudioComposition) -> None:
        try:
            composition.shutdown()
        except BaseException:  # noqa: BLE001 - cleanup-error tests expect this
            pass

    def run_to_completion(self, composition: StudioComposition, **overrides: object):
        application = composition.application
        job = application.submit_generation(_request(**overrides))
        return job, application.poll_or_stream_progress(job.job_id)


# ------------------------------------------------------------ 10.1 selection


class BackendSelectionTests(_Base):
    def test_backend_kinds_are_closed(self) -> None:
        self.assertEqual((MOCK_BACKEND, HEADLESS_BACKEND), BACKEND_KINDS)

    def test_mock_is_selected_explicitly(self) -> None:
        composition = build_standalone(
            backend_kind=MOCK_BACKEND, result_root=self.root
        )
        self.addCleanup(composition.shutdown)
        readiness = composition.readiness()
        self.assertEqual("studio-alpha-mock", readiness.backend_id)
        self.assertTrue(readiness.backend_is_mock)
        self.assertEqual(MOCK_BACKEND, readiness.host_metadata["backend_kind"])

    def test_headless_is_selected_explicitly(self) -> None:
        composition = self.composition(self.session())
        readiness = composition.readiness()
        self.assertEqual("forge-headless", readiness.backend_id)
        self.assertFalse(readiness.backend_is_mock)
        self.assertEqual(HEADLESS_BACKEND, readiness.host_metadata["backend_kind"])
        self.assertTrue(readiness.host_metadata["headless_generation_injected"])

    def test_unknown_backend_fails_closed(self) -> None:
        with self.assertRaises(StudioError) as raised:
            build_standalone(backend_kind="quantum", result_root=self.root)
        self.assertEqual("BACKEND_NOT_WIRED", raised.exception.error.code)

    def test_there_is_no_silent_fallback_from_headless_to_mock(self) -> None:
        composition = self.composition(self.session())
        self.assertNotEqual(
            "studio-alpha-mock", composition.readiness().backend_id
        )
        self.assertFalse(composition.readiness().backend_is_mock)

    def test_a_factory_still_overrides_the_kind(self) -> None:
        built: list[str] = []

        def factory(services):
            built.append("factory")
            from forge_studio.mock_backend import MockBackend

            return MockBackend(result_directory=services.result_root)

        composition = build_standalone(
            backend_kind=HEADLESS_BACKEND,
            backend_factory=factory,
            result_root=self.root,
        )
        self.addCleanup(composition.shutdown)
        self.assertEqual(["factory"], built)

    def test_the_phase_one_backend_name_spelling_still_works(self) -> None:
        composition = build_standalone(
            backend_name=MOCK_BACKEND, result_root=self.root
        )
        self.addCleanup(composition.shutdown)
        self.assertEqual(MOCK_BACKEND, composition.readiness().host_metadata["backend_kind"])

    def test_selection_is_not_read_from_the_environment(self) -> None:
        tree = ast.parse(COMPOSITION_SOURCE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "StandaloneHost":
                names = {
                    child.attr
                    for child in ast.walk(node)
                    if isinstance(child, ast.Attribute)
                }
                self.assertNotIn("environ", names)
                self.assertNotIn("getenv", names)

    def test_headless_import_is_deferred_to_selection(self) -> None:
        tree = ast.parse(COMPOSITION_SOURCE.read_text(encoding="utf-8"))
        module_level = {
            alias.name.partition(".")[0]
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        module_level |= {
            node.module.partition(".")[0]
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
        }
        self.assertNotIn("forge_headless", module_level)


# ------------------------------------------------------- 10.2 real contract


class RealAdapterContractTests(_Base):
    def test_the_selected_backend_is_the_real_adapter(self) -> None:
        composition = self.composition(self.session())
        backend = composition.application._backend  # noqa: SLF001
        self.assertIsInstance(backend, HeadlessBackendAdapter)

    def test_status_reports_the_session(self) -> None:
        composition = self.composition(self.session())
        status = composition.application.get_backend_status()
        self.assertEqual("forge-headless", status.backend_id)
        self.assertTrue(status.ready)
        self.assertTrue(status.model_loaded)
        self.assertFalse(status.is_mock)
        self.assertFalse(status.cuda_initialized)
        self.assertFalse(status.network_access)

    def test_models_include_the_resident_session(self) -> None:
        composition = self.composition(self.session())
        models = composition.application.list_models()
        self.assertEqual(1, len(models))
        self.assertEqual(MODEL_ID, models[0].model_id)
        self.assertFalse(models[0].is_mock)

    def test_residency_reports_the_session(self) -> None:
        composition = self.composition(self.session())
        residency = composition.application.get_current_model()
        self.assertTrue(residency.loaded)
        self.assertEqual(MODEL_ID, residency.model_id)
        self.assertFalse(residency.is_mock)

    def test_capability_comes_from_the_session(self) -> None:
        composition = self.composition(self.session())
        capability = composition.application.get_capability(MODEL_ID, "txt2img")
        # 8, the VAE factor -- not 16. The patch size is not the caller's
        # problem: the model pads the latent to its patch grid and crops the
        # output back (backend/nn/anima.py:478 and :510).
        self.assertEqual(8, capability.dimension_alignment)
        self.assertFalse(capability.is_mock)
        self.assertFalse(capability.normalizes_dimensions)

    def test_supported_parameters_are_the_eight_studio_fields(self) -> None:
        composition = self.composition(self.session())
        self.assertEqual(
            frozenset(STUDIO_REQUEST_FIELDS),
            composition.application.supported_generation_parameters(),
        )

    def test_without_a_session_the_adapter_still_refuses(self) -> None:
        """The Phase 2A read-only contract is preserved, not replaced."""

        adapter = HeadlessBackendAdapter(None)
        for call in (
            lambda: adapter.submit_generation(_request()),
            lambda: adapter.poll_or_stream_progress("x"),
            lambda: adapter.cancel_generation("x"),
            lambda: adapter.get_result("x"),
        ):
            with self.assertRaises(HeadlessBackendError) as raised:
                call()
            self.assertEqual(
                "HEADLESS_BACKEND_NOT_READY", raised.exception.error.code
            )
        self.assertEqual(frozenset(), adapter.supported_generation_parameters())

    def test_the_default_port_refuses_after_full_validation(self) -> None:
        session = HeadlessGenerationSession(
            resident_model=ResidentModel(
                model_id=MODEL_ID, family="anima", resident=True
            ),
            result_root=self.root,
        )
        self.assertIsInstance(session._gateway.port, PolicyGatedGenerator)  # noqa: SLF001
        composition = self.composition(session)
        job = composition.application.submit_generation(_request())
        event = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.FAILED, event.state)


# ------------------------------------------------------ 10.3 request fidelity


class RequestFidelityTests(_Base):
    def test_every_studio_field_is_carried_verbatim(self) -> None:
        request = _request(
            positive_prompt="p", negative_prompt="n", seed=7, steps=9,
            cfg_scale=5.5, width=512, height=640,
        )
        translation = translate_request(request, request_id="rid")
        headless = translation.headless_request
        self.assertEqual(request.model_id, headless.model_id)
        self.assertEqual(request.positive_prompt, headless.positive_prompt)
        self.assertEqual(request.negative_prompt, headless.negative_prompt)
        self.assertEqual(request.seed, headless.seed)
        self.assertEqual(request.steps, headless.steps)
        self.assertEqual(request.cfg_scale, headless.cfg_scale)
        self.assertEqual(request.width, headless.width)
        self.assertEqual(request.height, headless.height)
        self.assertEqual(STUDIO_REQUEST_FIELDS, translation.carried)

    def test_backend_defaulted_fields_are_reported_not_invented(self) -> None:
        translation = translate_request(_request(), request_id="rid")
        headless = translation.headless_request
        self.assertEqual(DEFAULT_SAMPLER, headless.sampler)
        self.assertEqual(DEFAULT_SCHEDULER, headless.scheduler)
        self.assertEqual(DEFAULT_DISTILLED_CFG_SCALE, headless.distilled_cfg_scale)
        self.assertEqual(1, headless.batch_size)
        self.assertEqual(1, headless.output_count)
        self.assertEqual(BACKEND_DEFAULTED_FIELDS, translation.backend_defaulted)

    def test_tier0_feature_flags_are_pinned_off(self) -> None:
        headless = translate_request(_request(), request_id="rid").headless_request
        self.assertFalse(headless.enable_hr)
        self.assertFalse(headless.enable_adetailer)
        self.assertFalse(headless.enable_extensions)
        self.assertFalse(headless.reference_image_enabled)
        self.assertEqual(PINNED_OFF_FLAGS, translation_flags())

    def test_a_random_seed_is_refused_rather_than_substituted(self) -> None:
        with self.assertRaises(HeadlessError) as raised:
            translate_request(_request(seed=-1), request_id="rid")
        self.assertEqual("GENERATION_SEED_NOT_FIXED", raised.exception.code)

    def test_the_translated_record_carries_no_prompt_text(self) -> None:
        translation = translate_request(
            _request(positive_prompt="secret words"), request_id="rid"
        )
        rendered = json.dumps(translation.to_dict())
        self.assertNotIn("secret words", rendered)
        self.assertIn("positive_prompt_length", rendered)

    def test_the_request_reaches_the_port_unchanged(self) -> None:
        generator = RecordingGenerator(outcome=_outcome(), total_steps=4)
        composition = self.composition(self.session(generator=generator))
        composition.application.submit_generation(_request(seed=99, steps=6))
        self.assertEqual(1, len(generator.requests))
        self.assertEqual(99, generator.requests[0].seed)
        self.assertEqual(6, generator.requests[0].steps)

    def test_a_round_trip_through_the_source_vocabulary_preserves_fields(self) -> None:
        generator = RecordingGenerator(outcome=_outcome(), total_steps=4)
        composition = self.composition(self.session(generator=generator))
        presentation = StudioPresentation(composition.application, GenerationRequest)
        presentation.submit(
            {
                "model": MODEL_ID,
                "positive_prompt": "round trip",
                "negative_prompt": "",
                "seed": 4242,
                "steps": 5,
                "cfg_scale": 6.0,
                "width": 512,
                "height": 512,
            }
        )
        recorded = generator.requests[0]
        self.assertEqual(4242, recorded.seed)
        self.assertEqual(5, recorded.steps)
        self.assertEqual(512, recorded.width)
        self.assertEqual("round trip", recorded.positive_prompt)


def translation_flags() -> tuple[str, ...]:
    return PINNED_OFF_FLAGS


# ----------------------------------------------------- 10.4 progress fidelity


class ProgressFidelityTests(_Base):
    def test_every_headless_state_projects_onto_a_studio_state(self) -> None:
        for state in HeadlessJobState:
            self.assertIn(state, STATE_PROJECTION)
        self.assertIs(JobState.QUEUED, project_state(HeadlessJobState.QUEUED))
        for running in (
            HeadlessJobState.LOADING,
            HeadlessJobState.CONDITIONING,
            HeadlessJobState.SAMPLING,
            HeadlessJobState.DECODING,
            HeadlessJobState.PUBLISHING,
        ):
            self.assertIs(JobState.RUNNING, project_state(running))
        self.assertIs(JobState.COMPLETED, project_state(HeadlessJobState.COMPLETED))
        self.assertIs(JobState.CANCELLED, project_state(HeadlessJobState.CANCELLED))
        self.assertIs(JobState.FAILED, project_state(HeadlessJobState.FAILED))

    def test_the_stage_survives_the_five_state_projection(self) -> None:
        composition = self.composition(self.session())
        job, event = self.run_to_completion(composition)
        self.assertIs(JobState.COMPLETED, event.state)
        self.assertEqual("Completed", event.message)

    def test_progress_reaches_one_hundred_on_completion(self) -> None:
        composition = self.composition(self.session())
        _job, event = self.run_to_completion(composition)
        self.assertEqual(100, event.progress)
        self.assertEqual(4, event.step)
        self.assertEqual(4, event.total_steps)

    def test_polling_is_non_consuming(self) -> None:
        composition = self.composition(self.session())
        job, first = self.run_to_completion(composition)
        for _ in range(4):
            repeat = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(first.state, repeat.state)
        self.assertEqual(first.progress, repeat.progress)
        self.assertGreater(repeat.sequence, first.sequence)

    def test_a_deferred_job_reports_queued_before_it_runs(self) -> None:
        executor = DeferringExecutor()
        composition = self.composition(self.session(executor=executor))
        job = composition.application.submit_generation(_request())
        queued = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.QUEUED, queued.state)
        self.assertEqual(0, queued.progress)
        executor.run_all()
        done = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.COMPLETED, done.state)

    def test_progress_never_regresses_across_a_deferred_run(self) -> None:
        executor = DeferringExecutor()
        composition = self.composition(self.session(executor=executor))
        job = composition.application.submit_generation(_request())
        seen = [composition.application.poll_or_stream_progress(job.job_id).progress]
        executor.run_all()
        seen.append(composition.application.poll_or_stream_progress(job.job_id).progress)
        self.assertEqual(sorted(seen), seen)

    def test_no_step_is_claimed_before_the_port_reports_it(self) -> None:
        executor = DeferringExecutor()
        composition = self.composition(self.session(executor=executor))
        job = composition.application.submit_generation(_request())
        before = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIsNone(before.step)
        self.assertIsNone(before.total_steps)


# --------------------------------------------------- 10.5 cancellation matrix


class CancellationMatrixTests(_Base):
    def test_cancel_before_the_model_load_stage(self) -> None:
        executor = DeferringExecutor()
        composition = self.composition(self.session(executor=executor))
        job = composition.application.submit_generation(_request())
        result = composition.application.cancel_generation(job.job_id)
        self.assertTrue(result.cancelled)
        self.assertIs(JobState.CANCELLED, result.state)

    def test_a_job_cancelled_before_start_never_runs(self) -> None:
        executor = DeferringExecutor()
        generator = RecordingGenerator(outcome=_outcome(), total_steps=4)
        composition = self.composition(
            self.session(generator=generator, executor=executor)
        )
        job = composition.application.submit_generation(_request())
        composition.application.cancel_generation(job.job_id)
        executor.run_all()
        self.assertEqual(
            [], generator.requests, "a cancelled job must not reach the port"
        )
        self.assertIs(
            JobState.CANCELLED,
            composition.application.poll_or_stream_progress(job.job_id).state,
        )

    def test_cancel_during_synthetic_sampling(self) -> None:
        generator = RecordingGenerator(
            outcome=_outcome(), total_steps=8, cancel_at_step=3
        )
        composition = self.composition(self.session(generator=generator))
        job = composition.application.submit_generation(_request())
        event = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.CANCELLED, event.state)
        self.assertLess(
            len(generator.snapshots), 8, "sampling must stop at the cancellation"
        )

    def test_duplicate_cancellation_is_idempotent(self) -> None:
        executor = DeferringExecutor()
        composition = self.composition(self.session(executor=executor))
        job = composition.application.submit_generation(_request())
        first = composition.application.cancel_generation(job.job_id)
        second = composition.application.cancel_generation(job.job_id)
        self.assertTrue(first.cancelled)
        self.assertFalse(second.cancelled)
        self.assertIs(JobState.CANCELLED, second.state)

    def test_cancel_after_a_terminal_state_is_refused_truthfully(self) -> None:
        composition = self.composition(self.session())
        job, _event = self.run_to_completion(composition)
        result = composition.application.cancel_generation(job.job_id)
        self.assertFalse(result.cancelled)
        self.assertIs(JobState.COMPLETED, result.state)

    def test_cancelling_an_unknown_job_is_a_structured_error(self) -> None:
        composition = self.composition(self.session())
        with self.assertRaises(HeadlessBackendError) as raised:
            composition.application._backend.cancel_generation("no-such-job")  # noqa: SLF001
        self.assertEqual(JOB_NOT_FOUND, raised.exception.error.code)

    def test_cancellation_reaches_the_headless_progress_object(self) -> None:
        executor = DeferringExecutor()
        session = self.session(executor=executor)
        composition = self.composition(session)
        job = composition.application.submit_generation(_request())
        composition.application.cancel_generation(job.job_id)
        record = session._jobs[job.job_id]  # noqa: SLF001
        self.assertTrue(record.progress_view.cancellation_requested)
        self.assertIs(HeadlessJobState.CANCELLED, record.progress_view.state)


# --------------------------------------------------------- 10.6 error matrix


class ErrorMatrixTests(_Base):
    def _assert_scalar(self, error) -> None:
        self.assertIn(error.code, STUDIO_ERROR_CODES)
        self.assertIsInstance(error.message, str)
        self.assertTrue(error.field is None or isinstance(error.field, str))
        rendered = json.dumps(error.to_dict())
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("Traceback", rendered)

    def test_every_headless_code_maps_to_a_stable_studio_code(self) -> None:
        for headless in (
            "GENERATION_CANCELLED",
            "GENERATION_STEPS_OUT_OF_RANGE",
            "GENERATION_MODEL_NOT_RESIDENT",
            "HEADLESS_GENERATION_NOT_AUTHORIZED",
            "GENERATION_BACKEND_FAILED",
            "SOMETHING_NOBODY_LISTED",
        ):
            self.assertIn(studio_code_for(headless), STUDIO_ERROR_CODES)

    def test_model_not_selected(self) -> None:
        composition = self.composition(self.session(resident=False))
        with self.assertRaises(HeadlessBackendError) as raised:
            composition.application._backend.submit_generation(_request())  # noqa: SLF001
        self.assertEqual(MODEL_NOT_SELECTED, raised.exception.error.code)
        self._assert_scalar(raised.exception.error)

    def test_request_unsupported_for_a_random_seed(self) -> None:
        composition = self.composition(self.session())
        with self.assertRaises(HeadlessBackendError) as raised:
            composition.application._backend.submit_generation(_request(seed=-1))  # noqa: SLF001
        self.assertEqual(REQUEST_UNSUPPORTED, raised.exception.error.code)
        self.assertEqual("seed", raised.exception.error.field)
        self._assert_scalar(raised.exception.error)

    def test_a_misaligned_dimension_is_not_an_error_at_all(self) -> None:
        """Inverted 2026-08-20 by owner ruling.

        This asserted FAILED for 513x513. Studio's core needs no alignment --
        `modules/processing.py:968` floor-divides and refuses nothing -- and
        the owner verified it end to end: 500x500 yields a 496x496 image,
        silently, which he ruled is fine.

        Kept in the error matrix, inverted, rather than deleted: this row is
        now what stops the refusal being reintroduced as an "error case".
        """

        composition = self.composition(self.session())
        job = composition.application.submit_generation(_request(width=513, height=513))
        event = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIsNot(JobState.FAILED, event.state)
        self.assertIsNone(getattr(event, "error", None))

    def test_generation_failed(self) -> None:
        generator = RecordingGenerator(
            outcome=_outcome(), total_steps=3, fail_with="synthetic failure"
        )
        composition = self.composition(self.session(generator=generator))
        job = composition.application.submit_generation(_request())
        event = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.FAILED, event.state)
        self.assertIsNotNone(event.error)
        self._assert_scalar(event.error)

    def test_generation_cancelled_is_a_distinct_terminal_state(self) -> None:
        generator = RecordingGenerator(
            outcome=_outcome(), total_steps=6, cancel_at_step=2
        )
        composition = self.composition(self.session(generator=generator))
        job = composition.application.submit_generation(_request())
        self.assertIs(
            JobState.CANCELLED,
            composition.application.poll_or_stream_progress(job.job_id).state,
        )
        with self.assertRaises(HeadlessBackendError) as raised:
            composition.application._backend.get_result(job.job_id)  # noqa: SLF001
        self.assertEqual(GENERATION_CANCELLED, raised.exception.error.code)

    def test_result_publication_failed_without_a_result_root(self) -> None:
        session = self.session(result_root=None, result_writer=None)
        composition = build_standalone(
            backend_kind=HEADLESS_BACKEND, headless_generation=session
        )
        self.addCleanup(lambda: self._safe_shutdown(composition))
        job = composition.application.submit_generation(_request())
        event = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(JobState.FAILED, event.state)

    def test_result_publication_failed_when_bytes_never_appear(self) -> None:
        composition = self.composition(self.session(result_writer=None))
        job, _event = self.run_to_completion(composition)
        with self.assertRaises(HeadlessBackendError) as raised:
            composition.application._backend.get_result(job.job_id)  # noqa: SLF001
        self.assertEqual(RESULT_PUBLICATION_FAILED, raised.exception.error.code)
        self._assert_scalar(raised.exception.error)

    def test_backend_shutdown_after_close(self) -> None:
        session = self.session()
        composition = self.composition(session)
        composition.shutdown()
        with self.assertRaises(HeadlessBackendError) as raised:
            session.submit(_request())
        self.assertEqual(BACKEND_SHUTDOWN, raised.exception.error.code)
        self._assert_scalar(raised.exception.error)

    def test_cleanup_failed_is_reported_not_swallowed(self) -> None:
        def raising() -> None:
            raise RuntimeError("cleanup exploded")

        session = self.session(cleanup=raising)
        with self.assertRaises(HeadlessBackendError) as raised:
            session.close()
        self.assertEqual(CLEANUP_FAILED, raised.exception.error.code)
        self.assertNotIn("exploded", raised.exception.error.message)
        self._assert_scalar(raised.exception.error)

    def test_the_authorization_flag_is_validation_level_and_the_port_decides(self) -> None:
        """A finding worth pinning rather than assuming.

        `generation_authorized=False` adds HEADLESS_GENERATION_NOT_AUTHORIZED to
        the validation problems, but `GenerationGateway.submit` deliberately
        filters that code out of its blocking set and still calls the port --
        the whole point of the "refuse last" design. So the flag alone does not
        stop an injected port; the port does.

        This is why a session with no injected port keeps PolicyGatedGenerator,
        and why that is the property that actually gates generation.
        """

        session = self.session(generation_authorized=False)
        composition = self.composition(session)
        job = composition.application.submit_generation(_request())
        event = composition.application.poll_or_stream_progress(job.job_id)
        self.assertIs(
            JobState.COMPLETED,
            event.state,
            "the flag is validation-level; an injected port still runs",
        )

        gated = HeadlessGenerationSession(
            resident_model=ResidentModel(
                model_id=MODEL_ID, family="anima", resident=True
            ),
            result_root=self.root,
            generation_authorized=True,
        )
        gated_composition = self.composition(gated)
        gated_job = gated_composition.application.submit_generation(_request())
        self.assertIs(
            JobState.FAILED,
            gated_composition.application.poll_or_stream_progress(gated_job.job_id).state,
            "the default port refuses even when validation authorizes",
        )

    def test_job_not_found_for_an_unknown_job(self) -> None:
        composition = self.composition(self.session())
        with self.assertRaises(HeadlessBackendError) as raised:
            composition.application._backend.poll_or_stream_progress("nope")  # noqa: SLF001
        self.assertEqual(JOB_NOT_FOUND, raised.exception.error.code)
        self.assertEqual(404, raised.exception.http_status)

    def test_no_error_record_retains_an_exception_or_traceback(self) -> None:
        generator = RecordingGenerator(
            outcome=_outcome(), total_steps=2, fail_with="boom"
        )
        session = self.session(generator=generator)
        composition = self.composition(session)
        job = composition.application.submit_generation(_request())
        record = session._jobs[job.job_id]  # noqa: SLF001
        failure = record.failure
        self.assertIsNotNone(failure)
        for attribute in ("__traceback__", "__cause__", "__context__"):
            self.assertFalse(hasattr(failure, attribute))


# ------------------------------------------------------- 10.7 result delivery


class ResultDeliveryTests(_Base):
    def test_a_contained_result_reaches_an_opaque_handle(self) -> None:
        composition = self.composition(self.session())
        job, _event = self.run_to_completion(composition)
        asset = composition.application.result_asset(job.job_id)
        self.assertIsNotNone(asset)
        self.assertTrue(asset.handle.startswith("studio-result/"))
        self.assertEqual("image/png", asset.media_type)

    def test_no_path_leaks_through_the_handle_or_the_metadata(self) -> None:
        composition = self.composition(self.session())
        job, _event = self.run_to_completion(composition)
        asset = composition.application.result_asset(job.job_id)
        result = composition.application.get_result(job.job_id)
        self.assertNotIn(str(self.root), asset.handle)
        self.assertNotIn(str(self.root), json.dumps(result.metadata))
        self.assertNotIn(RESULT_NAME, asset.handle)

    def test_bytes_resolve_identically(self) -> None:
        composition = self.composition(self.session())
        job, _event = self.run_to_completion(composition)
        asset = composition.application.result_asset(job.job_id)
        payload = composition.application.read_result_asset(asset.handle)
        self.assertEqual(TINY_PNG, payload.content)
        self.assertEqual("image/png", payload.media_type)

    def test_the_result_survives_adapter_cleanup(self) -> None:
        composition = self.composition(self.session())
        job, _event = self.run_to_completion(composition)
        asset = composition.application.result_asset(job.job_id)
        composition.application._backend._generation.close()  # noqa: SLF001
        payload = composition.application.read_result_asset(asset.handle)
        self.assertEqual(TINY_PNG, payload.content)

    def test_the_adapter_returns_no_forge_or_tensor_objects(self) -> None:
        composition = self.composition(self.session())
        job, _event = self.run_to_completion(composition)
        result = composition.application.get_result(job.job_id)
        rendered = json.dumps(result.to_dict(), default=str)
        for forbidden in ("Processed", "GenerationOutcome", "Tensor", "PIL"):
            self.assertNotIn(forbidden, rendered)

    def test_result_metadata_is_scalar_and_declares_its_schema(self) -> None:
        composition = self.composition(self.session())
        job, _event = self.run_to_completion(composition)
        metadata = composition.application.get_result(job.job_id).metadata
        self.assertEqual("studio-headless-result/v1", metadata["schema_version"])
        for value in metadata.values():
            self.assertIsInstance(value, (str, int, float, bool))


# ---------------------------------------------- 10.8 frontend/service parity


class FrontendServiceEquivalenceTests(_Base):
    def _presentation(self, composition: StudioComposition) -> StudioPresentation:
        return StudioPresentation(composition.application, GenerationRequest)

    def test_the_canonical_studio_vocabulary_reaches_the_headless_adapter(self) -> None:
        generator = RecordingGenerator(outcome=_outcome(), total_steps=4)
        composition = self.composition(self.session(generator=generator))
        presentation = self._presentation(composition)
        submitted = presentation.submit(
            {
                "model": MODEL_ID, "positive_prompt": "canonical",
                "negative_prompt": "", "seed": 11, "steps": 4,
                "cfg_scale": 6.0, "width": 512, "height": 512,
            }
        )
        self.assertTrue(submitted["job_id"])
        polled = presentation.poll(submitted["job_id"])
        self.assertEqual("completed", polled["state"])
        self.assertIn("image_handle", polled["result"])
        self.assertEqual(1, len(generator.requests))

    def test_the_compatibility_source_vocabulary_reaches_the_same_adapter(self) -> None:
        generator = RecordingGenerator(outcome=_outcome(), total_steps=4)
        composition = self.composition(self.session(generator=generator))
        adapter = SourceFrontendAdapter(self._presentation(composition))
        response = adapter.generate(
            {
                "action": "generate", "prompt": "compatibility",
                "negative_prompt": "", "seed": 11, "steps": 4,
                "cfg_scale": 6.0, "width": 512, "height": 512,
            }
        )
        self.assertIsInstance(response, dict)
        self.assertEqual(1, len(generator.requests))

    def test_both_vocabularies_use_the_same_backend_instance(self) -> None:
        composition = self.composition(self.session())
        presentation = self._presentation(composition)
        adapter = SourceFrontendAdapter(presentation)
        self.assertIs(
            presentation._application,  # noqa: SLF001
            adapter._presentation._application,  # noqa: SLF001
        )
        self.assertIsInstance(
            composition.application._backend, HeadlessBackendAdapter  # noqa: SLF001
        )

    def test_the_canonical_response_exposes_no_filesystem_path(self) -> None:
        composition = self.composition(self.session())
        presentation = self._presentation(composition)
        submitted = presentation.submit(
            {
                "model": MODEL_ID, "positive_prompt": "no paths",
                "negative_prompt": "", "seed": 12, "steps": 4,
                "cfg_scale": 6.0, "width": 512, "height": 512,
            }
        )
        polled = presentation.poll(submitted["job_id"])
        rendered = json.dumps(polled, default=str)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("output_path", rendered)
        self.assertNotIn("metadata_path", rendered)

    def test_the_runtime_status_route_reports_the_headless_selection(self) -> None:
        composition = self.composition(self.session())
        status = self._presentation(composition).backend_status()
        self.assertEqual("forge-headless", status["backend_id"])
        self.assertFalse(status["is_mock"])


# -------------------------------------------------------------- 10.9 shutdown


class ShutdownTests(_Base):
    def test_shutdown_before_use(self) -> None:
        composition = self.composition(self.session())
        composition.shutdown()
        self.assertTrue(composition.closed)

    def test_shutdown_after_success(self) -> None:
        composition = self.composition(self.session())
        self.run_to_completion(composition)
        composition.shutdown()
        self.assertTrue(composition.closed)

    def test_shutdown_after_failure(self) -> None:
        generator = RecordingGenerator(
            outcome=_outcome(), total_steps=2, fail_with="boom"
        )
        composition = self.composition(self.session(generator=generator))
        composition.application.submit_generation(_request())
        composition.shutdown()
        self.assertTrue(composition.closed)

    def test_shutdown_with_a_pending_job_terminates_it(self) -> None:
        executor = DeferringExecutor()
        session = self.session(executor=executor)
        composition = self.composition(session)
        job = composition.application.submit_generation(_request())
        record = session._jobs[job.job_id]  # noqa: SLF001
        composition.shutdown()
        self.assertTrue(record.progress_view.terminal)
        self.assertIs(HeadlessJobState.CANCELLED, record.progress_view.state)

    def test_double_shutdown_is_idempotent(self) -> None:
        session = self.session(cleanup=lambda: self.cleanup_calls.append(1))
        composition = self.composition(session)
        composition.shutdown()
        composition.shutdown()
        composition.shutdown()
        self.assertEqual([1], self.cleanup_calls)

    def test_a_cleanup_error_still_releases_the_runtime(self) -> None:
        released: list[str] = []

        class Runtime:
            def shutdown(self) -> None:
                released.append("runtime")

        def raising() -> None:
            raise RuntimeError("cleanup exploded")

        adapter = HeadlessBackendAdapter(
            Runtime(), generation=self.session(cleanup=raising)
        )
        with self.assertRaises(HeadlessBackendError):
            adapter.shutdown()
        self.assertEqual(
            ["runtime"],
            released,
            "a raising cleanup hook must not strand the runtime",
        )


# ------------------------------------------------------ 9. import safety


def _probe(body: str) -> dict[str, object]:
    prologue = f"import json, sys\nsys.path.insert(0, {str(APP_ROOT)!r})\n"
    epilogue = (
        "names = sorted(sys.modules)\n"
        "print(json.dumps({\n"
        '    "gradio": [n for n in names if n == "gradio" or n.startswith("gradio.")],\n'
        '    "neo": [n for n in names if n in ("modules", "modules_forge", "webui")\n'
        '            or n.startswith(("modules.", "modules_forge.", "webui."))],\n'
        '    "torch": [n for n in names if n == "torch" or n.startswith("torch.")],\n'
        '    "cuda": [n for n in names if "cuda" in n.casefold()],\n'
        '    "forge_backend": [n for n in names if n == "backend" or n.startswith("backend.")],\n'
        '    "headless": [n for n in names if n.startswith("forge_headless")],\n'
        '    "socketserver": [n for n in names if n == "socketserver"],\n'
        "}))\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-I", "-S", "-B", "-c",
         prologue + textwrap.dedent(body).strip() + "\n" + epilogue],
        cwd=str(APP_ROOT), capture_output=True, text=True, timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-2000:])
    return json.loads(completed.stdout.strip().splitlines()[-1])


class ImportSafetyTests(unittest.TestCase):
    def test_mock_selection_imports_no_headless_or_backend_stack(self) -> None:
        report = _probe(
            "from forge_studio.composition import build_standalone\n"
            "c = build_standalone(backend_kind='mock')\n"
            "c.readiness()\n"
            "c.shutdown()"
        )
        for key in ("gradio", "neo", "torch", "cuda", "forge_backend", "headless"):
            self.assertEqual([], report[key], key)

    def test_headless_selection_constructs_without_model_or_cuda(self) -> None:
        report = _probe(
            "from forge_studio.composition import build_standalone\n"
            "c = build_standalone(backend_kind='headless')\n"
            "c.readiness()\n"
            "c.shutdown()"
        )
        self.assertEqual([], report["gradio"])
        self.assertEqual([], report["neo"])
        self.assertEqual([], report["torch"])
        self.assertEqual([], report["cuda"])
        self.assertEqual([], report["forge_backend"])
        self.assertTrue(
            report["headless"], "selecting headless must import the adapter"
        )

    def test_headless_selection_binds_no_socket(self) -> None:
        report = _probe(
            "from forge_studio.composition import build_standalone\n"
            "c = build_standalone(backend_kind='headless')\n"
            "c.shutdown()"
        )
        self.assertEqual([], report["socketserver"])

    def test_importing_the_session_module_is_clean(self) -> None:
        report = _probe("import forge_headless.studio_generation")
        for key in ("gradio", "neo", "torch", "cuda", "forge_backend"):
            self.assertEqual([], report[key], key)

    def test_the_session_module_imports_only_stdlib_and_owned_packages(self) -> None:
        tree = ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.partition(".")[0])
        for forbidden in ("gradio", "modules", "modules_forge", "webui", "torch", "backend"):
            self.assertNotIn(forbidden, roots)

    def test_the_adapter_module_imports_no_runtime_stack(self) -> None:
        tree = ast.parse(ADAPTER_SOURCE.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.partition(".")[0])
        for forbidden in ("gradio", "modules", "modules_forge", "webui", "torch", "backend"):
            self.assertNotIn(forbidden, roots)


class SuiteIntegrityTests(unittest.TestCase):
    def test_expected_number_of_tests_are_discovered(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        self.assertEqual(
            loaded.countTestCases(),
            EXPECTED_HEADLESS_INTEGRATION_TESTS,
            "Headless integration test count changed: update "
            "EXPECTED_HEADLESS_INTEGRATION_TESTS deliberately, never to match",
        )

    def test_scope_is_declared(self) -> None:
        doc = sys.modules[__name__].__doc__ or ""
        for label in SCOPE_LABELS:
            self.assertIn(label, doc)


if __name__ == "__main__":
    unittest.main()
