"""The fifth live-only defect: a channel that was open and never spoke.

Canonical was GREEN, 2476 tests, with `/studio/ws` emitting nothing at all
for every job the product can create.

```text
presentation._serve_websocket sends a progress frame only when
    self._source_adapter.active_job_id is not None

source_api_adapter.active_job_id read only `self._active`

`self._active` is set by SourceFrontendAdapter.generate and by nothing else

the product submits to /api/generate -> submit_async -> coordinator, which
never touches that object
```

So the gate never opened. The Live Preview frame travels ONLY on that socket
-- a 25 kB data URL does not belong in the job record that is serialised into
every poll -- which means Preview was carried correctly end to end, decoded a
frame, and delivered it through a channel that was shut. Every other ws-fed
field (progress, step, total_steps, textinfo, state) was dead for the same
reason.

Proven live 2026-08-10: during studio-job-000024, `/api/queue` reported it
`running` for its whole life while `/studio/task_id` -- which answers with
exactly `active_job_id` -- returned `""` throughout, and a WebSocket client
received zero messages.

WHY NO EXISTING TEST CAUGHT IT
==============================

The preview unit tests drive `HeadlessProgress` directly and assert a frame
is produced. Producing is not delivering. The one seam between them was a
boolean nobody asserted, and the two halves were owned by different objects,
so both halves were individually correct.

Both sides of every boundary are asserted here. It is not enough that the
gate opens for a coordinator job: it must still be shut when nothing is
running, or "always open" would pass every positive test in this file.

SCOPE: MINIMAL_RUNTIME_SCOPE. No model, no image, no GPU, no socket; the
coordinator and the source adapter are driven directly with fakes.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.jobs import JobCoordinator  # noqa: E402
from forge_studio.source_api_adapter import SourceFrontendAdapter  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_WS_GATE_TESTS = 20

DEADLINE = 5.0

A_FRAME = "data:image/png;base64,aGVsbG8="


def _until(predicate, deadline: float = DEADLINE) -> bool:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# ------------------------------------------------------- coordinator fakes


class _Session:
    def __init__(self) -> None:
        self.in_flight_job_id: str | None = None


class _Backend:
    def __init__(self) -> None:
        self._generation = _Session()


class _Lifecycle:
    gates_generation = True

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def mint_job_token(self) -> str:
        with self._lock:
            self._n += 1
            return f"studio-job-{self._n:06d}"

    def state(self) -> dict[str, Any]:
        return {"accepting_jobs": True, "active_job": None}

    def cancel_queued_job(self, token: str) -> bool:
        return False


class _Progress:
    def __init__(self) -> None:
        self.state = "running"
        self.message = "Sampling"
        self.step = 3
        self.total_steps = 8
        self.error = None


class _Application:
    """Blocks inside submit so a job can be observed while it is running."""

    def __init__(self) -> None:
        self.model_lifecycle = _Lifecycle()
        self._backend = _Backend()
        self.release = threading.Event()
        self.started: list[str] = []
        self.progress = _Progress()

    def submit_generation(self, request, *, job_token=None):
        self.started.append(request.label)
        self._backend._generation.in_flight_job_id = f"backend-{request.label}"
        self.release.wait(DEADLINE)
        self._backend._generation.in_flight_job_id = None
        return type("Identity", (), {"job_id": f"backend-{request.label}"})()

    def poll_or_stream_progress(self, backend_id):
        return self.progress

    def cancel_generation(self, backend_id):
        self.release.set()
        return type("Result", (), {"state": "cancelled"})()


class _Request:
    def __init__(self, label: str) -> None:
        self.label = label
        self.model_selection = {
            "checkpoint_model_id": "a" * 32,
            "text_encoder_model_id": "b" * 32,
            "vae_model_id": "c" * 32,
        }


# ------------------------------------------------- the coordinator's answer


class RunningJobIdTests(unittest.TestCase):
    """`running_job_id` is the fact the socket needs, and it is O(1)."""

    def setUp(self) -> None:
        self.application = _Application()
        self.coordinator = JobCoordinator(self.application)
        self.addCleanup(self._drain)

    def _drain(self) -> None:
        self.coordinator.close()
        self.application.release.set()

    def test_it_names_the_admitted_job_while_that_job_runs(self) -> None:
        job_id = self.coordinator.submit(_Request("a"))["job_id"]
        self.assertTrue(_until(lambda: "a" in self.application.started))
        self.assertEqual(job_id, self.coordinator.running_job_id())

    def test_it_is_none_once_nothing_is_running(self) -> None:
        """The discriminating half. A gate that is always open would satisfy
        every positive test in this file and re-break nothing visibly."""

        self.coordinator.submit(_Request("a"))
        self.assertTrue(_until(lambda: "a" in self.application.started))
        self.application.release.set()
        self.assertTrue(_until(lambda: self.coordinator.running_job_id() is None))

    def test_it_is_none_before_anything_is_submitted(self) -> None:
        self.assertIsNone(self.coordinator.running_job_id())

    def test_a_running_job_reports_the_backend_id_it_is_polled_with(self) -> None:
        """`describe` resolved the in-flight backend id, used it to poll, and
        then dropped it -- so the record said `backend_job_id: null` for the
        whole life of a running job and named it only at terminal.

        Live Preview is what needed it. Frames are held by the backend adapter
        under the id IT minted, so a reader holding only the public id gets no
        frame and no error: the decode was working and producing frames the
        socket could not ask for."""

        job_id = self.coordinator.submit(_Request("a"))["job_id"]
        self.assertTrue(_until(lambda: "a" in self.application.started))
        self.assertEqual(
            "backend-a", self.coordinator.describe(job_id)["backend_job_id"]
        )

    def test_a_queued_job_does_not_borrow_the_running_jobs_backend_id(self) -> None:
        """The discriminating half, and the reason the id is written back only
        for the ADMITTED job. Reporting it unconditionally would hand every
        waiting job the active one's identity -- which with a queue means
        `/api/jobs/<id>` showing you a different job's frames and steps."""

        self.coordinator.submit(_Request("a"))
        self.assertTrue(_until(lambda: "a" in self.application.started))
        queued = self.coordinator.submit(_Request("b"))["job_id"]
        self.assertIsNone(self.coordinator.describe(queued)["backend_job_id"])


