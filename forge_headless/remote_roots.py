"""Bounded access to a model folder that lives on another machine.

`root_policy` refuses network locations by default, and says why: touching
`\\\\otherpc\\share\\models` -- or a mapped drive, or an NFS mount -- can block
inside the operating system for as long as the server takes to answer, which
for a machine that is switched off can be minutes or forever. Python cannot
cancel a blocked `stat`. A request handler that makes one waits with it.

The owner can opt in ("Allow network model folders"). This module is what
makes the opt-in safe rather than merely permitted:

```text
every touch of a network root     runs on a worker thread; the caller waits
                                  at most a fixed time, then gets
                                  HEADLESS_CATALOGUE_ROOT_UNREACHABLE
a call that timed out             marks the root unreachable; later callers
                                  fail FAST until RETRY_AFTER_SECONDS pass or
                                  a worker comes back with an answer
stuck workers                     at most MAX_WORKERS_PER_ROOT per root, so a
                                  dead share cannot grow the thread count
```

The worker that timed out is not killed -- nothing can kill it -- and it is a
daemon, so it never holds the process open. It only ever COMPUTES; whoever
called it publishes the result, so a late answer changes nothing except the
root's reachability.

Local roots never come here. A catalogue on a local disk has no gate, and
every call it makes is the same direct call it always was.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar

from .contracts import HeadlessError

ROOT_UNREACHABLE = "HEADLESS_CATALOGUE_ROOT_UNREACHABLE"

#: How long one touch may take: resolving the root, a stat before a load, a
#: preview read. A live share on a home network answers in milliseconds.
PROBE_TIMEOUT_SECONDS = 5.0

#: How long one full scan of a root may take. A walk of a large library over
#: SMB is legitimately slower than a stat; it is still bounded.
SCAN_TIMEOUT_SECONDS = 60.0

#: After a timeout, callers are refused without trying for this long. A worker
#: that comes back with an answer ends the wait early.
RETRY_AFTER_SECONDS = 30.0

#: Workers that may be in flight against one root at once, stuck or not.
MAX_WORKERS_PER_ROOT = 4

T = TypeVar("T")


def unreachable() -> HeadlessError:
    return HeadlessError(
        ROOT_UNREACHABLE,
        "That network folder is not answering. Studio will try it again.",
    )


class RootGate:
    """Runs filesystem work for ONE network root, bounded in time and threads."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_WORKERS_PER_ROOT)
        self._inside = threading.local()
        self._unreachable_since: float | None = None

    @property
    def reachable(self) -> bool:
        """False from a timeout until a worker answers or a retry succeeds."""

        with self._lock:
            return self._unreachable_since is None

    def reset(self) -> None:
        """Forget a past timeout, so the next call tries. An owner's Rescan."""

        with self._lock:
            self._unreachable_since = None

    def _mark_unreachable(self) -> None:
        with self._lock:
            if self._unreachable_since is None:
                self._unreachable_since = self._clock()

    def _mark_answered(self) -> None:
        with self._lock:
            self._unreachable_since = None

    def _cooling_down(self) -> bool:
        with self._lock:
            since = self._unreachable_since
            return since is not None and self._clock() - since < RETRY_AFTER_SECONDS

    def run(self, work: Callable[[], T], *, timeout: float | None = None) -> T:
        """`work()`, or `ROOT_UNREACHABLE` once `timeout` seconds have passed.

        Re-entrant: work already running on this root's worker calls straight
        through, so a scan that resolves an entry does not queue behind itself.
        """

        if getattr(self._inside, "active", False):
            return work()
        if self._cooling_down():
            raise unreachable()
        limit = PROBE_TIMEOUT_SECONDS if timeout is None else float(timeout)
        started = time.monotonic()
        if not self._slots.acquire(timeout=limit):
            # Every slot is held by a worker that has not come back. The share
            # is not answering, and starting another would only add a thread.
            self._mark_unreachable()
            raise unreachable()

        outcome: dict[str, Any] = {}
        finished = threading.Event()

        def worker() -> None:
            self._inside.active = True
            try:
                outcome["value"] = work()
            except BaseException as error:  # noqa: BLE001 - re-raised by the caller
                outcome["error"] = error
            finally:
                error = outcome.get("error")
                if isinstance(error, HeadlessError) and error.code == ROOT_UNREACHABLE:
                    # The work itself concluded the share is gone (a fast
                    # "network path not found"), which is the same verdict a
                    # timeout reaches, only sooner.
                    self._mark_unreachable()
                else:
                    # The server answered, even if the answer was an error: a
                    # missing file is a fact about the share, not a dead share.
                    self._mark_answered()
                self._slots.release()
                finished.set()

        thread = threading.Thread(
            target=worker, name="studio-network-root", daemon=True
        )
        thread.start()
        remaining = max(0.0, limit - (time.monotonic() - started))
        if not finished.wait(remaining):
            self._mark_unreachable()
            raise unreachable()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]


__all__ = (
    "MAX_WORKERS_PER_ROOT",
    "PROBE_TIMEOUT_SECONDS",
    "RETRY_AFTER_SECONDS",
    "ROOT_UNREACHABLE",
    "SCAN_TIMEOUT_SECONDS",
    "RootGate",
    "unreachable",
)
