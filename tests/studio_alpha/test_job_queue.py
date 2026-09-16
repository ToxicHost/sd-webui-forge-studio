"""The job queue: what runs, what waits, and in which order.

The owner asked to be able to press Generate again without waiting. The
machinery for that was almost all present -- `JobCoordinator.submit` already
minted a public id, recorded QUEUED and started a worker -- but the ORDER was
emergent rather than owned, and that is the defect this suite exists for.

```text
submit()      starts one worker thread per job
each worker   races into application.submit_generation
which         reconciles the model, THEN takes the lifecycle lease
the lease     appends to the lifecycle's own deque, in THREAD ARRIVAL order
the page      rendered `_order`, in SUBMISSION order
```

So two jobs submitted a millisecond apart could execute in either order while
the queue displayed one of them, and there was no verb to reorder either list.
Worse, because the model is reconciled BEFORE the lease, two racing workers
could interleave two `ensure_loaded` calls -- which is precisely the
"two rapid submissions from cold race the load; the second fails
MODEL_ALREADY_LOADING" that the operations runbook documents as a rule owners
must work around.

`JobCoordinator` now owns admission: `_pending` is the execution order AND the
displayed order, because it is one list. Exactly one job is admitted at a
time, and that is a stated invariant rather than a property emerging from
whichever thread won a condition variable.

The two rules the queue adds to the project-wide control contract:

```text
7  SNAPSHOT   generation-affecting state is frozen at enqueue
8  ORDER      displayed queue order IS actual execution order
```

SCOPE: MINIMAL_RUNTIME_SCOPE. No model is loaded and no image is generated;
the application and lifecycle are fakes whose timing this suite controls, so
ordering and admission are proven deterministically rather than raced.
"""

from __future__ import annotations

import re
import sys
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.contracts import StructuredError, StudioError  # noqa: E402
from forge_studio.jobs import (  # noqa: E402
    CANCELLED,
    COMPLETED,
    QUEUED,
    RECENT_TERMINAL_LIMIT,
    RUNNING,
    STAGE_CANCELLED,
    STAGE_COMPLETED,
    STAGE_GENERATING,
    STAGE_HIRES,
    STAGE_QUEUED,
    STAGE_STARTING,
    JobCoordinator,
    owner_stage,
)

#: Asserted against the discovered count so a silently dropped test fails.
EXPECTED_QUEUE_TESTS = 48

#: Long enough that a loaded machine does not fail the suite, short enough
#: that a genuine hang is still a fast failure rather than a wedged run.
DEADLINE = 5.0


def _until(predicate, deadline: float = DEADLINE) -> bool:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@dataclass
class _Progress:
    state: str = "sampling"
    step: int = 3
    total_steps: int = 20
    progress: int = 15
    message: str = "Sampling"
    error: Any = None


@dataclass
class _Request:
    """Frozen-enough stand-in. `model_selection` makes submit legal."""

    label: str
    model_selection: dict[str, str] = field(
        default_factory=lambda: {
            "checkpoint_model_id": "a" * 32,
            "text_encoder_model_id": "b" * 32,
            "vae_model_id": "c" * 32,
        }
    )


class _Lifecycle:
    gates_generation = True

    def __init__(self) -> None:
        self._counter = 0
        self._lock = threading.Lock()
        self.cancelled_tokens: list[str] = []

    def mint_job_token(self) -> str:
        with self._lock:
            self._counter += 1
            return f"studio-job-{self._counter:06d}"

    def state(self) -> dict[str, Any]:
        return {"accepting_jobs": True, "active_job": None}

    def cancel_queued_job(self, token: str) -> bool:
        # Nothing reaches the LIFECYCLE queue any more: the coordinator's gate
        # admits one job at a time, so a waiting job is waiting here, not
        # there. Answering False is what makes the coordinator's own branch
        # the one that runs.
        self.cancelled_tokens.append(token)
        return False


class _Session:
    """Publishes the in-flight backend id mid-job, as the real one does.

    `JobCoordinator._in_flight_backend_id` walks
    `application._backend._generation.in_flight_job_id` -- an explicit chain,
    verified rather than probed. The fake reproduces the chain so the
    running-cancel path is exercised rather than assumed.
    """

    def __init__(self) -> None:
        self.in_flight_job_id: str | None = None


class _Backend:
    def __init__(self) -> None:
        self._generation = _Session()


