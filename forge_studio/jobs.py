"""One public job identity from submission to terminal state.

The final live run exposed a structural gap, not a bug: a lifecycle-queued
job had NO id any transport verb could name, because the backend id is minted
only after the lease is acquired. Cancelling a queued job therefore required
in-process access -- fine for a runner, unusable for a frontend.

`JobCoordinator` closes that gap by making the lifecycle token THE public job
id, minted before anything else happens:

```text
submit(request)
  public_id = lifecycle.mint_job_token()      exists BEFORE any lease/backend
  record QUEUED
  worker thread:
    application.submit_generation(request, job_token=public_id)
      -> lifecycle queues/acquires under public_id
      -> backend mints its internal id AFTER the lease
    record RUNNING (backend id mapped) ... COMPLETED / FAILED
cancel(public_id)
  queued   -> lifecycle.cancel_queued_job(public_id): terminal CANCELLED,
              never reaches adapter/session/port, no processing object,
              no publication, no per-job release
  running  -> application.cancel_generation(mapped backend id) -- the
              pre-existing active-cancel path, unchanged
  terminal -> reported as already terminal, idempotently
  unknown  -> stable scalar refusal
```

One id, stable through QUEUED -> RUNNING -> COMPLETED/FAILED/CANCELLED. The
backend's internal id still exists but never needs to reach a client.

Threading: one worker thread per submitted job, created at submission. The
thread was always there -- previously it was the HTTP handler's own thread
blocking inside submit; this moves the same wait behind an id the client can
poll and cancel. Waiting jobs block inside `acquire` on the lifecycle's
condition, exactly as before.

Import-safe, stdlib-only, and mutation is serialized behind one lock.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from .contracts import StructuredError, StudioError
from .job_log import JobLog

JOB_NOT_FOUND = "JOB_NOT_FOUND"
JOB_ALREADY_TERMINAL = "JOB_ALREADY_TERMINAL"
JOB_CANCEL_TOO_LATE = "JOB_CANCEL_TOO_LATE"
JOB_NOT_QUEUED = "JOB_NOT_QUEUED"
QUEUE_ORDER_INVALID = "QUEUE_ORDER_INVALID"

#: Public job states. QUEUED covers minted-through-waiting; RUNNING begins
#: when the lease is granted and the backend accepts the submission.
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATES = frozenset({COMPLETED, FAILED, CANCELLED})

#: How many terminal jobs the queue view keeps. The record store itself is
#: unbounded -- `describe(job_id)` must keep answering for a job whose result
#: the page is still holding -- but the owner-facing "Recent" list is not a
#: history feature and a long session should not render one.
RECENT_TERMINAL_LIMIT = 12

#: The owner-facing vocabulary, mapped from the backend's richer stage labels.
#:
#: The backend keeps nine lifecycle states and gains more with every phase;
#: the owner gets words about their picture. This is a projection, not a
#: rename -- `state` is unchanged on the wire, and `stage` is added beside it,
#: so nothing that reads the existing contract moves.
STAGE_QUEUED = "Queued"
STAGE_STARTING = "Starting"
STAGE_GENERATING = "Generating"
STAGE_HIRES = "Hires"
#: One owner-facing word for all three slots. The backend keeps them apart --
#: `message` still says "Auto Detail 2" -- but the owner is being told what is
#: happening to their picture, and "which of three detector passes" is not
#: that. The acceptance harnesses read the LABEL for slot order.
STAGE_AUTO_DETAIL = "Auto Detail"
STAGE_PUBLISHING = "Publishing"
STAGE_COMPLETED = "Completed"
STAGE_FAILED = "Failed"
STAGE_CANCELLED = "Cancelled"

#: Backend stage label -> owner-facing stage. An unmapped label still falls
#: back to Generating rather than leaking an internal word, which is what kept
#: the Auto Detail slots degrading safely before they were mapped here.
_BACKEND_STAGES: dict[str, str] = {
    "queued": STAGE_QUEUED,
    "loading model": STAGE_STARTING,
    "encoding prompt": STAGE_STARTING,
    "sampling": STAGE_GENERATING,
    "preparing hires pass": STAGE_HIRES,
    "hires sampling": STAGE_HIRES,
    "auto detail 1": STAGE_AUTO_DETAIL,
    "auto detail 2": STAGE_AUTO_DETAIL,
    "auto detail 3": STAGE_AUTO_DETAIL,
    "decoding": STAGE_PUBLISHING,
    "publishing result": STAGE_PUBLISHING,
    "completed": STAGE_COMPLETED,
    "cancelled": STAGE_CANCELLED,
    "failed": STAGE_FAILED,
}

_TERMINAL_STAGES: dict[str, str] = {
    COMPLETED: STAGE_COMPLETED,
    FAILED: STAGE_FAILED,
    CANCELLED: STAGE_CANCELLED,
}


def owner_stage(state: str, backend_stage: str | None) -> str:
    """One human stage word for one job.

    Terminal state wins over any label: a cancelled job that last reported
    "Sampling" is Cancelled, not Generating. That ordering is the whole
    reason this is a function rather than a dict lookup at the call site.
    """

    terminal = _TERMINAL_STAGES.get(state)
    if terminal is not None:
        return terminal
    if state == QUEUED:
        return STAGE_QUEUED
    label = (backend_stage or "").strip().casefold()
    return _BACKEND_STAGES.get(label, STAGE_GENERATING)


def _error(code: str, message: str, field: str | None = None) -> StudioError:
    return StudioError(StructuredError(code=code, message=message, field=field))


def _failure_record(exc: BaseException) -> dict[str, str]:
    """Record why a job failed, keeping an owned error when there is one.

    `StudioError` is handled by its own branch. A backend adapter raises its
    OWN exception type instead -- `HeadlessBackendError` is not a
    `StudioError` -- while still carrying an owned `StructuredError` built
    deliberately for the client. Recording `type(exc).__name__` discarded
    exactly that: a live generation refused for `GENERATION_SEED_NOT_FIXED`,
    with a plain-English message and `field="seed"`, reached the owner as the
    single word "HeadlessBackendError" and took a whole diagnostic session to
    attribute.

    Only the owned, already-plain `code`/`message` are copied -- the same two
    fields the `StudioError` branch reports and the same pair
    `presentation.py` reads back off an exception -- so no runtime, path, or
    traceback detail can reach the wire. Anything else keeps the opaque
    fallback.
    """

    owned = getattr(exc, "error", None)
    code = getattr(owned, "code", None)
    message = getattr(owned, "message", None)
    if isinstance(code, str) and code and isinstance(message, str) and message:
        return {"code": code, "message": message}
    return {"code": "GENERATION_FAILED", "message": type(exc).__name__}


def _reported_reason(progress: Any) -> dict[str, Any] | None:
    """The failure detail a backend REPORTED on its progress, or None.

    The sibling of `_failure_record`, for the path where nothing was raised.
    A backend that catches its own failure and marks its progress FAILED
    returns normally, so the handlers in `_run` never see an exception and the
    reason reaches the record only if something reads it off the observation.

    Deliberately the SAME discipline as `_failure_record`: only an owned,
    already-plain `code`/`message` pair travels, so no runtime, path or
    traceback detail can reach the wire. Anything else yields None, and the
    record keeps the honest `null` rather than an invented reason.
    """

    owned = getattr(progress, "error", None)
    code = getattr(owned, "code", None)
    message = getattr(owned, "message", None)
    if isinstance(code, str) and code and isinstance(message, str) and message:
        return {"code": code, "message": message}
    return None


def _expansion_facts(request: Any) -> dict[str, Any] | None:
    """The wildcard resolution, flattened for a JSON view.

    None when nothing expanded, which is the common case and keeps the job
    view unchanged for every host without a wildcard service.

    Prompts appear here deliberately. `FirstImageRequest.to_dict` omits them
    and should -- that is a diagnostic view of a REQUEST, and its length field
    exists so a bug report carries no prose. This is the owner's own result
    record, and section 68 requires both halves of the prompt in it.
    """

    record = getattr(request, "prompt_expansion", None)
    if record is None or not getattr(record, "expanded", False):
        return None
    return {
        "original_prompt": record.original_prompt,
        "resolved_prompt": record.resolved_prompt,
        "original_negative_prompt": record.original_negative_prompt,
        "resolved_negative_prompt": record.resolved_negative_prompt,
        "choices": list(record.choices),
        "missing": list(record.missing),
        "warnings": list(record.warnings),
        "truncated": bool(record.truncated),
        "seed": int(record.seed),
        "version": int(record.version),
    }


class JobCoordinator:
    """Public job records over the application's blocking submit."""

    def __init__(self, application: Any) -> None:
        lifecycle = getattr(application, "model_lifecycle", None)
        if lifecycle is None or not getattr(lifecycle, "gates_generation", False):
            raise _error(
                "COORDINATOR_REQUIRES_LIFECYCLE",
                "Job coordination needs a lifecycle-gated application.",
            )
        self._application = application
        self._lifecycle = lifecycle
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._closed = False
        #: Public ids awaiting admission, in EXECUTION order. This list is the
        #: queue: the order shown to the owner and the order jobs actually run
        #: in are the same list, which rule 8 requires and the previous
        #: arrangement could not promise.
        #:
        #: Ordering used to be emergent. `submit` starts one worker thread per
        #: job and each raced to `_lease_locked`, which appends to the
        #: lifecycle's own deque -- so the lifecycle queue was in THREAD
        #: ARRIVAL order while the page rendered `_order`, submission order.
        #: Two jobs submitted a millisecond apart could run in either order
        #: while the queue showed one of them.
        #:
        #: Worse, `application.submit_generation` reconciles the model BEFORE
        #: taking the lease, so racing workers could interleave two
        #: `ensure_loaded` calls -- which is the MODEL_ALREADY_LOADING failure
        #: the runbook documents as "two rapid submissions from cold race the
        #: load".
        self._pending: list[str] = []
        #: The one job allowed past the gate. Exactly one GPU pipeline runs at
        #: a time, and that is now a stated invariant rather than a property
        #: emerging from whichever thread won a condition variable.
        self._admitted: str | None = None
        self._gate = threading.Condition(self._lock)
        #: Per-job console output. Nothing was logged per job -- not one line
        #: -- across an entire owner session driving four generations. This is
        #: the layer that knows a job's public id, its queue position and its
        #: terminal outcome, so it is the layer that reports them.
        self._log = JobLog(names=getattr(application, "model_display_name", None))

    # -- submission ----------------------------------------------------------

    def submit(self, request: Any) -> dict[str, Any]:
        """Mint the public id, record QUEUED, and start the worker."""

        with self._lock:
            if self._closed:
                raise _error(
                    "COORDINATOR_CLOSED", "Studio is shutting down.", "job_id"
                )
            # A submission that cannot possibly run is still refused HERE,
            # synchronously, before a record or worker exists: a refusal the
            # client has to poll a job to discover is worse than one it gets
            # from the call it made. A race with a concurrent unload still
            # fails safely in the worker.
            #
            # But "nothing is loaded" is no longer such a case. A job that
            # NAMES its selection makes itself runnable -- the application
            # reconciles the selection before leasing -- so refusing on
            # `accepting_jobs` here would reinstate the Load step at the one
            # gate the owner cannot see. This was the last refusal standing
            # between the owner and a Load-free Generate, and it survived
            # every unit test because they all submitted with a model already
            # resident.
            snapshot = self._lifecycle.state()
            names_a_selection = bool(getattr(request, "model_selection", None))
            if not snapshot.get("accepting_jobs") and not names_a_selection:
                raise _error(
                    "MODEL_NOT_READY",
                    "No warm model session is ready for a job.",
                    "job_id",
                )
            public_id = self._lifecycle.mint_job_token()
            record: dict[str, Any] = {
                "job_id": public_id,
                "state": QUEUED,
                "backend_job_id": None,
                "error": None,
                "cancel_requested": False,
                # Section 28: the wildcard facts must survive the job, not just
                # the request. They rode on the request and no consumer could
                # read them -- the headless side never receives them (Studio
                # resolves before translating, deliberately), and the result
                # metadata is built there. So the job record is where the two
                # halves can actually meet a reader.
                #
                # Recorded at SUBMIT, not at terminal: a cancelled or failed
                # job still answers what it was asked to draw.
                "prompt_expansion": _expansion_facts(request),
            }
            self._jobs[public_id] = record
            self._order.append(public_id)
            self._pending.append(public_id)
            position = len(self._pending)
            depth = len(self._pending)
        self._log.accepted(
            public_id,
            queue_position=position,
            selection=dict(getattr(request, "model_selection", None) or {}),
        )
        self._log.queue("enqueued", job_id=public_id, depth=depth)

        worker = threading.Thread(
            target=self._run, args=(public_id, request), daemon=True,
            name=f"studio-job-worker-{public_id}",
        )
        worker.start()
        return {
            "job_id": public_id,
            "state": QUEUED,
            "queue_position": position,
        }

    # -- admission -------------------------------------------------------------

    def _await_admission(self, public_id: str) -> bool:
        """Block until this job is at the head and nothing else is running.

        Returns False when the job must not run at all -- removed, cancelled,
        or the coordinator closed while it waited. A job that returns False
        here has never touched the lifecycle, never reconciled a model and
        never allocated anything, which is exactly what "removed before start"
        has to mean.
        """

        with self._gate:
            while True:
                record = self._jobs.get(public_id)
                if record is None or record["state"] in TERMINAL_STATES:
                    self._drop_pending(public_id)
                    return False
                if self._closed:
                    self._drop_pending(public_id)
                    record["state"] = CANCELLED
                    return False
                if (
                    self._admitted is None
                    and self._pending
                    and self._pending[0] == public_id
                ):
                    self._pending.pop(0)
                    self._admitted = public_id
                    record["state"] = RUNNING
                    self._log.queue(
                        "starting", job_id=public_id, depth=len(self._pending)
                    )
                    return True
                # Timed rather than indefinite: `close()` and `cancel()` both
                # notify, but a lifecycle that finishes a job through a path
                # this class does not observe would otherwise strand the
                # queue. A one-second re-check costs nothing and cannot hang.
                self._gate.wait(1.0)

    def _finish_admission(self, public_id: str) -> None:
        with self._gate:
            if self._admitted == public_id:
                self._admitted = None
            self._gate.notify_all()

    def _drop_pending(self, public_id: str) -> None:
        """Remove a job from the pending order. The caller holds the lock."""

        if public_id in self._pending:
            self._pending.remove(public_id)

    def _run(self, public_id: str, request: Any) -> None:
        if not self._await_admission(public_id):
            return
        try:
            self._run_admitted(public_id, request)
        finally:
            self._finish_admission(public_id)

    def _run_admitted(self, public_id: str, request: Any) -> None:
        try:
            identity = self._application.submit_generation(
                request, job_token=public_id
            )
        except StudioError as exc:
            with self._lock:
                record = self._jobs[public_id]
                if record["state"] not in TERMINAL_STATES:
                    if exc.error.code == "MODEL_JOB_CANCELLED":
                        record["state"] = CANCELLED
                    else:
                        record["state"] = FAILED
                        record["error"] = {
                            "code": exc.error.code,
                            "message": exc.error.message,
                        }
            self._report_terminal(public_id)
            return
        except BaseException as exc:  # noqa: BLE001 - scalar only
            with self._lock:
                record = self._jobs[public_id]
                if record["state"] not in TERMINAL_STATES:
                    record["state"] = FAILED
                    record["error"] = _failure_record(exc)
            self._report_terminal(public_id)
            return

        backend_id = getattr(identity, "job_id", None)
        with self._lock:
            record = self._jobs[public_id]
            record["backend_job_id"] = backend_id
            if record["state"] not in TERMINAL_STATES:
                # The blocking submit has already run the job to its backend
                # terminal by the time it returns; the backend poll below is
                # what reports which terminal it was.
                state, reason = self._terminal_from_backend(backend_id)
                record["state"] = state
                # A failure the backend REPORTED rather than RAISED still has a
                # reason, and it used to stop here. `submit_generation` returns
                # normally in that case, so neither handler above fires, and
                # this branch recorded the state while leaving `error` at the
                # None it was initialised with. That made `error: null` on a
                # FAILED job ambiguous between "the backend process died" and
                # "the backend failed and we discarded why" -- and the runbook
                # taught only the first reading, so a healthy process with an
                # intact log read as a crash.
                #
                # `project_progress` has always built a proper StructuredError
                # for this case. Nothing ever read it.
                #
                # Repro: generate with sampler "NotASampler". `create_sampler`
                # asserts, the backend marks its own progress failed, and the
                # job reported `failed` with no reason at all.
                if state == FAILED and reason is not None:
                    record["error"] = reason
        self._report_terminal(public_id)

    def _report_terminal(self, public_id: str) -> None:
        """One closing line per job, whichever terminal it reached.

        Placed here rather than at each of the three branches above so a
        fourth outcome cannot be added without one -- the failure reason in
        particular now EXISTS, after the `error: null` repair, and the console
        still did not print it.
        """

        with self._lock:
            record = dict(self._jobs.get(public_id) or {})
        state = record.get("state")
        if state == FAILED:
            error = record.get("error") or {}
            self._log.failure(
                public_id,
                code=str(error.get("code", "")),
                message=str(error.get("message", "")),
            )
        elif state == CANCELLED:
            self._log.queue("cancelled", job_id=public_id, depth=len(self._pending))
        elif state == COMPLETED:
            self._log.result(public_id)

    def _terminal_from_backend(
        self, backend_id: str | None
    ) -> tuple[str, dict[str, Any] | None]:
        """Which terminal the backend reached, and why when it failed."""

        if not backend_id:
            return FAILED, None
        try:
            progress = self._application.poll_or_stream_progress(backend_id)
        except BaseException:  # noqa: BLE001 - observation never raises
            return FAILED, None
        state = str(getattr(progress, "state", "")).lower()
        if "cancel" in state:
            return CANCELLED, None
        if "fail" in state or "error" in state:
            return FAILED, _reported_reason(progress)
        return COMPLETED, None

    # -- observation ---------------------------------------------------------

    def _presented(self, record: dict[str, Any]) -> dict[str, Any]:
        """A record as a client sees it.

        RUNNING is derived by observation: the blocking submit only returns at
        terminal, so the record itself stays QUEUED while the worker waits AND
        while it generates. The lifecycle knows the difference -- its active
        job token IS this public id once the lease is granted.
        """

        presented = dict(record)
        if presented["state"] == QUEUED:
            try:
                if self._lifecycle.state().get("active_job") == presented["job_id"]:
                    presented["state"] = RUNNING
            except BaseException:  # noqa: BLE001 - observation never raises
                pass
        # Where it sits in the order it will actually run in -- the same list,
        # not a parallel one. 1-based, because it is shown to a person.
        job_id = presented["job_id"]
        presented["queue_position"] = (
            self._pending.index(job_id) + 1 if job_id in self._pending else None
        )
        return presented

    def describe(self, public_id: str) -> dict[str, Any]:
        """The public record, merged with live backend progress when mapped."""

        with self._lock:
            record = self._jobs.get(public_id)
            if record is None:
                raise _error(JOB_NOT_FOUND, "That job is not known.", "job_id")
            merged = self._presented(record)
        # The in-flight id belongs to whatever is RUNNING, so only the running
        # job may borrow it. Unconditionally, a queued job -- or any finished
        # one whose own backend id was never mapped -- was answered with the
        # active job's progress under its own id, which with a queue means
        # `/api/jobs/<id>` showing you a different job's steps.
        #
        # Safe because admission is serial: exactly one job is admitted at a
        # time, so "the in-flight job" and "the admitted job" are the same job
        # by construction rather than by luck.
        backend_id = merged.get("backend_job_id")
        if backend_id is None and merged["job_id"] == self._admitted:
            backend_id = self._in_flight_backend_id()
            # REPORT it, having resolved it. This was used to poll and then
            # dropped, so the record said `backend_job_id: null` for the whole
            # time a job was running and only named it at terminal -- while
            # this method was already using the id it declined to mention.
            #
            # Live Preview is what needed it. Frames are held by the backend
            # adapter under the id IT minted, so a reader with only the public
            # id gets no frame and no error. The decode was working and
            # producing frames the socket could not ask for.
            merged["backend_job_id"] = backend_id
        if backend_id and merged["state"] in (RUNNING, COMPLETED, FAILED):
            try:
                progress = self._application.poll_or_stream_progress(backend_id)
                merged["step"] = getattr(progress, "step", None)
                merged["total_steps"] = getattr(progress, "total_steps", None)
                merged["progress"] = getattr(progress, "progress", None)
                # The STAGE LABEL, which this merge did not carry.
                #
                # The rest of defect 6, found by the live P0.7 re-run. Steps
                # became observable and the label did not, so `/api/jobs/<id>`
                # reported a moving counter with no idea which pass it was
                # counting -- and at TERMINAL it looked fine, because
                # `job_status` takes a different path for a completed job
                # (`poll()`, which returns the whole event). Mid-flight None,
                # terminal correct: the shape that reads as working.
                #
                # `project_progress` carries this deliberately -- it is the
                # only place the nine-state lifecycle survives the five-state
                # projection. Two things depend on it and neither could work:
                # the acceptance harness cancels from the job's own reported
                # phase, so both cancellation legs fired at None and never
                # cancelled at all; and `owner_stage` had nothing to project,
                # so the Jobs panel said "Generating" through a Hires pass.
                merged["message"] = getattr(progress, "message", "") or ""
            except BaseException:  # noqa: BLE001 - observation never raises
                pass
        return merged

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._presented(self._jobs[job_id]) for job_id in self._order]

    def running_job_id(self) -> str | None:
        """The public id of the job executing right now, or None.

        Separate from `queue_view` on cost grounds, not taste. The progress
        WebSocket asks this ten times a second per connection, and
        `queue_view` copies every record this coordinator has ever seen in
        order to answer it -- a per-frame O(jobs) walk to learn one id.

        The fallback loop mirrors `queue_view` and `cancel_all`: a job
        promoted through a path this class did not admit still counts as
        running, because the alternative is a socket that goes silent for a
        job the owner can see.
        """

        with self._lock:
            if self._admitted is not None and self._admitted in self._jobs:
                return self._admitted
            for job_id in self._order:
                if self._presented(self._jobs[job_id])["state"] == RUNNING:
                    return job_id
        return None

    # -- the owner-facing queue -------------------------------------------------

    def queue_view(self) -> dict[str, Any]:
        """Running, queued and recently finished, in the owner's vocabulary.

        The panel this replaces showed BUSY, an Unload button and
        `1 active - 0 queued`: lifecycle internals exposed to someone who
        should never have to reason about leases, cache ownership or session
        state. Those remain, as diagnostics, on `/api/model/state`. This is
        the answer to "what is running, what is waiting, and what can I do
        about it".
        """

        with self._lock:
            pending = list(self._pending)
            admitted = self._admitted
            records = {job_id: dict(self._jobs[job_id]) for job_id in self._order}
            order = list(self._order)

        running = None
        if admitted is not None and admitted in records:
            running = self._describe_for_queue(admitted)
        else:
            # Fall back to the lifecycle's own view. A host that submits
            # without this coordinator -- or a job promoted through a path
            # this class did not admit -- still shows up rather than
            # disappearing from the panel that claims to list everything.
            for job_id in order:
                if self._presented(records[job_id])["state"] == RUNNING:
                    running = self._describe_for_queue(job_id)
                    break

        queued = []
        for position, job_id in enumerate(pending, start=1):
            entry = self._describe_for_queue(job_id)
            entry["queue_position"] = position
            queued.append(entry)

        recent = [
            self._describe_for_queue(job_id)
            for job_id in reversed(order)
            if records[job_id]["state"] in TERMINAL_STATES
        ][:RECENT_TERMINAL_LIMIT]

        return {
            "running": running,
            "queued": queued,
            "recent": recent,
            "queue_depth": len(queued),
        }

    def _describe_for_queue(self, public_id: str) -> dict[str, Any]:
        try:
            entry = self.describe(public_id)
        except StudioError:
            return {"job_id": public_id, "state": CANCELLED,
                    "stage": STAGE_CANCELLED}
        entry["stage"] = owner_stage(entry.get("state", ""), entry.get("message"))
        return entry

    def reorder(self, order: list[str]) -> dict[str, Any]:
        """Set the execution order of the QUEUED jobs.

        `order` must be a permutation of exactly the currently pending ids.
        Refused otherwise rather than reconciled: a client working from a
        stale view would otherwise silently drop or duplicate a job, and the
        owner would see a queue that ran in an order they never chose. They
        should re-read and try again.
        """

        wanted = [str(job_id) for job_id in order]
        with self._gate:
            if sorted(wanted) != sorted(self._pending):
                raise _error(
                    QUEUE_ORDER_INVALID,
                    "The queue changed since that order was read; "
                    "re-read the queue and reorder again.",
                    "order",
                )
            self._pending = wanted
            self._gate.notify_all()
        self._log.queue("reordered", depth=len(wanted))
        return self.queue_view()

    def remove(self, public_id: str) -> dict[str, Any]:
        """Drop a job that has not started. Never loads or allocates anything.

        Distinct from `cancel` on purpose: cancel is one verb for any state
        and will reach the backend for a running job. This one refuses if the
        job is not still waiting, so a client cannot ask to "remove" the
        picture that is currently being made and get it stopped instead.
        """

        with self._gate:
            record = self._jobs.get(public_id)
            if record is None:
                raise _error(JOB_NOT_FOUND, "That job is not known.", "job_id")
            if public_id not in self._pending:
                raise _error(
                    JOB_NOT_QUEUED,
                    "That job has already started or finished.",
                    "job_id",
                )
            self._pending.remove(public_id)
            record["state"] = CANCELLED
            record["cancel_requested"] = True
            self._gate.notify_all()
            depth = len(self._pending)
        self._log.queue("removed_before_start", job_id=public_id, depth=depth)
        return {"job_id": public_id, "state": CANCELLED, "removed_before_start": True}

    def clear_queued(self) -> dict[str, Any]:
        """Remove every waiting job. The running one is left alone.

        Stated in the name and honoured in the code: an owner clearing a
        queue of five is not asking to lose the image currently being made.
        """

        with self._gate:
            removed = list(self._pending)
            for job_id in removed:
                record = self._jobs.get(job_id)
                if record is not None and record["state"] not in TERMINAL_STATES:
                    record["state"] = CANCELLED
                    record["cancel_requested"] = True
            self._pending.clear()
            self._gate.notify_all()
        if removed:
            self._log.queue("cleared", depth=0)
        return {"removed": removed, "count": len(removed), "cancelled_running": False}

    def cancel_all(self) -> dict[str, Any]:
        """Clear the queue AND stop the running job.

        A separate verb from `clear_queued` because the difference matters
        and a flag on one call would be read wrong exactly once, at the cost
        of an image that was nearly finished.
        """

        cleared = self.clear_queued()
        with self._lock:
            running = self._admitted
            if running is None:
                for job_id in self._order:
                    if self._presented(self._jobs[job_id])["state"] == RUNNING:
                        running = job_id
                        break
        cancelled_running = False
        if running is not None:
            try:
                self.cancel(running)
                cancelled_running = True
            except StudioError:
                # Already terminal, or in the promotion window. The queue is
                # still cleared, which is most of what was asked for, and the
                # answer says truthfully that the running job was not stopped.
                cancelled_running = False
        return {
            "removed": cleared["removed"],
            "count": cleared["count"],
            "cancelled_running": cancelled_running,
            "running_job_id": running,
        }

    def _in_flight_backend_id(self) -> str | None:
        """The running job's backend id, before the blocking submit returns.

        `record["backend_job_id"]` is only written AFTER `submit_generation`
        returns, and that call blocks until the job is finished. So for the
        entire duration of every job the id was None, `describe()` skipped its
        progress merge, and `/api/jobs/<id>` reported no step, no total and no
        stage label -- then all of it at once at terminal. Not a Hires problem:
        NO job had observable progress through this route.

        The page never noticed because it takes progress over the WebSocket.
        Anything else -- the polling flow the runbook documents, and any
        non-browser client -- saw a job that appeared to do nothing and then
        be done.

        Read defensively: a host whose backend does not publish this simply
        keeps the previous behaviour rather than failing.
        """

        # The explicit chain, verified rather than probed: the application
        # holds the backend adapter, and the adapter holds the generation
        # session that mints the id. A speculative attribute walk would keep
        # "working" by silently finding nothing the day either name changes.
        backend = getattr(self._application, "_backend", None)
        session = getattr(backend, "_generation", None)
        job_id = getattr(session, "in_flight_job_id", None)
        return job_id if isinstance(job_id, str) and job_id else None

    def backend_id_for(self, public_id: str) -> str | None:
        with self._lock:
            record = self._jobs.get(public_id)
            return record.get("backend_job_id") if record else None

    def mark_running(self, public_id: str, backend_id: str) -> None:
        """Called by the transport layer when backend identity is known early.

        The blocking submit returns only at terminal, so RUNNING is normally
        inferred; a backend that reports identity mid-flight can upgrade the
        record here. Never downgrades a terminal state.
        """

        with self._lock:
            record = self._jobs.get(public_id)
            if record and record["state"] == QUEUED:
                record["state"] = RUNNING
                record["backend_job_id"] = backend_id

    # -- cancellation ---------------------------------------------------------

    def cancel(self, public_id: str) -> dict[str, Any]:
        """One cancel verb for queued AND backend jobs. Idempotent."""

        with self._lock:
            record = self._jobs.get(public_id)
            if record is None:
                raise _error(JOB_NOT_FOUND, "That job is not known.", "job_id")
            state = record["state"]
            if state in TERMINAL_STATES:
                return {
                    "job_id": public_id,
                    "state": state,
                    "already_terminal": True,
                    # NOTHING was cancelled -- the job had already finished,
                    # failed or been cancelled before this call arrived. Every
                    # branch of this method reports `cancelled` because the
                    # adapter above defaulted a MISSING key to True, so an
                    # idempotent no-op was reported to the owner as "Cancelled."
                    "cancelled": False,
                }
            record["cancel_requested"] = True
            backend_id = record.get("backend_job_id")
            # Waiting at the admission gate: this is the whole cancellation.
            # The job has not reconciled a model, taken a lease, or reached
            # the adapter, session or port. Its worker wakes, sees a terminal
            # record, and returns without running anything.
            if public_id in self._pending:
                self._pending.remove(public_id)
                record["state"] = CANCELLED
                self._gate.notify_all()
                return {
                    "job_id": public_id,
                    "state": CANCELLED,
                    "cancelled_while_queued": True,
                    "cancelled": True,
                }

        # Queued in the LIFECYCLE rather than here -- a job submitted through
        # a path that did not pass this coordinator's gate. Still the whole
        # cancellation, for the same reason.
        if self._lifecycle.cancel_queued_job(public_id):
            with self._lock:
                record = self._jobs[public_id]
                record["state"] = CANCELLED
            return {"job_id": public_id, "state": CANCELLED,
                    "cancelled_while_queued": True,
                    "cancelled": True}

        # Running. `record["backend_job_id"]` is only written AFTER the
        # blocking submit returns -- which is at terminal -- so for the entire
        # duration of every job it is None, and this branch fell through to
        # the refusal below. The only thing that would have filled it earlier
        # is `mark_running`, which was written, exported, and has never had a
        # caller. Cancelling a running job was therefore unreachable through
        # the one verb a client has, which is why P0.7's two cancellation legs
        # could not be closed.
        #
        # `_in_flight_backend_id` already reads the id the session publishes
        # mid-flight; it was added for `describe()`'s progress merge and is
        # the same answer this needs.
        backend_id = backend_id or self._in_flight_backend_id()
        if backend_id:
            result = self._application.cancel_generation(backend_id)
            with self._lock:
                record = self._jobs[public_id]
                if record["state"] not in TERMINAL_STATES:
                    record["state"] = CANCELLED
            return {"job_id": public_id, "state": CANCELLED,
                    "cancelled_while_queued": False,
                    "cancelled": True,
                    "backend_result": getattr(result, "state", None)}

        raise _error(
            JOB_CANCEL_TOO_LATE,
            "That job is being promoted and can no longer be cancelled "
            "before it starts.",
            "job_id",
        )

    # -- teardown --------------------------------------------------------------

    def close(self) -> None:
        with self._gate:
            self._closed = True
            # Wake every worker parked at the gate. Without this they sit on
            # the one-second re-check until shutdown outruns them, and a
            # closing Studio would look like a hung queue.
            self._gate.notify_all()


__all__ = (
    "CANCELLED",
    "COMPLETED",
    "FAILED",
    "JOB_ALREADY_TERMINAL",
    "JOB_CANCEL_TOO_LATE",
    "JOB_NOT_FOUND",
    "JOB_NOT_QUEUED",
    "QUEUED",
    "QUEUE_ORDER_INVALID",
    "RECENT_TERMINAL_LIMIT",
    "RUNNING",
    "STAGE_CANCELLED",
    "STAGE_COMPLETED",
    "STAGE_FAILED",
    "STAGE_GENERATING",
    "STAGE_HIRES",
    "STAGE_PUBLISHING",
    "STAGE_QUEUED",
    "STAGE_STARTING",
    "TERMINAL_STATES",
    "JobCoordinator",
    "owner_stage",
)
