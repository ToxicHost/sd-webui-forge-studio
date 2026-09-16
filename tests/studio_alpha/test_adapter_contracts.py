"""Owned contract surface a real ForgeBackendAdapter must implement.

Covers the five `MUST FIX BEFORE ADAPTER` items other than result delivery:
progress step reporting, model residency, adapter-declared supported
parameters, model capability, and backend shutdown.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _request_payload(model_id: str, **overrides: object) -> dict[str, object]:
    return {
        "model_id": model_id,
        "positive_prompt": "adapter contracts",
        "negative_prompt": "",
        "seed": 3,
        "steps": 20,
        "cfg_scale": 7.0,
        "width": 512,
        "height": 512,
        **overrides,
    }


class _Surface(unittest.TestCase):
    def setUp(self) -> None:
        self.studio = importlib.import_module("forge_studio")
        self.presentation_module = importlib.import_module(
            "forge_studio.presentation"
        )
        self.backend = self.studio.MockBackend(event_interval_seconds=0.001)
        self.application = self.studio.StudioApplication(self.backend)
        self.presentation = self.presentation_module.StudioPresentation(
            self.application,
            self.studio.GenerationRequest,
        )

    def _model_id(self) -> str:
        return self.application.list_models()[0].model_id

    def _run(self, **overrides: object) -> list[object]:
        request = self.studio.GenerationRequest(
            **_request_payload(self._model_id(), **overrides)
        )
        job_id = self.application.submit_generation(request).job_id
        events = []
        for _ in range(64):
            event = self.application.poll_or_stream_progress(job_id)
            events.append(event)
            if event.state.value in {"completed", "failed", "cancelled"}:
                break
        return events


class ProgressStepContractTests(_Surface):
    """M1 — ProgressEvent carries the backend's own step counters."""

    def test_progress_events_report_step_and_total_steps(self) -> None:
        events = self._run(steps=20)
        self.assertTrue(events)
        for event in events:
            self.assertEqual(20, event.total_steps)
            self.assertIsNotNone(event.step)
            self.assertGreaterEqual(event.step, 0)
            self.assertLessEqual(event.step, 20)

    def test_steps_are_monotonic_and_reach_the_total(self) -> None:
        events = self._run(steps=20)
        steps = [event.step for event in events]
        self.assertEqual(steps, sorted(steps))
        self.assertEqual(20, steps[-1])

    def test_total_steps_follows_the_request(self) -> None:
        for requested in (1, 7, 150):
            with self.subTest(steps=requested):
                events = self._run(steps=requested)
                self.assertEqual(requested, events[-1].total_steps)
                self.assertEqual(requested, events[-1].step)

    def test_step_fields_are_serialized(self) -> None:
        event = self._run(steps=12)[-1]
        payload = event.to_dict()
        self.assertEqual(12, payload["total_steps"])
        self.assertEqual(12, payload["step"])

    def test_absent_step_fields_default_to_none(self) -> None:
        """A backend that does not report steps stays contract-valid."""

        event = self.studio.ProgressEvent(
            "job",
            self.studio.JobState.RUNNING,
            1,
            50,
            "half",
        )
        self.assertIsNone(event.step)
        self.assertIsNone(event.total_steps)


class ProgressAdapterBridgeTests(_Surface):
    """M1 — the frontend bridge prefers contract values over its own math."""

    def setUp(self) -> None:
        super().setUp()
        self.adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        self.adapter = self.adapter_module.SourceFrontendAdapter(
            self.presentation
        )

    def test_bridge_uses_contract_step_when_reported(self) -> None:
        active = self.adapter_module._ActiveGeneration(
            job_id="job",
            steps=20,
            model_title="title",
            request=_request_payload("m"),
            ignored_parameters=(),
        )
        snapshot = self.adapter._record_progress(
            active,
            {
                "state": "running",
                "progress": 50,
                "message": "half",
                "step": 7,
                "total_steps": 20,
            },
        )
        # Percent-derived would be 10; the contract says 7.
        self.assertEqual(7, snapshot.step)
        self.assertEqual(20, snapshot.total_steps)

    def test_bridge_falls_back_when_steps_are_absent(self) -> None:
        active = self.adapter_module._ActiveGeneration(
            job_id="job",
            steps=20,
            model_title="title",
            request=_request_payload("m"),
            ignored_parameters=(),
        )
        snapshot = self.adapter._record_progress(
            active,
            {"state": "running", "progress": 50, "message": "half"},
        )
        self.assertEqual(10, snapshot.step)
        self.assertEqual(20, snapshot.total_steps)

    def test_bridge_ignores_malformed_step_values(self) -> None:
        active = self.adapter_module._ActiveGeneration(
            job_id="job",
            steps=20,
            model_title="title",
            request=_request_payload("m"),
            ignored_parameters=(),
        )
        for bad in (None, True, -1, "7", 3.5):
            with self.subTest(step=bad):
                snapshot = self.adapter._record_progress(
                    active,
                    {
                        "state": "running",
                        "progress": 50,
                        "message": "half",
                        "step": bad,
                    },
                )
                self.assertEqual(10, snapshot.step)