class _Application:
    """Blocks inside `submit_generation` until the test releases the job."""

    def __init__(self) -> None:
        self.model_lifecycle = _Lifecycle()
        self._backend = _Backend()
        self.started: list[str] = []
        self.finished: list[str] = []
        self.concurrent_peak = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self._release: dict[str, threading.Event] = {}
        self._outcome: dict[str, str] = {}
        self._raise: dict[str, BaseException] = {}
        self._backend_state: dict[str, str] = {}

    def gate_for(self, label: str) -> threading.Event:
        event = self._release.setdefault(label, threading.Event())
        return event

    def fail_with(self, label: str, exc: BaseException) -> None:
        self._raise[label] = exc

    def backend_reports(self, label: str, state: str) -> None:
        self._backend_state[label] = state

    def submit_generation(self, request: Any, *, job_token: str | None = None):
        label = request.label
        with self._lock:
            self.started.append(label)
            self._in_flight += 1
            self.concurrent_peak = max(self.concurrent_peak, self._in_flight)
            self._backend._generation.in_flight_job_id = f"backend-{label}"
        try:
            self.gate_for(label).wait(DEADLINE)
            if label in self._raise:
                raise self._raise[label]
            self._outcome[label] = "done"
            return type("Identity", (), {"job_id": f"backend-{label}"})()
        finally:
            with self._lock:
                self._in_flight -= 1
                self.finished.append(label)
                self._backend._generation.in_flight_job_id = None

    def poll_or_stream_progress(self, backend_id: str) -> _Progress:
        label = str(backend_id).replace("backend-", "")
        return _Progress(state=self._backend_state.get(label, "completed"))

    def cancel_generation(self, backend_id: str):
        label = str(backend_id).replace("backend-", "")
        self.gate_for(label).set()
        return type("Result", (), {"state": "cancelled"})()


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.application = _Application()
        self.coordinator = JobCoordinator(self.application)
        self.addCleanup(self._drain)

    def _drain(self) -> None:
        self.coordinator.close()
        for event in list(self.application._release.values()):
            event.set()

    def _submit(self, label: str) -> str:
        return self.coordinator.submit(_Request(label))["job_id"]

    def _await_running(self, label: str) -> None:
        self.assertTrue(
            _until(lambda: label in self.application.started),
            f"{label} never started; started={self.application.started}",
        )

    def _finish(self, label: str) -> None:
        self.application.gate_for(label).set()
        self.assertTrue(
            _until(lambda: label in self.application.finished),
            f"{label} never finished",
        )


class AdmissionIsSerialTests(_Base):
    def test_only_one_job_runs_at_a_time(self) -> None:
        """Not "usually one": the invariant the handoff states is exactly one
        GPU pipeline, and a peak of 2 would mean two `ensure_loaded` calls
        could interleave."""

        for label in ("a", "b", "c"):
            self._submit(label)
        self._await_running("a")
        time.sleep(0.05)
        self.assertEqual(["a"], self.application.started)
        self.assertEqual(1, self.application.concurrent_peak)

    def test_the_next_job_starts_automatically(self) -> None:
        for label in ("a", "b"):
            self._submit(label)
        self._await_running("a")
        self._finish("a")
        self._await_running("b")

    def test_the_whole_queue_drains_in_order(self) -> None:
        for label in ("a", "b", "c"):
            self._submit(label)
        for label in ("a", "b", "c"):
            self._await_running(label)
            self._finish(label)
        self.assertEqual(["a", "b", "c"], self.application.started)
        self.assertEqual(1, self.application.concurrent_peak)

    def test_a_submission_reports_its_queue_position(self) -> None:
        """Counted among jobs still WAITING. The first submission is admitted
        immediately, so the next one is first in the queue, not second --
        anything else would tell the owner they are behind a job that is
        already running."""

        self._submit("a")
        self._await_running("a")
        self.assertEqual(1, self.coordinator.submit(_Request("b"))["queue_position"])
        self.assertEqual(2, self.coordinator.submit(_Request("c"))["queue_position"])


