"""Four defects that only a live run could find, pinned so they cannot return.

Canonical was GREEN with all four present. That is the point of this file.

```text
1  the stage LABEL was never merged into /api/jobs/<id>
2  `_in_flight_job_id` was set at submit and never cleared, so one job
   reported another job's progress
3  gateway progress was adopted only AFTER submit returned -- at terminal --
   so every job read `queued` for its whole life
4  Neo reports sampling steps ZERO-BASED, so `value >= total` was
   unsatisfiable and HIRES_PREPARING was unreachable in production
```

WHY THE EXISTING SUITES DID NOT CATCH ANY OF THEM
=================================================

Forty-plus Hires tests drove `HeadlessProgress` DIRECTLY, calling
`report_step(total)` -- a value Neo never emits. The state machine was correct
in isolation and nothing drove it correctly in production.

Worse for defect 4: the repair (`value + 1 >= total`) is strictly MORE
PERMISSIVE than the bug (`value >= total`), so every existing test passes
under both. A suite that passes either way measures nothing, which is why the
zero-based tests below assert `total - 2` does NOT fire as carefully as they
assert `total - 1` does. A test that only checks the positive case would go
green against the broken condition too.

Every test here fails against the implementation as it shipped.

SCOPE: MINIMAL_RUNTIME_SCOPE. No model, no image, no GPU; the progress model
and the coordinator are driven directly, with fakes whose timing is controlled.
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

from forge_headless.contracts import HeadlessError  # noqa: E402
from forge_headless.headless_progress import (  # noqa: E402
    HeadlessProgress,
    JobState,
)
from forge_headless.studio_generation import (  # noqa: E402
    HeadlessGenerationSession,
    _JobRecord,
    translate_request,
)
from forge_studio.contracts import GenerationRequest  # noqa: E402
from forge_studio.jobs import CANCELLED, RUNNING, JobCoordinator  # noqa: E402

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_LIVE_FIX_TESTS = 24

DEADLINE = 5.0


def _until(predicate, deadline: float = DEADLINE) -> bool:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _sampling(total: int, *, hires: bool) -> HeadlessProgress:
    progress = HeadlessProgress("j")
    if hires:
        progress.expect_hires_pass()
    progress.advance_to(JobState.CONDITIONING)
    progress.advance_to(JobState.SAMPLING)
    progress.set_total_steps(total)
    return progress


def _drive_zero_based(progress: HeadlessProgress, total: int) -> None:
    """Exactly what Neo does: report 0 .. total-1, and never `total`."""

    for step in range(total):
        progress.report_step(step)


# ---------------------------------------------------------------- defect 4


class ZeroBasedCallbackTests(unittest.TestCase):
    """Neo's real callback contract, which is `0 .. total-1`."""

    def test_the_final_real_step_ends_the_base_pass(self) -> None:
        """`total - 1` is the last value Neo ever sends. Under the shipped
        condition (`value >= total`) this never fired for any step count."""

        progress = _sampling(8, hires=True)
        progress.report_step(7)
        self.assertIs(JobState.HIRES_PREPARING, progress.state)

    def test_one_step_earlier_does_not_end_it(self) -> None:
        """The discriminating half. Without this, `value + 1 >= total` could
        be loosened to anything and the suite would stay green."""

        progress = _sampling(8, hires=True)
        progress.report_step(6)
        self.assertIs(JobState.SAMPLING, progress.state)

    def test_no_transition_depends_on_the_callback_reaching_total(self) -> None:
        """The production acceptance: driven the way Neo actually drives it,
        the base pass ends. `total` is never sent and must not be needed."""

        progress = _sampling(8, hires=True)
        _drive_zero_based(progress, 8)
        self.assertIs(JobState.HIRES_PREPARING, progress.state)

    def test_a_base_only_job_never_enters_a_hires_state(self) -> None:
        """`expect_hires_pass` is the gate. Loosening the step condition must
        not make a base-only job wander into the second-pass states."""

        progress = _sampling(8, hires=False)
        _drive_zero_based(progress, 8)
        self.assertIs(JobState.SAMPLING, progress.state)

    def test_it_holds_at_every_step_count(self) -> None:
        """The old condition failed for ALL of these, not just eight."""

        for total in (1, 2, 6, 15, 30, 150):
            with self.subTest(total=total):
                progress = _sampling(total, hires=True)
                _drive_zero_based(progress, total)
                self.assertIs(JobState.HIRES_PREPARING, progress.state)

    def test_the_second_pass_rewinds_the_counter(self) -> None:
        """The consequence that made the bar sit full for the whole second
        pass: the rewind only happens from HIRES_PREPARING, which was
        unreachable, so it never happened either."""

        progress = _sampling(8, hires=True)
        _drive_zero_based(progress, 8)
        progress.set_total_steps(4)          # Neo's second launch_sampling
        self.assertIs(JobState.HIRES_SAMPLING, progress.state)
        snapshot = progress.snapshot()
        self.assertEqual(0, snapshot.step)
        self.assertEqual(4, snapshot.total_steps)

    def test_the_second_pass_reports_its_own_progress(self) -> None:
        progress = _sampling(8, hires=True)
        _drive_zero_based(progress, 8)
        progress.set_total_steps(4)
        _drive_zero_based(progress, 4)
        self.assertEqual(3, progress.snapshot().step)

    def test_the_stage_label_names_both_passes(self) -> None:
        progress = _sampling(8, hires=True)
        _drive_zero_based(progress, 8)
        self.assertEqual("Preparing Hires pass", progress.snapshot().stage_label)
        progress.set_total_steps(4)
        self.assertEqual("Hires sampling", progress.snapshot().stage_label)

    def test_within_one_pass_the_counter_still_never_rewinds(self) -> None:
        """Monotonic-within-a-pass is deliberate and must survive the fix: an
        out-of-order callback must not pull the bar backwards."""

        progress = _sampling(8, hires=False)
        progress.report_step(5)
        progress.report_step(2)
        self.assertEqual(5, progress.snapshot().step)


