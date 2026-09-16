"""Product-owned model and warm-session lifecycle.

Everything before this milestone treated "is a model loaded" as a boolean the
backend happened to know. That was enough for a diagnostic and is not enough for
a product: a user selects a profile, asks for a load, runs several jobs against
one warm session, then unloads or switches. Each of those is a state, and the
illegal combinations have to be refusable by name.

Three pieces:

* `ModelLifecycleState` and `TRANSITIONS` -- the declared graph. A transition
  that is not in the table is rejected with a stable code, never allowed
  "because nothing checked".
* `ModelLifecycle` -- the state itself, serialized behind one lock. Reads are
  snapshots; there is no window where a caller sees half a transition.
* `WarmSessionManager` -- the one owner of the live session. It leases the
  session to jobs, keeps it warm between them, and is the only thing that
  loads, unloads or switches.

The loader and the closer are **injected**. This module imports no backend, no
Torch and no Neo: constructing the whole lifecycle is free, and it stays free
until someone calls `load()` with a real loader behind it.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from collections.abc import Mapping
from enum import Enum
from threading import Condition, RLock
from typing import Any, Callable, Iterable

from .contracts import StructuredError, StudioError
from .load_diagnostics import load_failure_from
from .model_profiles import ModelProfile, ModelProfileRepository

MODEL_NOT_SELECTED = "MODEL_NOT_SELECTED"
MODEL_ALREADY_LOADING = "MODEL_ALREADY_LOADING"
MODEL_BUSY = "MODEL_BUSY"
MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
MODEL_UNLOAD_FAILED = "MODEL_UNLOAD_FAILED"
MODEL_SWITCH_FAILED = "MODEL_SWITCH_FAILED"
MODEL_SESSION_SHUTDOWN = "MODEL_SESSION_SHUTDOWN"
MODEL_TRANSITION_REJECTED = "MODEL_TRANSITION_REJECTED"
MODEL_NOT_READY = "MODEL_NOT_READY"
#: A job cancelled while it waited for the session. Distinct from a cancelled
#: *running* job: this one never reached the backend at all.
MODEL_JOB_CANCELLED = "MODEL_JOB_CANCELLED"
#: No result root is configured, so a load has nowhere to publish to. Raised
#: before any payload is opened.
RESULT_ROOT_NOT_CONFIGURED = "RESULT_ROOT_NOT_CONFIGURED"


class ModelLifecycleState(str, Enum):
    NO_MODEL = "no_model"
    # PROFILE_SELECTED is gone. It existed because choosing a model and
    # loading it were two owner actions; they are one now. Selecting a
    # dropdown changes desired state in the browser and reaches the server
    # only when a job names it, so there is no server-side state between
    # "nothing loaded" and "loading".
    LOADING = "loading"
    READY = "ready"
    BUSY = "busy"
    UNLOADING = "unloading"
    SWITCHING = "switching"
    FAILED = "failed"
    SHUTDOWN = "shutdown"


S = ModelLifecycleState

#: The declared graph. `SHUTDOWN` is reachable from everywhere and leads
#: nowhere, which is what makes it terminal rather than merely final-looking.
TRANSITIONS: dict[ModelLifecycleState, frozenset[ModelLifecycleState]] = {
    # A job that names its selection takes NO_MODEL straight to LOADING.
    S.NO_MODEL: frozenset({S.LOADING, S.SHUTDOWN}),
    S.LOADING: frozenset({S.READY, S.FAILED, S.SHUTDOWN}),
    S.READY: frozenset({S.BUSY, S.UNLOADING, S.SWITCHING, S.SHUTDOWN}),
    S.BUSY: frozenset({S.READY, S.FAILED, S.SHUTDOWN}),
    S.UNLOADING: frozenset({S.NO_MODEL, S.FAILED, S.SHUTDOWN}),
    S.SWITCHING: frozenset({S.READY, S.FAILED, S.NO_MODEL, S.SHUTDOWN}),
    # A valid selection recovers a failed lifecycle without a manual clear:
    # the owner's remedy is to pick something that works and press Generate.
    S.FAILED: frozenset({S.LOADING, S.NO_MODEL, S.SHUTDOWN}),
    S.SHUTDOWN: frozenset(),
}

#: States in which no job may start.
#:
#: UNLOADING and SWITCHING are here because the observable unload contract
#: names "new generation refused" as a step. Refusal during a teardown was
#: previously emergent rather than stated: the manager holds its lock across
#: the whole close, so a concurrent submit blocked and then found NO_MODEL.
#: That worked, but `accepting_jobs` still answered True while the session was
#: being released, which is a state the product was not in. The coordinator
#: reads that flag before it leases (`jobs.py`), so saying it plainly refuses
#: at the boundary instead of at the far side of a blocked lock.
_NOT_ACCEPTING = frozenset({
    S.NO_MODEL, S.LOADING, S.UNLOADING, S.SWITCHING,
    S.SHUTDOWN,
})


def _record_unload_event(name: str) -> None:
    """Note one cleanup step for the canonical unload recorder.

    Deferred import, like every other `forge_studio -> forge_headless` reach in
    this package: startup must not gain a module because the lifecycle exists.
    The recorder is off unless a test or a rehearsal switched it on, so the
    normal path is one import lookup and an early return. Failure here is
    swallowed -- an observation must never be able to fail an unload.
    """

    try:
        from forge_headless.unload_events import record

        record(name)
    except BaseException:  # noqa: BLE001 - observation is never load-bearing
        pass


def _error(code: str, message: str, field: str | None = None) -> StudioError:
    return StudioError(StructuredError(code=code, message=message, field=field))


class ModelLifecycle:
    """The state, serialized. Every read is a consistent snapshot."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._state = S.NO_MODEL
        self._profile: ModelProfile | None = None
        self._lock = RLock()
        self._clock = clock or time.monotonic
        self._history: list[dict[str, Any]] = []
        self._failure: StructuredError | None = None

    @property
    def state(self) -> ModelLifecycleState:
        with self._lock:
            return self._state

    @property
    def profile(self) -> ModelProfile | None:
        with self._lock:
            return self._profile

    def can(self, target: ModelLifecycleState) -> bool:
        with self._lock:
            return target in TRANSITIONS[self._state]

    def transition(
        self,
        target: ModelLifecycleState,
        *,
        profile: ModelProfile | None = None,
        failure: StructuredError | None = None,
        reason: str = "",
    ) -> ModelLifecycleState:
        """Move, or refuse by name. Never a silent no-op."""

        with self._lock:
            current = self._state
            if target not in TRANSITIONS[current]:
                raise _error(
                    MODEL_TRANSITION_REJECTED,
                    f"Cannot go from {current.value} to {target.value}.",
                    "state",
                )
            self._state = target
            if profile is not None:
                self._profile = profile
            if target in (S.NO_MODEL, S.SHUTDOWN):
                self._profile = None if target is S.SHUTDOWN else self._profile
            self._failure = failure if target is S.FAILED else None
            self._history.append(
                {
                    "from": current.value,
                    "to": target.value,
                    "at": round(self._clock(), 6),
                    "reason": reason,
                }
            )
            return target

    def clear_profile(self) -> None:
        with self._lock:
            self._profile = None

    def snapshot(self) -> dict[str, Any]:
        """Scalars only. The profile appears through its public projection."""

        with self._lock:
            return {
                "state": self._state.value,
                # RESIDENT, named as such. `profile`/`profile_id` below are the
                # same object under the loader's legacy vocabulary and are kept
                # so nothing that reads them breaks -- but "profile" is a
                # concept this product retired in P0.3, and a reader cannot tell
                # from that name whether it is what the owner ASKED for or what
                # is actually loaded. Those are different questions and only one
                # of them is answerable here.
                #
                # This is the lifecycle's fact to state: it owns residency.
                # Presentation reads it from here and must not re-derive it from
                # a desired ModelSelection -- two sources of truth for "what is
                # loaded" is how a switch silently reuses the wrong model, which
                # `ensure_loaded` already says in its own docstring.
                "resident_selection": (
                    self._profile.describe() if self._profile else None
                ),
                # A DEFERRED switch -- desired, accepted, not yet resident -- is
                # deliberately NOT reported here. `_pending_profile` belongs to
                # `WarmSessionManager`, not to this class, and reading it with a
                # `getattr` default would have produced a field that is
                # permanently None: present in the payload, never populated,
                # indistinguishable from "no switch pending". That is the
                # written-exported-never-called shape this codebase has been
                # bitten by three times. If pending needs reporting, it is the
                # session manager's `describe()` that owns the answer.
                "profile": self._profile.describe() if self._profile else None,
                "profile_id": self._profile.profile_id if self._profile else None,
                "accepting_jobs": self._state not in _NOT_ACCEPTING,
                "model_loaded": self._state in (S.READY, S.BUSY, S.SWITCHING),
                # `detail` carries the load stage and is included only when
                # present, so a failure raised elsewhere keeps its old shape.
                # It is built by `load_diagnostics`, which admits scalars only
                # and never a path, a repr or a traceback.
                "failure": (
                    {
                        "code": self._failure.code,
                        "message": self._failure.message,
                        **(
                            {"detail": dict(self._failure.detail)}
                            if getattr(self._failure, "detail", None)
                            else {}
                        ),
                    }
                    if self._failure
                    else None
                ),
                "transitions": len(self._history),
            }

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._history]