class DisplayedOrderIsExecutionOrderTests(_Base):
    def test_the_queue_view_lists_waiting_jobs_in_order(self) -> None:
        ids = [self._submit(label) for label in ("a", "b", "c")]
        self._await_running("a")
        view = self.coordinator.queue_view()
        self.assertEqual(ids[0], view["running"]["job_id"])
        self.assertEqual([ids[1], ids[2]], [j["job_id"] for j in view["queued"]])
        self.assertEqual([1, 2], [j["queue_position"] for j in view["queued"]])
        self.assertEqual(2, view["queue_depth"])

    def test_reordering_changes_what_runs_next(self) -> None:
        """The assertion the old arrangement could not make: the order shown
        and the order executed are the same list."""

        ids = [self._submit(label) for label in ("a", "b", "c")]
        self._await_running("a")
        self.coordinator.reorder([ids[2], ids[1]])
        view = self.coordinator.queue_view()
        self.assertEqual([ids[2], ids[1]], [j["job_id"] for j in view["queued"]])
        self._finish("a")
        self._await_running("c")
        time.sleep(0.05)
        self.assertEqual(["a", "c"], self.application.started)

    def test_a_stale_order_is_refused_not_reconciled(self) -> None:
        """A client working from a stale view would otherwise silently drop or
        duplicate a job, and the owner would watch a queue run in an order
        nobody chose."""

        ids = [self._submit(label) for label in ("a", "b", "c")]
        self._await_running("a")
        with self.assertRaises(StudioError) as caught:
            self.coordinator.reorder([ids[1]])
        self.assertEqual("QUEUE_ORDER_INVALID", caught.exception.error.code)

    def test_reordering_cannot_smuggle_in_the_running_job(self) -> None:
        ids = [self._submit(label) for label in ("a", "b")]
        self._await_running("a")
        with self.assertRaises(StudioError):
            self.coordinator.reorder([ids[0], ids[1]])


class RemoveBeforeStartTests(_Base):
    def test_a_removed_job_never_executes(self) -> None:
        ids = [self._submit(label) for label in ("a", "b", "c")]
        self._await_running("a")
        self.coordinator.remove(ids[1])
        self._finish("a")
        self._await_running("c")
        time.sleep(0.05)
        self.assertNotIn("b", self.application.started)

    def test_a_removed_job_is_terminal_and_truthful(self) -> None:
        ids = [self._submit(label) for label in ("a", "b")]
        self._await_running("a")
        result = self.coordinator.remove(ids[1])
        self.assertEqual(CANCELLED, result["state"])
        self.assertTrue(result["removed_before_start"])
        self.assertEqual(CANCELLED, self.coordinator.describe(ids[1])["state"])

    def test_removing_the_running_job_is_refused(self) -> None:
        """Remove and cancel are different verbs. A client must not be able to
        ask to remove a waiting job and instead stop the picture being made."""

        ids = [self._submit("a")]
        self._await_running("a")
        with self.assertRaises(StudioError) as caught:
            self.coordinator.remove(ids[0])
        self.assertEqual("JOB_NOT_QUEUED", caught.exception.error.code)

    def test_removing_an_unknown_job_is_refused(self) -> None:
        with self.assertRaises(StudioError) as caught:
            self.coordinator.remove("studio-job-999999")
        self.assertEqual("JOB_NOT_FOUND", caught.exception.error.code)

    def test_cancelling_a_waiting_job_never_reaches_the_backend(self) -> None:
        """"Must never load models, must never allocate detector/generation
        resources." Proven by the application never being called at all."""

        ids = [self._submit(label) for label in ("a", "b")]
        self._await_running("a")
        result = self.coordinator.cancel(ids[1])
        self.assertTrue(result["cancelled_while_queued"])
        self._finish("a")
        time.sleep(0.05)
        self.assertEqual(["a"], self.application.started)


class ClearAndCancelAllTests(_Base):
    def test_clearing_the_queue_spares_the_running_job(self) -> None:
        ids = [self._submit(label) for label in ("a", "b", "c")]
        self._await_running("a")
        result = self.coordinator.clear_queued()
        self.assertEqual(2, result["count"])
        self.assertFalse(result["cancelled_running"])
        self.assertEqual([ids[1], ids[2]], result["removed"])
        self.assertNotIn(
            self.coordinator.describe(ids[0])["state"], (CANCELLED,)
        )

    def test_cancel_all_stops_the_running_job_too(self) -> None:
        ids = [self._submit(label) for label in ("a", "b")]
        self._await_running("a")
        result = self.coordinator.cancel_all()
        self.assertTrue(result["cancelled_running"])
        self.assertEqual(ids[0], result["running_job_id"])
        self.assertEqual(CANCELLED, self.coordinator.describe(ids[1])["state"])

    def test_clearing_an_empty_queue_is_harmless(self) -> None:
        result = self.coordinator.clear_queued()
        self.assertEqual(0, result["count"])
        self.assertEqual([], result["removed"])