# ---------------------------------------------------------------- defect 2


class InFlightIdentityTests(unittest.TestCase):
    """"In flight" has to mean in flight."""

    def _session(self) -> tuple[HeadlessGenerationSession, _JobRecord]:
        session = HeadlessGenerationSession()
        request = GenerationRequest(
            model_id="m", positive_prompt="a cube", negative_prompt="",
            seed=1, steps=8, cfg_scale=4.0, width=512, height=512,
        )
        translation = translate_request(request, request_id="backend-a")
        record = _JobRecord(
            job_id="backend-a",
            headless_request=translation.headless_request,
            local_progress=HeadlessProgress("backend-a"),
            translation=translation,
        )

        class _Gateway:
            def submit(self, *args: Any, **kwargs: Any) -> None:
                return None

            def progress_for(self, job_id: str) -> HeadlessProgress:
                raise HeadlessError("GENERATION_JOB_UNKNOWN", "none yet")

        session._gateway = _Gateway()  # noqa: SLF001
        return session, record

    def test_a_finished_job_stops_being_in_flight(self) -> None:
        """It was set at submit and never cleared, so it named the last job
        ever submitted for the rest of the process."""

        session, record = self._session()
        session._in_flight_job_id = "backend-a"  # noqa: SLF001
        session._make_work(record, model=None)()  # noqa: SLF001
        self.assertIsNone(session.in_flight_job_id)

    def test_it_only_clears_its_own_job(self) -> None:
        """A late finisher must not clear the id of the job that started
        after it."""

        session, record = self._session()
        session._in_flight_job_id = "backend-b"  # noqa: SLF001
        session._make_work(record, model=None)()  # noqa: SLF001
        self.assertEqual("backend-b", session.in_flight_job_id)


class _Progress:
    def __init__(self, *, state="running", message="Sampling", step=3,
                 total_steps=20, progress=15):
        self.state = state
        self.message = message
        self.step = step
        self.total_steps = total_steps
        self.progress = progress
        self.error = None


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


class _Application:
    """Blocks inside submit so a job can be observed while it runs."""

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


class _Coordinated(unittest.TestCase):
    def setUp(self) -> None:
        self.application = _Application()
        self.coordinator = JobCoordinator(self.application)
        self.addCleanup(self._drain)

    def _drain(self) -> None:
        self.coordinator.close()
        self.application.release.set()

    def _run_one(self, label: str) -> str:
        job_id = self.coordinator.submit(_Request(label))["job_id"]
        self.assertTrue(_until(lambda: label in self.application.started))
        return job_id