# --------------------------------------------------- the presentation doubles


class _Presentation:
    """Only the four methods this seam actually reaches for."""

    def __init__(self) -> None:
        self.running: str | None = None
        self.records: dict[str, dict[str, Any]] = {}
        #: Keyed by BACKEND id, which is how the real backend adapter holds them.
        self.frames: dict[str, tuple[int, str]] = {}
        self.frame_lookups: list[str] = []
        self.raise_on_running = False

    def running_job_id(self) -> str | None:
        if self.raise_on_running:
            raise RuntimeError("the coordinator is gone")
        return self.running

    def job_status(self, job_id: str) -> dict[str, Any]:
        return dict(self.records[job_id])

    def poll(self, job_id: str, *, include_result: bool = True) -> dict[str, Any]:
        return dict(self.records[job_id])

    def preview_frame(self, job_id: str) -> tuple[int, str | None]:
        self.frame_lookups.append(job_id)
        return self.frames.get(job_id, (0, None))


def _record(
    public_id: str = "studio-job-000001",
    *,
    backend_id: str = "backend-a",
    state: str = "running",
    message: str = "Sampling",
    step: int = 3,
    total: int = 8,
    progress: int = 37,
) -> dict[str, Any]:
    return {
        "job_id": public_id,
        "backend_job_id": backend_id,
        "state": state,
        "message": message,
        "step": step,
        "total_steps": total,
        "progress": progress,
    }


class _Adapted(unittest.TestCase):
    def setUp(self) -> None:
        self.presentation = _Presentation()
        self.adapter = SourceFrontendAdapter(self.presentation)

    def _running(self, **kwargs: Any) -> str:
        record = _record(**kwargs)
        self.presentation.records[record["job_id"]] = record
        self.presentation.running = record["job_id"]
        return record["job_id"]


# ----------------------------------------------------------------- the gate