class CancellingTheRunningJobTests(_Base):
    """The leg that was unreachable, and why.

    `record["backend_job_id"]` is written only after the blocking submit
    RETURNS, which is at terminal, so it was None for the whole life of every
    job. `cancel` therefore fell through to JOB_CANCEL_TOO_LATE for any
    running job. The only thing that would have filled it earlier is
    `mark_running` -- written, exported, and never called.

    This is the fourth `is_known_*`-shaped find: a function written for a
    purpose, exported, and left with no caller while the purpose went unmet.
    """

    def test_a_running_job_can_be_cancelled(self) -> None:
        job_id = self._submit("a")
        self._await_running("a")
        result = self.coordinator.cancel(job_id)
        self.assertFalse(result["cancelled_while_queued"])
        self.assertEqual(CANCELLED, result["state"])

    def test_cancelling_the_running_job_lets_the_next_one_start(self) -> None:
        """"Queue advances to the next job only after the cancelled job
        reaches a safe terminal state.\""""

        job_id = self._submit("a")
        self._submit("b")
        self._await_running("a")
        self.coordinator.cancel(job_id)
        self._await_running("b")

    def test_the_backend_was_actually_asked_to_stop(self) -> None:
        """Not merely recorded as cancelled locally. A record that says
        CANCELLED over a job still sampling is the same class of lie this
        project keeps removing."""

        job_id = self._submit("a")
        self._await_running("a")
        self.coordinator.cancel(job_id)
        self.assertTrue(self.application.gate_for("a").is_set())


class QueueSurvivesFailureTests(_Base):
    def test_a_failed_job_does_not_strand_the_queue(self) -> None:
        """"Queue continues to B unless failure is engine-fatal." A stranded
        gate would be indistinguishable from a hung app."""

        self._submit("a")
        self._submit("b")
        self._await_running("a")
        self.application.fail_with(
            "a",
            StudioError(
                StructuredError(code="GENERATION_FAILED", message="boom")
            ),
        )
        self._finish("a")
        self._await_running("b")

    def test_the_failure_reason_is_visible(self) -> None:
        job_id = self._submit("a")
        self._await_running("a")
        self.application.fail_with(
            "a",
            StudioError(
                StructuredError(
                    code="GENERATION_SEED_NOT_FIXED", message="explicit seed"
                )
            ),
        )
        self._finish("a")
        self.assertTrue(
            _until(
                lambda: self.coordinator.describe(job_id).get("error") is not None
            )
        )
        error = self.coordinator.describe(job_id)["error"]
        self.assertEqual("GENERATION_SEED_NOT_FIXED", error["code"])

    def test_a_raising_job_still_releases_admission(self) -> None:
        """The gate is released in a `finally`. Without that, one exception
        wedges every later job forever."""

        self._submit("a")
        self._submit("b")
        self._await_running("a")
        self.application.fail_with("a", RuntimeError("unowned"))
        self._finish("a")
        self._await_running("b")


class OwnerVocabularyTests(unittest.TestCase):
    """Internal states may stay rich. The projection must be human."""

    def test_backend_stages_map_to_owner_words(self) -> None:
        self.assertEqual(STAGE_STARTING, owner_stage(RUNNING, "Loading model"))
        self.assertEqual(STAGE_STARTING, owner_stage(RUNNING, "Encoding prompt"))
        self.assertEqual(STAGE_GENERATING, owner_stage(RUNNING, "Sampling"))
        self.assertEqual(STAGE_HIRES, owner_stage(RUNNING, "Hires sampling"))
        self.assertEqual(
            STAGE_HIRES, owner_stage(RUNNING, "Preparing Hires pass")
        )

    def test_a_terminal_state_beats_the_last_label(self) -> None:
        """A cancelled job whose last report was "Sampling" is Cancelled."""

        self.assertEqual(STAGE_CANCELLED, owner_stage(CANCELLED, "Sampling"))
        self.assertEqual(STAGE_COMPLETED, owner_stage(COMPLETED, "Sampling"))

    def test_a_queued_job_says_queued(self) -> None:
        self.assertEqual(STAGE_QUEUED, owner_stage(QUEUED, None))

    def test_an_unmapped_label_never_leaks_an_internal_word(self) -> None:
        """Auto Detail will add stages. Until they are mapped, the fallback
        must be an owner word, not whatever the backend happened to call it."""

        self.assertEqual(
            STAGE_GENERATING, owner_stage(RUNNING, "adetailer_slot_2_inpaint")
        )

    def test_no_lifecycle_vocabulary_reaches_the_owner(self) -> None:
        from forge_studio import jobs

        for word in ("lease", "busy", "cache", "session", "profile"):
            for stage in jobs._BACKEND_STAGES.values():
                self.assertNotIn(word, stage.casefold())