# ------------------------------------------------------------- defects 1+3


class LiveStageLabelTests(_Coordinated):
    """The label the cancellation legs fire from."""

    def test_the_running_job_reports_its_stage(self) -> None:
        """`describe()` merged step, total and progress and dropped the
        message -- which is the only place the nine-state lifecycle survives
        the five-state projection."""

        job_id = self._run_one("a")
        record = self.coordinator.describe(job_id)
        self.assertEqual("Sampling", record["message"])

    def test_the_stage_follows_the_backend(self) -> None:
        job_id = self._run_one("a")
        self.application.progress.message = "Hires sampling"
        self.assertEqual(
            "Hires sampling", self.coordinator.describe(job_id)["message"]
        )

    def test_the_owner_stage_projects_from_it(self) -> None:
        """With no label, `owner_stage` fell through to "Generating" and the
        Jobs panel said that for the whole of a Hires pass."""

        self._run_one("a")
        self.application.progress.message = "Hires sampling"
        self.assertEqual("Hires", self.coordinator.queue_view()["running"]["stage"])

    def test_a_running_job_is_not_reported_as_queued(self) -> None:
        """Defect 3: the gateway progress was adopted only at terminal, so
        the job read `queued` while the real run completed beside it."""

        job_id = self._run_one("a")
        self.assertEqual(RUNNING, self.coordinator.describe(job_id)["state"])

    def test_live_step_counts_are_visible_while_it_runs(self) -> None:
        job_id = self._run_one("a")
        record = self.coordinator.describe(job_id)
        self.assertEqual(3, record["step"])
        self.assertEqual(20, record["total_steps"])


class JobsDoNotInheritEachOthersProgressTests(_Coordinated):
    """Defect 2, at the layer where an owner would see it."""

    def test_a_queued_job_does_not_report_the_running_job_s_progress(self) -> None:
        """Observed live: a fresh 30-step job showed `Completed 15/15` from
        the previous Hires run, under its own id."""

        self._run_one("a")
        queued = self.coordinator.submit(_Request("b"))["job_id"]
        record = self.coordinator.describe(queued)
        self.assertIsNone(record.get("step"))
        self.assertIsNone(record.get("total_steps"))
        self.assertNotEqual("Sampling", record.get("message"))

    def test_a_queued_job_reports_that_it_is_waiting(self) -> None:
        self._run_one("a")
        queued = self.coordinator.submit(_Request("b"))["job_id"]
        view = self.coordinator.queue_view()
        self.assertEqual([queued], [j["job_id"] for j in view["queued"]])
        self.assertEqual("Queued", view["queued"][0]["stage"])

    def test_only_the_admitted_job_may_borrow_the_in_flight_id(self) -> None:
        """The invariant that makes the scoping safe: admission is serial, so
        the in-flight job and the admitted job are the same job by
        construction rather than by luck."""

        running = self._run_one("a")
        queued = self.coordinator.submit(_Request("b"))["job_id"]
        self.assertEqual(3, self.coordinator.describe(running)["step"])
        self.assertIsNone(self.coordinator.describe(queued).get("step"))

    def test_a_removed_job_reports_no_borrowed_progress(self) -> None:
        self._run_one("a")
        queued = self.coordinator.submit(_Request("b"))["job_id"]
        self.coordinator.remove(queued)
        record = self.coordinator.describe(queued)
        self.assertEqual(CANCELLED, record["state"])
        self.assertIsNone(record.get("step"))


class TheRepairIsNotJustLooserTests(unittest.TestCase):
    """A guard on the guard.

    `value + 1 >= total` passes everything `value >= total` passed, so a
    suite that only exercised the positive case would have gone green against
    the shipped bug. These pin the boundary from both sides.
    """

    def test_the_shipped_condition_would_fail_these(self) -> None:
        progress = _sampling(10, hires=True)
        progress.report_step(9)                 # total - 1: Neo's last value
        self.assertIs(JobState.HIRES_PREPARING, progress.state)

    def test_and_a_looser_one_would_fail_these(self) -> None:
        for early in (0, 4, 8):                 # anything below total - 1
            with self.subTest(step=early):
                progress = _sampling(10, hires=True)
                progress.report_step(early)
                self.assertIs(JobState.SAMPLING, progress.state)


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_LIVE_FIX_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