class ModelResidencyTests(_Surface):
    """M2 — residency is a backend fact, not frontend state."""

    def test_nothing_is_resident_initially(self) -> None:
        residency = self.application.get_current_model()
        self.assertIsNone(residency.model_id)
        self.assertFalse(residency.loaded)

    def test_explicit_load_establishes_residency(self) -> None:
        model_id = self._model_id()
        loaded = self.application.load_model(model_id)
        self.assertEqual(model_id, loaded.model_id)
        self.assertTrue(loaded.loaded)
        self.assertEqual(model_id, self.application.get_current_model().model_id)

    def test_load_is_idempotent(self) -> None:
        model_id = self._model_id()
        first = self.application.load_model(model_id)
        second = self.application.load_model(model_id)
        self.assertEqual(first, second)

    def test_reads_do_not_mutate_residency(self) -> None:
        for _ in range(3):
            self.assertIsNone(self.application.get_current_model().model_id)
        self.application.list_models()
        self.assertIsNone(self.application.get_current_model().model_id)

    def test_catalog_read_never_selects_a_model(self) -> None:
        """A5: listing models must not choose one."""

        self.application.list_models()
        self.assertFalse(self.application.get_current_model().loaded)

    def test_unload_clears_residency(self) -> None:
        self.application.load_model(self._model_id())
        cleared = self.application.unload_model()
        self.assertIsNone(cleared.model_id)
        self.assertFalse(cleared.loaded)

    def test_unload_is_idempotent(self) -> None:
        self.application.unload_model()
        self.assertFalse(self.application.unload_model().loaded)

    def test_unknown_model_is_a_structured_error(self) -> None:
        for candidate in ("nope", "", None, 7):
            with self.subTest(model_id=candidate):
                with self.assertRaises(self.studio.StudioError) as raised:
                    self.application.load_model(candidate)
                self.assertEqual(
                    "MODEL_NOT_AVAILABLE",
                    raised.exception.error.code,
                )

    def test_mock_declares_itself_mock(self) -> None:
        self.assertTrue(self.application.get_current_model().is_mock)

    def test_backend_status_reflects_residency(self) -> None:
        self.assertFalse(self.application.get_backend_status().model_loaded)
        self.application.load_model(self._model_id())
        self.assertTrue(self.application.get_backend_status().model_loaded)


class ResidencyBridgeTests(_Surface):
    """M2 — the frontend bridge reads residency from the backend."""

    def setUp(self) -> None:
        super().setUp()
        adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )
        self.adapter_module = adapter_module
        self.adapter = adapter_module.SourceFrontendAdapter(self.presentation)

    def test_bridge_reports_backend_residency(self) -> None:
        self.assertEqual("", self.adapter.current_model()["title"])
        models = self.adapter.get("/studio/models")
        self.adapter.post("/studio/load_model", {"title": models[0]["title"]})
        self.assertEqual(
            models[0]["title"],
            self.adapter.current_model()["title"],
        )

    def test_backend_side_load_is_visible_to_the_bridge(self) -> None:
        """Proves the bridge holds no private selection of its own."""

        self.application.load_model(self._model_id())
        self.assertNotEqual("", self.adapter.current_model()["title"])

    def test_backend_side_unload_is_visible_to_the_bridge(self) -> None:
        self.application.load_model(self._model_id())
        self.application.unload_model()
        self.assertEqual("", self.adapter.current_model()["title"])

    def test_bridge_unload_clears_backend_residency(self) -> None:
        self.application.load_model(self._model_id())
        self.adapter.post("/studio/unload_model", {})
        self.assertFalse(self.application.get_current_model().loaded)

    def test_generation_without_residency_fails_safely(self) -> None:
        with self.assertRaises(Exception) as raised:
            self.adapter.post(
                "/studio/generate",
                {"prompt": "no model", "steps": 20, "width": 64, "height": 64},
            )
        self.assertTrue(hasattr(raised.exception, "error"))

    def test_unknown_model_title_is_rejected_by_the_bridge(self) -> None:
        with self.assertRaises(Exception) as raised:
            self.adapter.post("/studio/load_model", {"title": "absent"})
        self.assertTrue(hasattr(raised.exception, "error"))