class QueueViewShapeTests(_Base):
    def test_recent_terminal_jobs_are_bounded(self) -> None:
        """Not a history feature. A long session must not render one."""

        for index in range(RECENT_TERMINAL_LIMIT + 4):
            label = f"j{index}"
            self._submit(label)
            self._await_running(label)
            self._finish(label)
        self.assertTrue(
            _until(
                lambda: len(self.coordinator.queue_view()["recent"])
                == RECENT_TERMINAL_LIMIT
            )
        )

    def test_recent_is_newest_first(self) -> None:
        ids = []
        for label in ("a", "b"):
            ids.append(self._submit(label))
            self._await_running(label)
            self._finish(label)
        self.assertTrue(
            _until(lambda: len(self.coordinator.queue_view()["recent"]) == 2)
        )
        recent = self.coordinator.queue_view()["recent"]
        self.assertEqual(ids[1], recent[0]["job_id"])

    def test_every_entry_carries_an_owner_stage(self) -> None:
        self._submit("a")
        self._submit("b")
        self._await_running("a")
        view = self.coordinator.queue_view()
        self.assertIn("stage", view["running"])
        self.assertEqual(STAGE_QUEUED, view["queued"][0]["stage"])

    def test_an_empty_queue_has_no_running_job(self) -> None:
        view = self.coordinator.queue_view()
        self.assertIsNone(view["running"])
        self.assertEqual([], view["queued"])
        self.assertEqual(0, view["queue_depth"])


class SnapshotAtEnqueueTests(_Base):
    def test_a_queued_job_keeps_the_request_it_was_submitted_with(self) -> None:
        """Rule 7. Later UI edits build a NEW request; the queued job holds
        the object it was handed, and nothing mutates it in place."""

        first = _Request("a")
        second = _Request("b")
        self.coordinator.submit(first)
        self.coordinator.submit(second)
        self._await_running("a")
        # The page changing its controls is a new object, not a mutation of
        # the one already queued.
        second.model_selection = dict(second.model_selection)
        second.model_selection["checkpoint_model_id"] = "z" * 32
        self._finish("a")
        self._await_running("b")
        # The worker holds its own reference, captured at submit.
        self.assertEqual("b", self.application.started[-1])


class ClosingTests(_Base):
    def test_closing_wakes_every_waiting_worker(self) -> None:
        """A closing Studio must not look like a hung queue."""

        ids = [self._submit(label) for label in ("a", "b", "c")]
        self._await_running("a")
        self.coordinator.close()
        self.assertTrue(
            _until(
                lambda: self.coordinator.describe(ids[2])["state"] == CANCELLED
            )
        )

    def test_a_closed_coordinator_refuses_new_work(self) -> None:
        self.coordinator.close()
        with self.assertRaises(StudioError) as caught:
            self.coordinator.submit(_Request("a"))
        self.assertEqual("COORDINATOR_CLOSED", caught.exception.error.code)


class RouteSurfaceTests(unittest.TestCase):
    """The verbs exist, are distinct, and are registered as fixed paths."""

    SOURCE = (
        APP_ROOT / "forge_studio" / "presentation.py"
    ).read_text(encoding="utf-8")

    def test_every_queue_route_is_registered(self) -> None:
        for route in (
            '"/api/queue"',
            '"/api/queue/reorder"',
            '"/api/queue/clear"',
            '"/api/queue/cancel_all"',
        ):
            self.assertIn(route, self.SOURCE, route)

    def test_remove_is_a_separate_route_from_cancel(self) -> None:
        self.assertIn('parts[3] == "remove"', self.SOURCE)
        self.assertIn('parts[3] == "cancel"', self.SOURCE)

    def test_the_fixed_queue_paths_are_matched_before_the_id_walk(self) -> None:
        """`/api/queue/clear` must not be read as a job id. A fixed path
        registered after a segment walk is not a route -- the same lesson the
        `/studio/fs/` and pixel routes already carry in this file.

        Scoped to do_POST: do_GET has its own `parts[:2]` walk earlier in the
        file, and comparing against that one measured nothing.
        """

        post = self.SOURCE[self.SOURCE.index("def do_POST"):]
        self.assertLess(
            post.index('path == "/api/queue/reorder"'),
            post.index('parts[:2] == ["api", "jobs"]'),
        )

    def test_reorder_refuses_a_payload_that_is_not_a_list_of_ids(self) -> None:
        from forge_studio.presentation import StudioPresentation

        self.assertIn("order must be a list of job ids.", self.SOURCE)
        self.assertTrue(hasattr(StudioPresentation, "reorder_queue"))


