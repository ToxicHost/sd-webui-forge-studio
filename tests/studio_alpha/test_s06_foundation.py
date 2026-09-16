"""Focused S0.6 tests for concurrency, lifecycle, and loopback trust."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import importlib
from threading import Event, Lock
from types import MethodType
import unittest
from typing import Any


class _ManualClock:
    def __init__(self) -> None:
        self._value = 100.0
        self._lock = Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


class _SlowApplication:
    def __init__(self) -> None:
        self.submit_started = Event()
        self.release_submit = Event()

    def submit_generation(self, _request: object) -> dict[str, str]:
        self.submit_started.set()
        if not self.release_submit.wait(2):
            raise RuntimeError("test did not release the slow submission")
        return {"job_id": "slow-job", "state": "queued"}

    def get_backend_status(self) -> dict[str, object]:
        return {"state": "ready", "ready": True}

    def cancel_generation(self, job_id: str) -> dict[str, object]:
        return {
            "job_id": job_id,
            "cancelled": True,
            "state": "cancelled",
        }


class _PresentationSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def backend_status(self) -> dict[str, object]:
        self.calls.append(("status", None))
        return {"state": "ready"}

    def submit(self, payload: object) -> dict[str, str]:
        self.calls.append(("submit", payload))
        return {"job_id": "host-test-job", "state": "queued"}


def _request_payload(model_id: str) -> dict[str, object]:
    return {
        "model_id": model_id,
        "positive_prompt": "S0.6 deterministic foundation",
        "negative_prompt": "",
        "seed": 606,
        "steps": 4,
        "cfg_scale": 7.0,
        "width": 512,
        "height": 512,
    }


class MockLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        studio = importlib.import_module("forge_studio")
        self.studio = studio
        self.clock = _ManualClock()
        self.backend = studio.MockBackend(
            clock=self.clock,
            event_interval_seconds=1.0,
        )
        self.application = studio.StudioApplication(self.backend)

    def _submit(self, prompt: str = "S0.6 deterministic foundation") -> object:
        model_id = self.application.list_models()[0].model_id
        request = self.studio.GenerationRequest(
            **{
                **_request_payload(model_id),
                "positive_prompt": prompt,
            }
        )
        return self.application.submit_generation(request)

    def test_repeated_poll_is_an_idempotent_observation(self) -> None:
        job = self._submit()
        first = self.application.poll_or_stream_progress(job.job_id)
        repeated = self.application.poll_or_stream_progress(job.job_id)
        self.assertEqual(first, repeated)
        self.assertEqual(self.studio.JobState.QUEUED, first.state)

        self.clock.advance(2)
        advanced = self.application.poll_or_stream_progress(job.job_id)
        advanced_again = self.application.poll_or_stream_progress(job.job_id)
        self.assertEqual(advanced, advanced_again)
        self.assertEqual(2, advanced.sequence)
        self.assertEqual(self.studio.JobState.RUNNING, advanced.state)

    def test_two_pollers_observe_the_same_state_without_stealing(self) -> None:
        job = self._submit()
        self.clock.advance(3)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self.application.poll_or_stream_progress,
                    job.job_id,
                )
                for _ in range(2)
            ]
            observations = [future.result(timeout=1) for future in futures]
        self.assertEqual(observations[0], observations[1])
        self.assertEqual(3, observations[0].sequence)

        self.clock.advance(10)
        with ThreadPoolExecutor(max_workers=2) as executor:
            terminal = [
                future.result(timeout=1)
                for future in (
                    executor.submit(
                        self.application.poll_or_stream_progress,
                        job.job_id,
                    ),
                    executor.submit(
                        self.application.poll_or_stream_progress,
                        job.job_id,
                    ),
                )
            ]
        self.assertEqual(terminal[0], terminal[1])
        self.assertEqual(self.studio.JobState.COMPLETED, terminal[0].state)

    def test_result_becomes_available_without_polling(self) -> None:
        job = self._submit()
        self.clock.advance(10)
        result = self.application.get_result(job.job_id)
        self.assertEqual(self.studio.JobState.COMPLETED, result.state)
        observed = self.application.poll_or_stream_progress(job.job_id)
        self.assertEqual(self.studio.JobState.COMPLETED, observed.state)
        self.assertEqual(result, self.application.get_result(job.job_id))

    def test_cancellation_is_terminal_and_a_later_job_recovers(self) -> None:
        cancelled_job = self._submit("cancel the scheduled mock")
        self.clock.advance(2)
        cancellation = self.application.cancel_generation(
            cancelled_job.job_id
        )
        self.assertTrue(cancellation.cancelled)
        self.clock.advance(20)
        first = self.application.poll_or_stream_progress(
            cancelled_job.job_id
        )
        repeated = self.application.poll_or_stream_progress(
            cancelled_job.job_id
        )
        self.assertEqual(first, repeated)
        self.assertEqual(self.studio.JobState.CANCELLED, first.state)
        with self.assertRaises(self.studio.StudioError):
            self.application.get_result(cancelled_job.job_id)

        recovery = self._submit("recover after cancellation")
        self.clock.advance(10)
        result = self.application.get_result(recovery.job_id)
        self.assertEqual(self.studio.JobState.COMPLETED, result.state)


class PresentationConcurrencyTests(unittest.TestCase):
    def test_slow_submit_does_not_block_status_or_cancel(self) -> None:
        presentation_type = importlib.import_module(
            "forge_studio.presentation"
        ).StudioPresentation
        application = _SlowApplication()
        presentation = presentation_type(application, lambda **payload: payload)
        payload = {
            "model": "studio-mock",
            "positive_prompt": "slow submit",
            "negative_prompt": "",
            "seed": 606,
            "steps": 4,
            "cfg_scale": 7.0,
            "width": 512,
            "height": 512,
        }

        with ThreadPoolExecutor(max_workers=3) as executor:
            submit = executor.submit(presentation.submit, payload)
            self.assertTrue(application.submit_started.wait(1))
            try:
                status = executor.submit(presentation.backend_status)
                cancel = executor.submit(presentation.cancel, "existing-job")
                self.assertEqual(
                    "ready",
                    status.result(timeout=1)["state"],
                )
                self.assertEqual(
                    "cancelled",
                    cancel.result(timeout=1)["state"],
                )
            finally:
                application.release_submit.set()
            self.assertEqual("slow-job", submit.result(timeout=1)["job_id"])


class LoopbackHostValidationTests(unittest.TestCase):
    @staticmethod
    def _handler(
        method: str,
        path: str,
        host: str | None,
        *,
        origin: str | None = None,
    ) -> tuple[Any, _PresentationSpy, list[tuple[object, object]], list[object]]:
        presentation_module = importlib.import_module(
            "forge_studio.presentation"
        )
        server = object.__new__(presentation_module._StudioHTTPServer)
        server.server_address = ("127.0.0.1", 7865)
        server.presentation = _PresentationSpy()

        handler = object.__new__(presentation_module._StudioRequestHandler)
        handler.server = server
        handler.command = method
        handler.path = path
        headers = Message()
        if host is not None:
            headers["Host"] = host
        if origin is not None:
            headers["Origin"] = origin
        handler.headers = headers

        responses: list[tuple[object, object]] = []
        body_reads: list[object] = []

        def send_json(
            _handler: object,
            status: object,
            payload: object,
        ) -> None:
            responses.append((status, payload))

        def read_json(_handler: object) -> dict[str, str]:
            body_reads.append(True)
            return {"request": "accepted"}

        def read_json_bounded(_handler: object, *, maximum_bytes: int = 0) -> dict:
            # `/api/generate` reads through the BOUNDED reader since WP1 gave
            # it an image payload. Stubbed alongside the plain one so this
            # suite keeps testing host validation rather than which reader the
            # route happens to call.
            body_reads.append(True)
            return {"request": "accepted"}

        handler._send_json = MethodType(send_json, handler)
        handler._read_json = MethodType(read_json, handler)
        handler._read_json_bounded = MethodType(read_json_bounded, handler)
        return handler, server.presentation, responses, body_reads

    def test_loopback_hosts_are_accepted_for_get_and_post(self) -> None:
        for host in ("127.0.0.1:7865", "localhost:7865"):
            with self.subTest(host=host, method="GET"):
                handler, presentation, responses, _reads = self._handler(
                    "GET",
                    "/api/status",
                    host,
                )
                handler.do_GET()
                self.assertEqual([("status", None)], presentation.calls)
                self.assertEqual(200, int(responses[0][0]))

            with self.subTest(host=host, method="POST"):
                handler, presentation, responses, reads = self._handler(
                    "POST",
                    "/api/generate",
                    host,
                    origin=f"http://{host}",
                )
                handler.do_POST()
                self.assertEqual([True], reads)
                self.assertEqual("submit", presentation.calls[0][0])
                self.assertEqual(202, int(responses[0][0]))

    def test_foreign_or_missing_hosts_are_rejected_before_api_access(self) -> None:
        for host in ("attacker.example:7865", "127.0.0.1:9999", None):
            for method, path in (
                ("GET", "/api/status"),
                ("POST", "/api/generate"),
            ):
                with self.subTest(host=host, method=method):
                    handler, presentation, responses, reads = self._handler(
                        method,
                        path,
                        host,
                        origin="http://127.0.0.1:7865",
                    )
                    getattr(handler, f"do_{method}")()
                    self.assertEqual([], presentation.calls)
                    self.assertEqual([], reads)
                    self.assertEqual(403, int(responses[0][0]))

    def test_head_validates_host_before_serving_static_content(self) -> None:
        for host, expected_serves in (
            ("localhost:7865", 1),
            ("foreign.example:7865", 0),
        ):
            with self.subTest(host=host):
                handler, _presentation, responses, _reads = self._handler(
                    "HEAD",
                    "/",
                    host,
                )
                serves: list[str] = []

                def serve_static(_handler: object, path: str) -> bool:
                    serves.append(path)
                    return True

                handler._serve_static = MethodType(serve_static, handler)
                handler.do_HEAD()
                self.assertEqual(expected_serves, len(serves))
                if expected_serves:
                    self.assertEqual([], responses)
                else:
                    self.assertEqual(403, int(responses[0][0]))

    def test_origin_check_remains_in_force_after_host_validation(self) -> None:
        handler, presentation, responses, reads = self._handler(
            "POST",
            "/api/generate",
            "127.0.0.1:7865",
            origin="http://attacker.example:7865",
        )
        handler.do_POST()
        self.assertEqual([], presentation.calls)
        self.assertEqual([], reads)
        self.assertEqual(403, int(responses[0][0]))
        self.assertIn("Cross-origin", responses[0][1]["error"])


if __name__ == "__main__":
    unittest.main()