class SupportedParameterTests(_Surface):
    """M3 — the ignored-setting notice is backend-driven."""

    def setUp(self) -> None:
        super().setUp()
        self.adapter_module = importlib.import_module(
            "forge_studio.source_api_adapter"
        )

    def test_mock_declares_no_optional_parameters(self) -> None:
        self.assertEqual(
            frozenset(),
            self.application.supported_generation_parameters(),
        )

    def test_base_adapter_default_is_empty(self) -> None:
        self.assertEqual(
            frozenset(),
            self.studio.BackendAdapter.supported_generation_parameters(
                self.backend
            ),
        )

    def test_unsupported_settings_are_reported(self) -> None:
        ignored = self.adapter_module._ignored_generation_parameters(
            {"sampler": "Euler", "adetailer": True},
            frozenset(),
        )
        self.assertEqual(("sampler", "adetailer"), ignored)

    def test_backend_declared_support_silences_the_notice(self) -> None:
        ignored = self.adapter_module._ignored_generation_parameters(
            {"sampler": "Euler", "adetailer": True},
            frozenset({"sampler"}),
        )
        self.assertEqual(("adetailer",), ignored)

    def test_unknown_payload_keys_are_never_reported(self) -> None:
        ignored = self.adapter_module._ignored_generation_parameters(
            {"totally_unknown": "value", "another": 1},
            frozenset(),
        )
        self.assertEqual((), ignored)

    def test_notice_reflects_backend_support_end_to_end(self) -> None:
        class _Supporting(type(self.backend)):
            SUPPORTED_GENERATION_PARAMETERS = frozenset({"sampler"})

        application = self.studio.StudioApplication(
            _Supporting(event_interval_seconds=0.001)
        )
        surface = self.presentation_module.StudioPresentation(
            application,
            self.studio.GenerationRequest,
        )
        adapter = self.adapter_module.SourceFrontendAdapter(surface)
        models = adapter.get("/studio/models")
        adapter.post("/studio/load_model", {"title": models[0]["title"]})
        response = adapter.post(
            "/studio/generate",
            {
                "prompt": "notice",
                "steps": 20,
                "width": 64,
                "height": 64,
                "sampler": "Euler",
                "adetailer": True,
            },
        )
        self.assertIsNotNone(response["notice"])
        self.assertNotIn("sampler", response["notice"])
        self.assertIn("adetailer", response["notice"])


class ModelCapabilityTests(_Surface):
    """M4 — capability comes from the backend, not a guessed constant."""

    def test_mock_reports_its_real_behaviour(self) -> None:
        capability = self.application.get_capability(self._model_id())
        self.assertEqual(1, capability.dimension_alignment)
        self.assertEqual(1, capability.minimum_dimension)
        self.assertIsNone(capability.maximum_dimension)
        self.assertIsNone(capability.maximum_pixels)
        self.assertFalse(capability.normalizes_dimensions)
        self.assertTrue(capability.is_mock)

    def test_capability_is_bound_to_model_and_operation(self) -> None:
        model_id = self._model_id()
        capability = self.application.get_capability(model_id, "txt2img")
        self.assertEqual(model_id, capability.model_id)
        self.assertEqual("txt2img", capability.operation)

    def test_unknown_model_is_a_structured_error(self) -> None:
        with self.assertRaises(self.studio.StudioError) as raised:
            self.application.get_capability("absent")
        self.assertEqual("MODEL_NOT_AVAILABLE", raised.exception.error.code)

    def test_unsupported_operation_is_a_structured_error(self) -> None:
        with self.assertRaises(self.studio.StudioError) as raised:
            self.application.get_capability(self._model_id(), "img2img")
        self.assertEqual(
            "OPERATION_NOT_SUPPORTED",
            raised.exception.error.code,
        )

    def test_capability_read_is_pure(self) -> None:
        self.application.get_capability(self._model_id())
        self.assertFalse(self.application.get_current_model().loaded)

    def test_declared_capability_matches_observed_behaviour(self) -> None:
        """Alignment 1 and no maximum must mean exactly that."""

        capability = self.application.get_capability(self._model_id())
        self.assertEqual(1, capability.dimension_alignment)
        events = self._run(width=521, height=777)
        self.assertEqual("completed", events[-1].state.value)
        result = self.application.get_result(events[-1].job_id)
        self.assertEqual(521, result.metadata["width"])
        self.assertEqual(777, result.metadata["height"])