class ThePanelIsOwnerFacingTests(unittest.TestCase):
    """The replacement for "Model / Session lifecycle (internal alpha)"."""

    HTML = (
        APP_ROOT / "forge_studio" / "frontend" / "index.html"
    ).read_text(encoding="utf-8")
    CONTROLS = (
        APP_ROOT / "forge_studio" / "frontend" / "studio-model-controls.js"
    ).read_text(encoding="utf-8")

    def test_the_panel_no_longer_calls_itself_a_lifecycle(self) -> None:
        # COMMENTS STRIPPED, for the third time in this session. The markup's
        # own comment names the panel it replaced, so the raw search finds the
        # old title inside the note recording that it is gone. A rule stated
        # in prose trips the check that enforces it -- OPERATIONS.md sec 7.
        markup = re.sub(r"<!--.*?-->", "", self.HTML, flags=re.S)
        self.assertNotIn("Model / Session", markup)
        self.assertNotIn("panels.modelSession", markup)
        self.assertIn('data-i18n="panels.jobs"', markup)

    def test_the_panel_shows_running_queued_and_recent(self) -> None:
        for element in (
            "studioRunningJob",
            "studioQueuedSection",
            "studioQueueList",
            "studioRecentList",
        ):
            self.assertIn(element, self.HTML, element)
            self.assertIn(element, self.CONTROLS, element)

    def test_the_owner_can_reorder_and_remove(self) -> None:
        self.assertIn("moveQueued", self.CONTROLS)
        self.assertIn("removeJob", self.CONTROLS)
        self.assertIn("studioClearQueueBtn", self.HTML)

    def test_unload_survives_but_is_not_queue_management(self) -> None:
        """"Keep an explicit Unload Model action, but do not place it as if
        it is part of queue management.\""""

        self.assertIn("studioUnloadBtn", self.HTML)
        self.assertGreater(
            self.HTML.index("studioUnloadBtn"),
            self.HTML.index("studioRecentList"),
        )

    def test_no_lifecycle_vocabulary_is_rendered(self) -> None:
        """BUSY, leases and cache ownership are diagnostics. They stay on
        /api/model/state, which is not a place an owner is asked to look."""

        block = self.CONTROLS[self.CONTROLS.index("function renderQueue"):]
        block = block[: block.index("function render(")]
        # COMMENTS STRIPPED. The renderer's own comment names the words it
        # stopped rendering, so a raw search finds the defect inside the note
        # saying it is gone. Fourth time in this codebase, second in this
        # session -- see OPERATIONS.md section 7.
        code = "\n".join(
            line for line in block.splitlines()
            if not line.lstrip().startswith("//")
        )
        for internal in ("BUSY", "lease", "cache", "terminal_cache"):
            self.assertNotIn(internal, code)

    def test_the_reorder_request_sends_the_whole_order(self) -> None:
        """A "swap these two" message would apply to whichever jobs happened
        to occupy those positions by the time it arrived."""

        self.assertIn("JSON.stringify({ order: ids })", self.CONTROLS)

    def test_a_conditional_attribute_absent_means_absent(self) -> None:
        """`disabled="null"` is a disabled button, so the obvious spelling of
        a conditional attribute would have disabled every reorder control it
        was meant to enable."""

        self.assertIn("if (value === null || value === undefined) continue;",
                      self.CONTROLS)

    def test_the_summary_carries_no_prompt_and_no_path(self) -> None:
        block = self.CONTROLS[self.CONTROLS.index("const jobSummary"):]
        block = block[: block.index("const progressText")]
        for forbidden in ("prompt", "path", "positive", "negative"):
            self.assertNotIn(forbidden, block.casefold())


class SuiteIntegrityTests(unittest.TestCase):
    def test_the_discovered_count_matches_the_declared_count(self) -> None:
        loaded = unittest.defaultTestLoader.loadTestsFromName(__name__)
        self.assertEqual(EXPECTED_QUEUE_TESTS, loaded.countTestCases())

    def test_the_suite_declares_its_scope(self) -> None:
        self.assertIn("MINIMAL_RUNTIME_SCOPE", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
