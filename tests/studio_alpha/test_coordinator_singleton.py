"""One presentation, one queue -- even when three requests arrive together.

Found live 2026-08-10 from a COLD start, and it is the worst defect this
project has had: a submitted job could become permanently unobservable.

```text
def _coordinator_or_none(self):
    if not self.lifecycle_available:
        return None
    if getattr(self, "_coordinator", None) is None:      # <-- read
        from .jobs import JobCoordinator
        self._coordinator = JobCoordinator(self._application)   # <-- write
    return self._coordinator
```

Unsynchronized lazy initialisation, served by a `ThreadingHTTPServer`. Three
concurrent `/api/generate` calls all completed the read before any of them
performed the write, so each built its OWN `JobCoordinator` -- its own
`_pending` list, its own `_admitted` slot. The last assignment won.

Observed: three simultaneous submissions all admitted in the same
millisecond, `depth=1` on every enqueue because each queue held only its own
job, two dead on MODEL_ALREADY_LOADING from the genuinely-singleton
lifecycle, and afterwards `/api/jobs` listed ONE of the three. The other two
were absent from every surface and uncancellable, while their worker threads
ran on and one published a result nothing could reach.

WHY 48 QUEUE TESTS AND A 30/30 LIVE RUN ALL MISSED IT
=====================================================

The queue's unit tests construct the coordinator themselves, so they cannot
enter the window. The live queue legs submit sequentially over HTTP, and a
sequential caller never races construction either. The admission logic was
correct the whole time and remains unchanged by this fix -- it was being
asked to serialize three jobs that were in three different queues.

The test below must therefore FORCE the window open. A test that calls
`_coordinator_or_none` twice in a row passes against the defect, which is
precisely how this survived.

SCOPE: MINIMAL_RUNTIME_SCOPE. No model, no image, no GPU, no HTTP; the
presentation is driven directly from threads released by a barrier.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio import jobs as jobs_module  # noqa: E402
from forge_studio.presentation import StudioPresentation  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_SINGLETON_TESTS = 9

CALLERS = 4

#: Long enough that every barrier-released thread is inside the window before
#: the first one leaves it. Against the fixed code this is paid once.
CONSTRUCTION_DELAY = 0.05


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


class _Session:
    def __init__(self) -> None:
        self.in_flight_job_id: str | None = None


class _Backend:
    def __init__(self) -> None:
        self._generation = _Session()


class _Progress:
    state = "running"
    message = "Sampling"
    step = 1
    total_steps = 4
    error = None


class _Application:
    def __init__(self) -> None:
        self.model_lifecycle = _Lifecycle()
        self._backend = _Backend()
        self.release = threading.Event()
        self.progress = _Progress()

    def submit_generation(self, request, *, job_token=None):
        self.release.wait(5.0)
        return type("Identity", (), {"job_id": f"backend-{job_token}"})()

    def poll_or_stream_progress(self, backend_id):
        return self.progress

    def cancel_generation(self, backend_id):
        self.release.set()
        return type("Result", (), {"state": "cancelled"})()


class _NoLifecycle:
    model_lifecycle = None


class _Request:
    model_selection = {
        "checkpoint_model_id": "a" * 32,
        "text_encoder_model_id": "b" * 32,
        "vae_model_id": "c" * 32,
    }


class _SlowRealCoordinator(jobs_module.JobCoordinator):
    """The real coordinator, constructed slowly.

    Defined at module scope so the base class is bound to the REAL
    `JobCoordinator` before any test patches that name.

    The consequence tests below need this. Constructing the genuine
    coordinator takes microseconds, so barrier-released threads serialize by
    luck and the tests pass against the defect -- measured: with the plain
    class, only the two identity tests failed against the reverted
    implementation. A test that cannot fail is not coverage, so the window is
    held open here too.
    """

    def __init__(self, application: Any) -> None:
        time.sleep(CONSTRUCTION_DELAY)
        super().__init__(application)


def _presentation(application: Any) -> StudioPresentation:
    return StudioPresentation(application, lambda **kwargs: _Request())


def _concurrently(work, count: int = CALLERS) -> list[Any]:
    """Release `count` threads at the same instant and collect their results."""

    results: list[Any] = []
    guard = threading.Lock()
    barrier = threading.Barrier(count)

    def run() -> None:
        barrier.wait()
        value = work()
        with guard:
            results.append(value)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10.0)
    return results


class ConstructionRaceTests(unittest.TestCase):
    """The window, forced open."""

    def setUp(self) -> None:
        self.application = _Application()
        self.presentation = _presentation(self.application)
        self.built: list[Any] = []

    def _slow_coordinator(self):
        built = self.built

        class _Slow:
            def __init__(self, application: Any) -> None:
                # The window. Without a delay here the threads can serialize
                # by luck and the test passes against the defect -- which is
                # exactly how the real one survived a green suite.
                time.sleep(CONSTRUCTION_DELAY)
                self.application = application
                built.append(self)

        return _Slow

    def test_concurrent_callers_all_receive_the_same_coordinator(self) -> None:
        with mock.patch.object(jobs_module, "JobCoordinator", self._slow_coordinator()):
            got = _concurrently(self.presentation._coordinator_or_none)
        self.assertEqual(CALLERS, len(got))
        self.assertEqual(1, len({id(item) for item in got}),
                         "callers received more than one coordinator")

    def test_exactly_one_coordinator_is_ever_constructed(self) -> None:
        """The direct statement of the defect. It built one per caller."""

        with mock.patch.object(jobs_module, "JobCoordinator", self._slow_coordinator()):
            _concurrently(self.presentation._coordinator_or_none)
        self.assertEqual(1, len(self.built))

    def test_no_caller_is_answered_with_none(self) -> None:
        """The lock must not turn a slow construction into a missing queue."""

        with mock.patch.object(jobs_module, "JobCoordinator", self._slow_coordinator()):
            got = _concurrently(self.presentation._coordinator_or_none)
        self.assertTrue(all(item is not None for item in got))

    def test_a_later_call_reuses_the_one_already_built(self) -> None:
        with mock.patch.object(jobs_module, "JobCoordinator", self._slow_coordinator()):
            first = self.presentation._coordinator_or_none()
            second = self.presentation._coordinator_or_none()
        self.assertIs(first, second)
        self.assertEqual(1, len(self.built))


class OwnerVisibleConsequenceTests(unittest.TestCase):
    """What the lost coordinators cost: jobs that no surface can see."""

    def test_every_concurrently_submitted_job_stays_visible(self) -> None:
        """Live, two of three submissions vanished from `/api/jobs` -- not
        delayed, absent, with their workers still running.

        Driven through the REAL `JobCoordinator`, because the claim is about
        what the owner can observe afterwards and not about identity."""

        application = _Application()
        presentation = _presentation(application)
        self.addCleanup(application.release.set)

        def submit() -> str:
            coordinator = presentation._coordinator_or_none()
            return coordinator.submit(_Request())["job_id"]

        with mock.patch.object(jobs_module, "JobCoordinator", _SlowRealCoordinator):
            submitted = _concurrently(submit)
        coordinator = presentation._coordinator_or_none()
        self.addCleanup(coordinator.close)

        listed = {record["job_id"] for record in coordinator.list_jobs()}
        self.assertEqual(CALLERS, len(submitted))
        self.assertEqual(set(submitted), listed,
                         "a submitted job is missing from the job list")

    def test_they_all_land_in_one_queue(self) -> None:
        """Three queues each admitting their own job is what produced three
        simultaneous `starting depth=0` lines and two MODEL_ALREADY_LOADING
        failures. One queue means one job admitted and the rest waiting."""

        application = _Application()
        presentation = _presentation(application)
        self.addCleanup(application.release.set)

        def submit() -> str:
            return presentation._coordinator_or_none().submit(_Request())["job_id"]

        with mock.patch.object(jobs_module, "JobCoordinator", _SlowRealCoordinator):
            _concurrently(submit)
        coordinator = presentation._coordinator_or_none()
        self.addCleanup(coordinator.close)

        deadline = time.monotonic() + 5.0
        view = coordinator.queue_view()
        while time.monotonic() < deadline and view["running"] is None:
            time.sleep(0.01)
            view = coordinator.queue_view()
        running = 1 if view["running"] is not None else 0
        self.assertEqual(CALLERS, running + view["queue_depth"],
                         "jobs are not all accounted for in one queue")


class LifecycleGateTests(unittest.TestCase):
    def test_no_coordinator_is_built_without_a_lifecycle(self) -> None:
        """The discriminating half. A fix that just always built one would
        pass every test above and break every host that has no lifecycle."""

        presentation = _presentation(_NoLifecycle())
        built: list[Any] = []

        class _Counted:
            def __init__(self, application: Any) -> None:
                built.append(self)

        with mock.patch.object(jobs_module, "JobCoordinator", _Counted):
            self.assertIsNone(presentation._coordinator_or_none())
            self.assertIsNone(presentation._coordinator_or_none())
        self.assertEqual([], built)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_SINGLETON_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