class BackendShutdownTests(_Surface):
    """M6 — shutdown is defined, idempotent, and safe in flight."""

    def test_shutdown_marks_the_backend_not_ready(self) -> None:
        self.application.shutdown()
        status = self.application.get_backend_status()
        self.assertFalse(status.ready)
        self.assertEqual("stopped", status.state)

    def test_shutdown_is_idempotent(self) -> None:
        self.application.shutdown()
        self.application.shutdown()
        self.assertFalse(self.application.get_backend_status().ready)

    def test_shutdown_releases_residency(self) -> None:
        self.application.load_model(self._model_id())
        self.application.shutdown()
        self.assertFalse(self.application.get_current_model().loaded)

    def test_shutdown_with_a_job_in_flight_does_not_raise(self) -> None:
        request = self.studio.GenerationRequest(
            **_request_payload(self._model_id())
        )
        self.application.submit_generation(request)
        self.application.shutdown()
        self.assertFalse(self.application.get_backend_status().ready)

    def test_load_after_shutdown_is_refused(self) -> None:
        model_id = self._model_id()
        self.application.shutdown()
        with self.assertRaises(self.studio.StudioError) as raised:
            self.application.load_model(model_id)
        self.assertEqual("BACKEND_STOPPED", raised.exception.error.code)

    def test_presentation_shutdown_reaches_the_backend(self) -> None:
        self.presentation.shutdown()
        self.assertFalse(self.application.get_backend_status().ready)


class BackendAdapterSurfaceTests(unittest.TestCase):
    """The owned contract a real adapter must satisfy."""

    def setUp(self) -> None:
        self.studio = importlib.import_module("forge_studio")

    def test_required_methods_are_abstract(self) -> None:
        expected = {
            "get_backend_status",
            "list_models",
            "submit_generation",
            "poll_or_stream_progress",
            "cancel_generation",
            "get_result",
            "load_model",
            "unload_model",
            "get_current_model",
            "get_capability",
            "shutdown",
        }
        self.assertEqual(
            expected,
            set(self.studio.BackendAdapter.__abstractmethods__),
        )

    def test_incomplete_adapter_cannot_be_instantiated(self) -> None:
        class _Partial(self.studio.BackendAdapter):
            def get_backend_status(self):
                raise NotImplementedError

        with self.assertRaises(TypeError):
            _Partial()

    def test_mock_backend_satisfies_the_contract(self) -> None:
        backend = self.studio.MockBackend()
        for name in self.studio.BackendAdapter.__abstractmethods__:
            with self.subTest(method=name):
                self.assertFalse(
                    getattr(
                        getattr(type(backend), name),
                        "__isabstractmethod__",
                        False,
                    ),
                    f"{name} is still abstract on MockBackend",
                )

    def test_methods_added_this_milestone_are_documented(self) -> None:
        """Scoped to the methods this milestone added.

        The five pre-existing abstract methods predate it and documenting
        them is not in scope.
        """

        added = {
            "load_model",
            "unload_model",
            "get_current_model",
            "get_capability",
            "shutdown",
        }
        for name in added:
            with self.subTest(method=name):
                member = getattr(self.studio.BackendAdapter, name)
                self.assertTrue(
                    inspect.getdoc(member),
                    f"{name} has no contract docstring",
                )


if __name__ == "__main__":
    unittest.main()