class ActiveJobIdTests(_Adapted):
    """What `_serve_websocket` reads before it sends anything at all."""

    def test_it_follows_the_coordinator_when_this_adapter_started_nothing(
        self,
    ) -> None:
        """The defect, exactly: `_active` is None for every job the product
        creates, and this used to answer None with one plainly running."""

        job_id = self._running()
        self.assertEqual(job_id, self.adapter.active_job_id)

    def test_it_is_none_when_the_coordinator_has_nothing_running(self) -> None:
        self.presentation.running = None
        self.assertIsNone(self.adapter.active_job_id)

    def test_the_legacy_path_still_wins_when_it_owns_a_job(self) -> None:
        """`generate` sets `_active`, and that job is the one it is waiting on.
        The fallback must not steal the answer from underneath it."""

        from forge_studio.source_api_adapter import _ActiveGeneration

        self._running()
        self.adapter._active = _ActiveGeneration(
            job_id="legacy-job",
            steps=4,
            model_title="",
            request={},
            ignored_parameters=(),
        )
        self.assertEqual("legacy-job", self.adapter.active_job_id)

    def test_a_presentation_without_the_accessor_answers_none(self) -> None:
        """Duck-typed seam. Several doubles predate this method, and the
        established rule here is that a missing capability is answered, not
        raised -- `preview_frame` reads the same way."""

        class _Older:
            pass

        self.assertIsNone(SourceFrontendAdapter(_Older()).active_job_id)

    def test_a_raising_accessor_answers_none_rather_than_killing_progress(
        self,
    ) -> None:
        self._running()
        self.presentation.raise_on_running = True
        self.assertIsNone(self.adapter.active_job_id)


# -------------------------------------------------------- what it then sends


class WebsocketStatusTests(_Adapted):
    """The message itself, behind the gate."""

    def test_a_coordinator_job_produces_real_progress(self) -> None:
        """Opening the gate and then reporting the idle placeholder would be
        the same defect one layer down."""

        self._running()
        message = self.adapter.websocket_status()
        self.assertEqual("running", message["state"])
        self.assertEqual(3, message["step"])
        self.assertEqual(8, message["total_steps"])
        self.assertEqual("Sampling", message["textinfo"])

    def test_the_message_names_the_job_by_its_public_id(self) -> None:
        """`task_id` is what a client would cancel with, and cancellation is
        addressed by the public lifecycle token."""

        job_id = self._running()
        self.assertEqual(job_id, self.adapter.websocket_status()["task_id"])

    def test_the_frame_is_looked_up_by_the_backend_id(self) -> None:
        """The second half of the same trap. `application.preview_frame`
        forwards to the backend adapter, which holds frames under the id it
        minted; asking it for `studio-job-NNNNNN` returns no frame and no
        error, which is the quietest way for a preview to be absent."""

        self._running(backend_id="backend-a")
        self.presentation.frames["backend-a"] = (7, A_FRAME)
        message = self.adapter.websocket_status()
        self.assertEqual(A_FRAME, message["preview"])
        self.assertEqual(7, message["preview_id"])
        self.assertIn("backend-a", self.presentation.frame_lookups)
        self.assertNotIn("studio-job-000001", self.presentation.frame_lookups)

    def test_preview_on_delivers_the_frame(self) -> None:
        self._running()
        self.presentation.frames["backend-a"] = (2, A_FRAME)
        self.assertEqual(A_FRAME, self.adapter.websocket_status()["preview"])

    def test_preview_off_delivers_nothing(self) -> None:
        """The other side. A backend that decoded no frame answers none, and
        the message must carry that rather than a stale one."""

        self._running()
        message = self.adapter.websocket_status()
        self.assertIsNone(message["preview"])
        self.assertEqual(0, message["preview_id"])

    def test_nothing_running_carries_no_frame_and_asks_for_none(self) -> None:
        self.presentation.running = None
        message = self.adapter.websocket_status()
        self.assertIsNone(message["preview"])
        self.assertEqual(0, message["job_count"])
        self.assertEqual([], self.presentation.frame_lookups)


# ------------------------------------------------------ the presentation hop


class PresentationAccessorTests(unittest.TestCase):
    def test_it_answers_none_without_a_coordinator(self) -> None:
        """A host may serve Studio with no lifecycle at all. That is an
        ordinary state, not a failure, and the socket simply stays quiet."""

        from forge_studio.presentation import StudioPresentation

        presentation = StudioPresentation.__new__(StudioPresentation)
        presentation._coordinator_or_none = lambda: None  # type: ignore[method-assign]
        self.assertIsNone(presentation.running_job_id())

    def test_it_forwards_the_coordinators_answer(self) -> None:
        from forge_studio.presentation import StudioPresentation

        presentation = StudioPresentation.__new__(StudioPresentation)
        presentation._coordinator_or_none = lambda: type(  # type: ignore[method-assign]
            "C", (), {"running_job_id": staticmethod(lambda: "studio-job-000009")}
        )
        self.assertEqual("studio-job-000009", presentation.running_job_id())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_WS_GATE_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
