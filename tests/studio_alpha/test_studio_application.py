"""Focused behavior tests for the Studio-owned application boundary."""

from __future__ import annotations

import base64
import importlib
import unittest
from typing import Any


def _public_api() -> tuple[type[Any], type[Any], type[Any], type[Any]]:
    studio = importlib.import_module("forge_studio")
    return (
        studio.StudioApplication,
        studio.MockBackend,
        studio.GenerationRequest,
        studio.JobState,
    )


class StudioApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        application_type, backend_type, request_type, job_state = _public_api()
        self.request_type = request_type
        self.job_state = job_state
        self.app = application_type(backend_type())

    def request(
        self,
        *,
        positive_prompt: str = "studio alpha deterministic request",
        negative_prompt: str = "",
        model_id: str | None = None,
        seed: int = 424242,
        steps: int = 4,
        cfg_scale: float = 7.0,
        width: int = 512,
        height: int = 512,
    ) -> object:
        selected_model = model_id or self.app.list_models()[0].model_id
        return self.request_type(
            model_id=selected_model,
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            steps=steps,
            cfg_scale=cfg_scale,
            width=width,
            height=height,
        )

    def poll_to_terminal(self, job_id: str) -> list[object]:
        progress = []
        terminal = {
            self.job_state.COMPLETED,
            self.job_state.CANCELLED,
            self.job_state.FAILED,
        }
        for _ in range(16):
            event = self.app.poll_or_stream_progress(job_id)
            progress.append(event)
            if event.state in terminal:
                return progress
        self.fail("mock job did not reach a terminal state")

    def test_models_and_backend_status_are_deterministic(self) -> None:
        application_type, backend_type, _request_type, _job_state = _public_api()
        other = application_type(backend_type())
        self.assertEqual(self.app.get_backend_status(), other.get_backend_status())
        self.assertTrue(self.app.get_backend_status().ready)
        self.assertTrue(self.app.get_backend_status().is_mock)
        self.assertEqual("ready", self.app.get_backend_status().state)
        self.assertFalse(self.app.get_backend_status().model_loaded)
        self.assertFalse(self.app.get_backend_status().cuda_initialized)
        self.assertFalse(self.app.get_backend_status().network_access)
        self.assertEqual(self.app.list_models(), self.app.list_models())
        self.assertEqual(self.app.list_models(), other.list_models())
        self.assertGreater(len(self.app.list_models()), 0)

    def test_valid_request_is_submitted(self) -> None:
        submitted = self.app.submit_generation(self.request())
        self.assertIsInstance(submitted.job_id, str)
        self.assertTrue(submitted.job_id)
        self.assertEqual(self.job_state.QUEUED, submitted.state)

    def test_presentation_adapter_completes_without_a_socket(self) -> None:
        presentation_type = importlib.import_module(
            "forge_studio.presentation"
        ).StudioPresentation
        presentation = presentation_type(self.app, self.request_type)
        model_id = self.app.list_models()[0].model_id
        submitted = presentation.submit(
            {
                "model": model_id,
                "positive_prompt": "presentation contract",
                "negative_prompt": "",
                "seed": 73,
                "steps": 4,
                "cfg_scale": 7.0,
                "width": 512,
                "height": 512,
            }
        )
        for _ in range(16):
            progress = presentation.poll(submitted["job_id"])
            if progress["state"] == "completed":
                break
        else:
            self.fail("presentation job did not complete")
        self.assertTrue(
            progress["result"]["image_data_url"].startswith("data:image/")
        )

    def test_result_free_poll_does_not_materialize_completed_image(
        self,
    ) -> None:
        presentation_type = importlib.import_module(
            "forge_studio.presentation"
        ).StudioPresentation

        class CompletedApplication:
            def __init__(self) -> None:
                self.result_calls = 0

            def poll_or_stream_progress(self, _job_id: str) -> dict[str, Any]:
                return {
                    "state": "completed",
                    "progress": 100,
                    "message": "done",
                }

            def get_result(self, _job_id: str) -> dict[str, Any]:
                self.result_calls += 1
                return {
                    "image_data_url": "data:image/png;base64,large-payload"
                }

        application = CompletedApplication()
        presentation = presentation_type(application, self.request_type)
        observed = presentation.poll(
            "completed-job",
            include_result=False,
        )
        self.assertNotIn("result", observed)
        self.assertEqual(0, application.result_calls)

        materialized = presentation.poll("completed-job")
        self.assertIn("result", materialized)
        self.assertEqual(1, application.result_calls)

    def test_presentation_rejects_mistyped_generation_inputs(self) -> None:
        presentation_module = importlib.import_module(
            "forge_studio.presentation"
        )
        presentation = presentation_module.StudioPresentation(
            self.app,
            self.request_type,
        )
        valid = {
            "model": self.app.list_models()[0].model_id,
            "positive_prompt": "strict JSON types",
            "negative_prompt": "",
            "seed": 73,
            "steps": 4,
            "cfg_scale": 7.0,
            "width": 512,
            "height": 512,
        }
        for field, value in (
            ("positive_prompt", ["not", "text"]),
            ("negative_prompt", None),
            ("steps", 4.5),
            ("seed", "73"),
            ("width", 512.5),
            ("height", -8),
            ("cfg_scale", float("nan")),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(
                    presentation_module.PresentationError
                ):
                    presentation.submit({**valid, field: value})

    def test_progress_is_ordered_and_success_completes(self) -> None:
        submitted = self.app.submit_generation(self.request())
        progress = self.poll_to_terminal(submitted.job_id)
        result = self.app.get_result(submitted.job_id)
        self.assertGreaterEqual(len(progress), 2)
        sequences = [item.sequence for item in progress]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(sequences), len(set(sequences)))
        self.assertEqual(self.job_state.COMPLETED, result.state)
        self.assertTrue(result.image_data_url.startswith("data:image/svg+xml;base64,"))

    def test_cancellation_is_terminal_and_prevents_completion(self) -> None:
        submitted = self.app.submit_generation(self.request())
        self.app.poll_or_stream_progress(submitted.job_id)
        running = self.app.poll_or_stream_progress(submitted.job_id)
        self.assertEqual(self.job_state.RUNNING, running.state)
        cancellation = self.app.cancel_generation(submitted.job_id)
        self.assertTrue(cancellation.cancelled)
        self.assertEqual(self.job_state.CANCELLED, cancellation.state)
        progress = self.poll_to_terminal(submitted.job_id)
        self.assertEqual(self.job_state.CANCELLED, progress[-1].state)
        self.assertNotIn(self.job_state.COMPLETED, {item.state for item in progress})
        studio_error = importlib.import_module("forge_studio").StudioError
        with self.assertRaises(studio_error) as caught:
            self.app.get_result(submitted.job_id)
        self.assertEqual("RESULT_NOT_AVAILABLE", caught.exception.error.code)

    def test_controlled_failure_returns_structured_error(self) -> None:
        submitted = self.app.submit_generation(
            self.request(positive_prompt="__mock_fail__")
        )
        terminal = self.poll_to_terminal(submitted.job_id)[-1]
        self.assertEqual(self.job_state.FAILED, terminal.state)
        error = terminal.error
        self.assertIsNotNone(error)
        self.assertEqual("MOCK_CONTROLLED_FAILURE", error.code)
        self.assertTrue(error.message)
        self.assertEqual("positive_prompt", error.field)

    def test_second_job_succeeds_after_completion(self) -> None:
        first = self.app.submit_generation(self.request(positive_prompt="first"))
        self.poll_to_terminal(first.job_id)
        self.app.get_result(first.job_id)
        second = self.app.submit_generation(self.request(positive_prompt="second"))
        self.poll_to_terminal(second.job_id)
        result = self.app.get_result(second.job_id)
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertEqual(self.job_state.COMPLETED, result.state)

    def test_second_job_succeeds_after_failure(self) -> None:
        failed = self.app.submit_generation(
            self.request(positive_prompt="__mock_fail__")
        )
        self.poll_to_terminal(failed.job_id)
        recovery = self.app.submit_generation(
            self.request(positive_prompt="recovery")
        )
        self.poll_to_terminal(recovery.job_id)
        result = self.app.get_result(recovery.job_id)
        self.assertNotEqual(failed.job_id, recovery.job_id)
        self.assertEqual(self.job_state.COMPLETED, result.state)

    def test_second_job_succeeds_after_cancellation(self) -> None:
        cancelled = self.app.submit_generation(
            self.request(positive_prompt="cancel this")
        )
        self.app.poll_or_stream_progress(cancelled.job_id)
        self.app.cancel_generation(cancelled.job_id)
        recovery = self.app.submit_generation(
            self.request(positive_prompt="recover after cancel")
        )
        self.poll_to_terminal(recovery.job_id)
        result = self.app.get_result(recovery.job_id)
        self.assertNotEqual(cancelled.job_id, recovery.job_id)
        self.assertEqual(self.job_state.COMPLETED, result.state)

    def test_invalid_inputs_are_rejected(self) -> None:
        cases = (
            {"steps": 0},
            {"width": 0},
            {"width": -1},
            {"height": 0},
            {"height": -1},
            {"model_id": "not-a-studio-model"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.app.submit_generation(self.request(**overrides))

    def test_empty_and_negative_only_prompts_are_valid(self) -> None:
        for positive_prompt, negative_prompt in (
            ("", ""),
            ("", "low quality, watermark"),
            ("normal positive prompt", ""),
        ):
            with self.subTest(
                positive_prompt=positive_prompt,
                negative_prompt=negative_prompt,
            ):
                submitted = self.app.submit_generation(
                    self.request(
                        positive_prompt=positive_prompt,
                        negative_prompt=negative_prompt,
                    )
                )
                terminal = self.poll_to_terminal(submitted.job_id)[-1]
                self.assertEqual(self.job_state.COMPLETED, terminal.state)

    def test_mock_preserves_capability_driven_dimensions_exactly(self) -> None:
        for width, height in ((520, 776), (2056, 520)):
            with self.subTest(width=width, height=height):
                submitted = self.app.submit_generation(
                    self.request(width=width, height=height)
                )
                self.poll_to_terminal(submitted.job_id)
                result = self.app.get_result(submitted.job_id)
                self.assertEqual(width, result.metadata["width"])
                self.assertEqual(height, result.metadata["height"])
                encoded = result.image_data_url.split(",", 1)[1]
                svg = base64.b64decode(encoded).decode("utf-8")
                self.assertIn(f'width="{width}"', svg)
                self.assertIn(f'height="{height}"', svg)

    def test_prompt_type_and_length_validation_remain_strict(self) -> None:
        with self.assertRaises(ValueError):
            self.app.submit_generation(
                self.request_type(
                    model_id=self.app.list_models()[0].model_id,
                    positive_prompt=["not", "text"],
                    negative_prompt="",
                    seed=73,
                    steps=4,
                    cfg_scale=7.0,
                    width=512,
                    height=512,
                )
            )
        # A long prompt is ACCEPTED. The 4000-character cap that used to be
        # asserted here was removed 2026-08-20 (AR6.5): nothing in the core
        # bounds a prompt, and a prompt with several LoRA tags and an expanded
        # wildcard reaches 4000 without trying. The TYPE check above is what
        # this test is really for, and it stays.
        self.app.submit_generation(self.request(positive_prompt="x" * 20_000))

    def test_presentation_allows_empty_and_negative_only_prompts(self) -> None:
        presentation_module = importlib.import_module(
            "forge_studio.presentation"
        )
        presentation = presentation_module.StudioPresentation(
            self.app,
            self.request_type,
        )
        valid = {
            "model": self.app.list_models()[0].model_id,
            "positive_prompt": "",
            "negative_prompt": "low quality",
            "seed": 73,
            "steps": 4,
            "cfg_scale": 7.0,
            "width": 512,
            "height": 512,
        }
        submitted = presentation.submit(valid)
        self.assertTrue(submitted["job_id"])

    def test_result_metadata_is_deterministic(self) -> None:
        application_type, backend_type, _request_type, _job_state = _public_api()
        other = application_type(backend_type())
        first_job = self.app.submit_generation(self.request())
        self.poll_to_terminal(first_job.job_id)
        first = self.app.get_result(first_job.job_id)
        second_request = self.request_type(
            model_id=other.list_models()[0].model_id,
            positive_prompt="studio alpha deterministic request",
            negative_prompt="",
            seed=424242,
            steps=4,
            cfg_scale=7.0,
            width=512,
            height=512,
        )
        second_job = other.submit_generation(second_request)
        for _ in range(16):
            event = other.poll_or_stream_progress(second_job.job_id)
            if event.state is self.job_state.COMPLETED:
                break
        else:
            self.fail("second deterministic job did not complete")
        second = other.get_result(second_job.job_id)
        self.assertEqual(first.metadata, second.metadata)
        self.assertEqual(first.image_data_url, second.image_data_url)


if __name__ == "__main__":
    unittest.main()