class WarmSessionManager:
    """The one owner of the live model session.

    Exactly one session exists at a time. Jobs lease it; leases keep it warm.
    Per-job generation cleanup is emphatically **not** this object's business --
    that runs inside the generation path and leaves the session READY, which is
    the whole point of a warm session.

    The loader and closer are injected callables:

        loader(profile) -> session
        closer(session)  -> None

    so nothing here imports a backend and constructing a manager costs nothing.
    """

    def __init__(
        self,
        *,
        profiles: ModelProfileRepository | None = None,
        loader: Callable[[ModelProfile], Any] | None = None,
        closer: Callable[[Any], None] | None = None,
        clock: Callable[[], float] | None = None,
        cancel_active: Callable[[], bool] | None = None,
        on_session_changed: Callable[[Any], None] | None = None,
    ) -> None:
        # `is None`, not `or`. A repository defines __len__, so an EMPTY one is
        # falsy, and `profiles or ...` silently replaced the caller's
        # repository with a fresh empty one whenever no static profiles were
        # configured. The composition root passes one repository to both this
        # manager and the lifecycle service; that substitution split them in
        # two, so a profile registered through the service could never be
        # selected here. Harmless while every host had a configured profile,
        # and exactly wrong once model folders became the way models arrive.
        self._profiles = (
            profiles if profiles is not None else ModelProfileRepository(())
        )
        self._loader = loader
        self._closer = closer
        self._cancel_active = cancel_active
        # Published on every load, unload, switch and shutdown so the
        # backend adapter uses the session the lifecycle owns rather than
        # building an unrelated one of its own.
        self._on_session_changed = on_session_changed
        self._job_tokens = itertools.count(1)
        self._lifecycle = ModelLifecycle(clock=clock)
        self._session: Any = None
        self._lock = RLock()
        self._idle = Condition(self._lock)
        self._queue: deque[str] = deque()
        #: Tokens cancelled while queued. A cancelled token is skipped by
        #: promotion and refused if its own thread is still waiting, so a
        #: cancelled job can never become active and never reaches the backend.
        self._cancelled_tokens: set[str] = set()
        #: True from the moment shutdown begins, so promotion stops handing
        #: the session to jobs that only just started waiting.
        self._shutting_down = False
        self._active_job: str | None = None
        self._pending: str | None = None          # "unload" | "switch"
        self._pending_profile: ModelProfile | None = None
        self._closed = False
        self.counters: dict[str, int] = {
            "loads": 0, "closes": 0, "leases": 0, "releases": 0,
            "switches": 0, "unloads": 0, "queued": 0, "rejected": 0,
            "queued_cancellations": 0,
            # Times a requested selection matched the resident one and was
            # served warm. Counted separately from `loads` on purpose: the
            # claim "the second generation did not reload" is only checkable
            # if reuse is observable, and `loads` staying flat is the absence
            # of evidence rather than evidence.
            "reuses": 0,
            # Counted separately from every other cache clear in the
            # process: this is the ONE terminal release per closed
            # session, and accounting must be able to say so.
            "terminal_cache_clears": 0,
        }
        #: The last close's terminal-release report, scalars only.
        self.last_terminal_release: dict[str, Any] | None = None

    # -- reads -------------------------------------------------------------

    @property
    def lifecycle(self) -> ModelLifecycle:
        return self._lifecycle

    # `profiles` is retired. It was "the repository `select` reads from",
    # exposed so a catalogue selection installed its runtime profile into the
    # same repository selection consulted. `select` and the catalogue-selection
    # route are both gone, so it guarded a drift between two references that
    # can no longer both exist. `_profiles` is still held -- `readiness()`
    # reports `profiles_configured` from it.

    @property
    def state(self) -> ModelLifecycleState:
        return self._lifecycle.state

    @property
    def manages_sessions(self) -> bool:
        """Whether this manager actually owns model sessions.

        False when no loader was injected: there is nothing it could load,
        so it must not gate generation. That is the mock host and every
        pre-Phase-2 arrangement, where the backend owns whatever session it
        was given. Refusing generation there would break a working product
        for no safety benefit -- the gate exists to stop a *generation*
        from opening a model, and with no loader none can.
        """

        return self._loader is not None

    @property
    def session(self) -> Any:
        with self._lock:
            return self._session

    @property
    def load_configuration_required(self) -> bool:
        """True when the loader exists but could not open anything if asked.

        Asked of the loader by surface, not by type: a loader that does not
        report `can_load` is treated as capable, because an injected test
        loader has no configuration to require.
        """

        loader = self._loader
        if loader is None:
            return False
        can_load = getattr(loader, "can_load", None)
        if can_load is None:
            return False
        return not bool(can_load)

    def describe(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._lifecycle.snapshot()
            snapshot.update(
                {
                    "load_configuration_required": self.load_configuration_required,
                    "session_active": self._session is not None,
                    "active_job": self._active_job,
                    "queued_jobs": len(self._queue),
                    "pending_action": self._pending,
                    "counters": dict(self.counters),
                    "closed": self._closed,
                }
            )
            return snapshot

    # -- selection ---------------------------------------------------------

    # `select(profile_id)` and `clear_selection()` are retired with the
    # owner-facing profile. Choosing a model is a browser-side change of
    # desired state that reaches the server only when a job names it, so
    # there is nothing for the server to "select" and nothing to clear.

    # -- load --------------------------------------------------------------

    def _complete_load(self, profile: Any) -> dict[str, Any]:
        """Run the loader and settle. The caller has already moved to LOADING.

        Split out of the old public `load()`. That method existed so an owner
        could ask for a load as a separate act; there is no such act now, so
        what remains is the work itself, driven by `ensure_loaded` under a
        state the caller already established.
        """

        # The loader runs outside the lock: it is the slow, fallible part, and
        # holding the lock across it would block every status read.
        try:
            session = self._loader(profile) if self._loader else None
            if session is None:
                raise RuntimeError("the loader returned no session")
        except BaseException as exc:  # noqa: BLE001 - reported as a scalar
            failure = load_failure_from(exc)
            with self._lock:
                self._lifecycle.transition(S.FAILED, failure=failure, reason="load failed")
            raise StudioError(failure) from None

        with self._lock:
            self._session = session
            self.counters["loads"] += 1
            self._lifecycle.transition(S.READY, profile=profile, reason="loaded")
            self._publish(session)
            return self._lifecycle.snapshot()

    def ensure_loaded(self, selection: Any) -> dict[str, Any]:
        """Make the resident session match the desired selection.

        The Studio equivalent of Forge Neo's `forge_model_reload()`: Neo hashes
        its loading parameters and compares them against the resident hash --
        unchanged reuses the model, changed unloads and reloads once, and
        generation then proceeds. Selecting a dropdown is not a load; asking to
        generate is.

        Residency is decided by comparing the resident object's `profile_id`
        against the requested one. For a `ResolvedSelection` that value IS the
        selection fingerprint, so **no second identity field exists** and there
        is nothing to keep in step. Two sources of truth for "what is loaded"
        is how a switch silently reuses the wrong model.

        This is the only place that decides load-vs-reuse-vs-switch, and it
        decides under the one lock, so two concurrent Generates naming
        different selections cannot both load.

        Reuse deliberately does not go through `load()`: `load()` counts a
        load, and a reuse that incremented `loads` would make "the second
        generation did not reload" pass for the wrong reason.
        """

        with self._lock:
            self._require_open()
            wanted = str(getattr(selection, "profile_id", "") or "")
            if not wanted:
                raise _error(
                    MODEL_NOT_SELECTED,
                    "That model selection could not be identified.",
                )

            state = self._lifecycle.state
            resident = self._lifecycle.profile
            resident_id = str(getattr(resident, "profile_id", "") or "")

            if state in (S.READY, S.BUSY):
                if resident is not None and resident_id == wanted:
                    self.counters["reuses"] += 1
                    return self._lifecycle.snapshot()
                if state is S.BUSY or self._active_job is not None:
                    # A switch under an active lease is deferred to the next
                    # safe boundary, exactly as an explicitly requested switch
                    # is. Forcing it would pull the model out from under a
                    # running job; refusing it would make a queued generation
                    # fail for a reason the owner cannot act on.
                    self._pending = "switch"
                    self._pending_profile = selection
                    return self.describe()
                # Warm, different selection, idle: close A before opening B.
                self._switch_locked(selection, reason="selection changed")
                return self._lifecycle.snapshot()

            if state is S.LOADING:
                raise _error(MODEL_ALREADY_LOADING, "A load is already in progress.")
            if state in (S.SWITCHING, S.UNLOADING):
                raise _error(
                    MODEL_BUSY,
                    "The previous model is still being released; try again.",
                )

            # NO_MODEL or FAILED. A valid selection recovers from FAILED
            # without a manual clear -- the owner's remedy for a failed load is
            # to pick something that works and press Generate. There is no
            # intermediate "selected" state to pass through: the job named the
            # model, so the next thing that happens is the load.
            self._lifecycle.transition(
                S.LOADING, profile=selection, reason="generate named a selection"
            )

        # Outside the lock: the loader is the slow, fallible part, and holding
        # the lock across it blocks every status read.
        return self._complete_load(selection)

    # -- job leases --------------------------------------------------------

    def acquire(self, job_id: str, *, timeout: float | None = None) -> bool:
        """Hold the session for one job, waiting for this job's turn.

        This is the call a caller must use before touching the backend. `lease`
        only *records* the queue position; it returns and the caller is free to
        run anyway, which is exactly how two generations reached one warm engine
        at once. Waiting here is what makes the queue mean something.

        Returns True when the lease was granted immediately, False when the job
        waited for it. Raises when it will never be granted -- refused,
        cancelled while queued, or shut down.
        """

        with self._lock:
            if self._lease_locked(job_id):
                return True
            # Queued. Wait for promotion on the same condition `release`
            # notifies; no polling and no spin.
            while True:
                if job_id in self._cancelled_tokens:
                    self._cancelled_tokens.discard(job_id)
                    raise _error(
                        MODEL_JOB_CANCELLED,
                        "That job was cancelled before it started.",
                        "job_id",
                    )
                if self._closed:
                    self._discard_locked(job_id)
                    raise _error(
                        MODEL_SESSION_SHUTDOWN,
                        "Studio shut down before that job started.",
                        "job_id",
                    )
                if self._active_job == job_id:
                    return False
                if not self._idle.wait(timeout=timeout if timeout else 30.0):
                    if timeout:
                        self._discard_locked(job_id)
                        raise _error(
                            MODEL_BUSY,
                            "That job waited too long for the model session.",
                            "job_id",
                        )

    def cancel_queued(self, job_id: str) -> bool:
        """Cancel a job that has not started. Never reaches the backend.

        Returns True when a queued job was cancelled. An active job is left
        alone: cancelling work already inside the generation port is a
        different operation with different risks, and is not done here.
        """

        with self._lock:
            if job_id not in self._queue:
                return False
            self._queue.remove(job_id)
            self._cancelled_tokens.add(job_id)
            self.counters["queued_cancellations"] += 1
            self._idle.notify_all()
            return True

    def queued_tokens(self) -> tuple[str, ...]:
        """The waiting job tokens, in FIFO order. Opaque, process-local."""

        with self._lock:
            return tuple(self._queue)

    def _discard_locked(self, job_id: str) -> None:
        if job_id in self._queue:
            self._queue.remove(job_id)

    def lease(self, job_id: str) -> bool:
        """Take the session for one job, or queue behind the active one.

        Returns True when the job holds the lease now, False when it is queued.
        A rejected job raises: nothing is silently dropped.

        Prefer `acquire`: this records a queue position without waiting for it,
        so a caller that ignores the answer runs concurrently with the active
        job. Retained because it is the locked primitive `acquire` is built on.
        """

        with self._lock:
            return self._lease_locked(job_id)

    def _lease_locked(self, job_id: str) -> bool:
        """The lease decision itself. The caller must already hold the lock."""

        self._require_open()
        state = self._lifecycle.state
        if state in _NOT_ACCEPTING:
            self.counters["rejected"] += 1
            raise _error(
                MODEL_NOT_READY,
                "No warm model session is ready for a job.",
                "job_id",
            )
        if self._pending is not None:
            self.counters["rejected"] += 1
            raise _error(
                MODEL_BUSY,
                f"A {self._pending} is pending; the session is not accepting jobs.",
                "job_id",
            )
        if self._active_job is None:
            self._active_job = job_id
            self.counters["leases"] += 1
            if self._lifecycle.state is S.READY:
                self._lifecycle.transition(S.BUSY, reason=f"job {job_id}")
            return True
        if job_id in self._queue or job_id == self._active_job:
            raise _error(MODEL_BUSY, "That job is already known.", "job_id")
        self._queue.append(job_id)
        self.counters["queued"] += 1
        return False

    def release(self, job_id: str) -> dict[str, Any]:
        """Give the session back. It stays warm; nothing unloads here."""

        with self._lock:
            if job_id in self._queue:
                self._queue.remove(job_id)
                return self.describe()
            if self._active_job != job_id:
                return self.describe()
            self._active_job = None
            self.counters["releases"] += 1
            if self._lifecycle.state is S.BUSY:
                self._lifecycle.transition(S.READY, reason=f"job {job_id} finished")
            self._promote_locked()
            self._idle.notify_all()
            return self.describe()

    def _promote_locked(self) -> None:
        """Run the pending action, or hand the lease to the next queued job."""

        if self._shutting_down:
            # A shutdown drains what is already running; it does not start
            # anything new. Promoting here would hand the session to a job
            # during the grace window and make shutdown wait for work it only
            # just began.
            return
        if self._pending is not None:
            action, profile = self._pending, self._pending_profile
            self._pending = None
            self._pending_profile = None
            if action == "unload":
                self._unload_locked(reason="pending unload")
            elif action == "switch" and profile is not None:
                self._switch_locked(profile, reason="pending switch")
            return
        while self._queue:
            candidate = self._queue.popleft()
            if candidate in self._cancelled_tokens:
                # Cancelled while it waited. Skip it rather than making a job
                # active that nobody is waiting for -- that would hold the
                # session until a release that never comes.
                self._cancelled_tokens.discard(candidate)
                continue
            self._active_job = candidate
            self.counters["leases"] += 1
            if self._lifecycle.state is S.READY:
                self._lifecycle.transition(S.BUSY, reason=f"job {self._active_job}")
            return

    # -- unload / switch ---------------------------------------------------

    def unload(self) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            state = self._lifecycle.state
            if state is S.NO_MODEL:
                return self._lifecycle.snapshot()
            if state is S.BUSY or self._active_job is not None:
                # Explicit policy: deferred, not refused and not forced.
                self._pending = "unload"
                self._pending_profile = None
                return self.describe()
            if state is S.FAILED:
                # A failed load left no session to release, so there is
                # nothing to close -- but the owner still needs a way back to
                # a clean start, and `clear_selection()` (which used to be
                # that way) is retired with the two-step selection it belonged
                # to. FAILED -> NO_MODEL is the edge the table already allows;
                # routing this through UNLOADING would be refused by name and
                # would strand the owner in FAILED with no owner-facing exit.
                # No unload is counted, because none happened.
                self._lifecycle.clear_profile()
                self._lifecycle.transition(S.NO_MODEL, reason="failure cleared")
                self._publish(None)
                return self._lifecycle.snapshot()
            self._unload_locked(reason="unload requested")
            return self._lifecycle.snapshot()

    def _unload_locked(self, *, reason: str) -> None:
        # READY only: FAILED has no session to close and no legal edge to
        # UNLOADING, and `unload()` clears it directly above.
        if self._lifecycle.state is not S.READY:
            if self._lifecycle.state is not S.UNLOADING:
                return
        self._lifecycle.transition(S.UNLOADING, reason=reason)
        # UNLOADING is in the non-accepting set, so generation is refused from
        # this point; the recorder marks the boundary the contract names.
        _record_unload_event("unloading_published")
        try:
            self._close_session_locked()
        except BaseException as exc:  # noqa: BLE001
            failure = StructuredError(
                code=MODEL_UNLOAD_FAILED,
                message=f"The model failed to unload ({type(exc).__name__}).",
            )
            self._lifecycle.transition(S.FAILED, failure=failure, reason="unload failed")
            raise StudioError(failure) from None
        self.counters["unloads"] += 1
        self._lifecycle.clear_profile()
        self._lifecycle.transition(S.NO_MODEL, reason="unloaded")
        _record_unload_event("no_model_published")
        # Idempotent: `_close_session_locked` already detached when there was
        # a session. Kept for the path where there was none to close.
        self._publish(None)

    # `switch(profile_id)` is retired with the profile repository it read from.
    # A change of model is now a job naming a different selection, which
    # `ensure_loaded` routes to `_switch_locked` below -- the same close-A-
    # before-open-B machinery, reached without a profile id.

    def _switch_locked(self, profile: ModelProfile, *, reason: str) -> None:
        previous = self._lifecycle.profile
        self._lifecycle.transition(S.SWITCHING, reason=reason)
        try:
            self._close_session_locked()
        except BaseException as exc:  # noqa: BLE001
            failure = StructuredError(
                code=MODEL_SWITCH_FAILED,
                message=f"The previous model failed to close ({type(exc).__name__}).",
            )
            self._lifecycle.transition(S.FAILED, failure=failure, reason="switch close failed")
            raise StudioError(failure) from None

        try:
            session = self._loader(profile) if self._loader else None
            if session is None:
                raise RuntimeError("the loader returned no session")
        except BaseException as exc:  # noqa: BLE001
            failure = StructuredError(
                code=MODEL_SWITCH_FAILED,
                message=f"The new model failed to load ({type(exc).__name__}).",
                field="profile_id",
            )
            # The old session is already closed and is NOT resurrected: coming
            # back up on a model the user did not ask for would be worse than
            # failing, because the next job would silently use it.
            self._lifecycle.clear_profile()
            self._lifecycle.transition(S.FAILED, failure=failure, reason="switch load failed")
            self._publish(None)
            raise StudioError(failure) from None

        self._session = session
        self.counters["switches"] += 1
        self.counters["loads"] += 1
        self._lifecycle.transition(S.READY, profile=profile, reason="switched")
        self._publish(session)
        del previous

    # -- shutdown ----------------------------------------------------------

    def shutdown(self, *, wait_seconds: float = 5.0) -> dict[str, Any]:
        """Deterministic close. Idempotent, and never waits forever."""

        with self._lock:
            if self._closed:
                return self.describe()
            self._shutting_down = True
            if self._active_job is not None and self._cancel_active is not None:
                try:
                    self._cancel_active()
                except BaseException:  # noqa: BLE001 - a refusing cancel is not fatal
                    pass
            deadline = time.monotonic() + max(0.0, wait_seconds)
            while self._active_job is not None and time.monotonic() < deadline:
                self._idle.wait(timeout=0.05)
            self._queue.clear()
            self._active_job = None
            self._pending = None
            self._pending_profile = None
            try:
                self._close_session_locked()
            except BaseException:  # noqa: BLE001 - shutdown reports, never raises
                pass
            self._closed = True
            # Wake anything still waiting for a lease, AFTER `_closed` is set,
            # so a waiter reports the shutdown rather than a cancellation.
            # Leaving them blocked on a condition nobody notifies again is the
            # difference between a bounded shutdown and a hung one.
            self._idle.notify_all()
            self._publish(None)
            if self._lifecycle.state is not S.SHUTDOWN:
                self._lifecycle.transition(S.SHUTDOWN, reason="shutdown")
            self._lifecycle.clear_profile()
            return self.describe()

    # -- internals ---------------------------------------------------------

    def _publish(self, session: Any) -> None:
        """Tell the composition which session is live. Never fails a load."""

        if self._on_session_changed is None:
            return
        try:
            self._on_session_changed(session)
        except BaseException:  # noqa: BLE001 - a refusing sink is reported by state
            pass

    def next_job_token(self) -> str:
        """A lease token for one job. Monotonic and process-local."""

        return f"studio-job-{next(self._job_tokens):06d}"

    def _close_session_locked(self) -> None:
        """The one close seam: explicit unload, switch, and shutdown.

        The closed session's own `close()` performs the terminal allocator
        release after dropping its last references, so every path through
        here gets exactly one -- and a duplicate close, which returns
        early either here (session already None) or inside the session,
        gets none.

        The generation adapter is detached HERE, as the first act of the
        close, rather than after the state transition each caller performs
        afterwards. The owner trial found the adapter still attached after
        public `NO_MODEL`: harmless in practice, because the lock is held
        throughout, but it published a state the adapter did not yet agree
        with. Detaching first also means every close path -- unload, switch
        and shutdown -- detaches identically, because they all arrive here.
        """

        session = self._session
        self._session = None
        if session is None:
            return
        # Before any ownership is released: nothing may serve this session
        # again, and nothing downstream can observe it attached.
        self._publish(None)
        _record_unload_event("adapter_detached")
        self.counters["closes"] += 1
        report: Any = None
        if self._closer is not None:
            report = self._closer(session)
        else:
            close = getattr(session, "close", None)
            if callable(close):
                report = close()
        terminal = None
        if isinstance(report, Mapping):
            terminal = report.get("terminal_release")
        if isinstance(terminal, Mapping):
            self.last_terminal_release = dict(terminal)
            if terminal.get("called"):
                self.counters["terminal_cache_clears"] += 1

    def _require_open(self) -> None:
        if self._closed or self._lifecycle.state is S.SHUTDOWN:
            raise _error(MODEL_SESSION_SHUTDOWN, "The model session is shut down.")


def transition_table() -> dict[str, list[str]]:
    """The declared graph, for evidence and for tests to compare against."""

    return {state.value: sorted(t.value for t in targets)
            for state, targets in TRANSITIONS.items()}


__all__ = (
    "MODEL_ALREADY_LOADING",
    "MODEL_BUSY",
    "MODEL_LOAD_FAILED",
    "MODEL_NOT_READY",
    "MODEL_NOT_SELECTED",
    "MODEL_SESSION_SHUTDOWN",
    "MODEL_SWITCH_FAILED",
    "MODEL_TRANSITION_REJECTED",
    "MODEL_UNLOAD_FAILED",
    "ModelLifecycle",
    "ModelLifecycleState",
    "TRANSITIONS",
    "WarmSessionManager",
    "transition_table",
)
