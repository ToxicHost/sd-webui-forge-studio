"""Per-job option overrides, scoped to the thread that runs the job.

An owner preference like "use GPU tile compositing" has to reach code deep
inside Neo -- `modules/esrgan_model.py` -- without becoming global state. The
obvious route is to write it onto `shared.opts` when the job starts, and that
route is closed: about forty modules do `from modules.shared import opts` and
bind the object once, so a write is visible to every job in the process at
once. A queued job would see the preference the NEXT job was enqueued with.

Threading an argument down instead does not work either. The Studio-owned
boundary ends at `live_generation_port.generate`, and between there and
`do_upscale` sit three Neo-owned frames -- `resize_image`, `Upscaler.upscale`,
`Upscaler.do_upscale` -- none of which Studio should be editing to carry a
Studio preference.

So the value travels in a `ContextVar`, and the read happens where Studio
already owns the lookup: `HeadlessOptions.__getattr__`, which every `opts.X`
read in the process already routes through. No `modules/` edit, no mutation of
the options object, and nothing left behind when the job ends.

WHY THIS IS SAFE HERE, stated as the condition rather than as a hope:
`JobCoordinator` starts a thread per job but admits exactly one
(`jobs.py:299-336`), and the chain from `_run_admitted` to `do_upscale` is
synchronous on that one thread -- `InlineExecutor.submit` calls the work
inline, and `GenerationGateway.submit` returns `self._port.generate(...)`
directly. A `ContextVar` set at the top of that chain is visible to all of it
and to nothing else. If admission ever becomes concurrent this stays correct,
because a `ContextVar` is per-thread by construction; what would break is any
attempt to read the decision back from outside the scope, which is why the
decision is recorded INTO the scope and returned from it.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Iterator, Mapping


#: Options this job overrides, by the same name the backend reads them under.
_OPTIONS: ContextVar[Mapping[str, Any]] = ContextVar(
    "studio_job_options", default={})

#: What the job actually decided, filled in while it runs.
_DECISIONS: ContextVar[dict[str, Any] | None] = ContextVar(
    "studio_job_decisions", default=None)


@contextlib.contextmanager
def job_scope(**overrides: Any) -> Iterator[dict[str, Any]]:
    """Run a job with these option overrides in force on this thread.

    Yields the decision record, which is empty on entry and carries whatever
    the job recorded by the time the block exits. Read it inside the block:
    outside, the scope is gone and so is the record.

    Restored by token rather than by deletion, so a nested scope puts back
    what it found instead of clearing it.
    """
    option_token = _OPTIONS.set(dict(overrides))
    decisions: dict[str, Any] = {}
    decision_token = _DECISIONS.set(decisions)
    try:
        yield decisions
    finally:
        _OPTIONS.reset(option_token)
        _DECISIONS.reset(decision_token)


def option(name: str, default: Any = None) -> Any:
    """This job's override for `name`, or `default` when it did not set one."""
    return _OPTIONS.get().get(name, default)


def has_option(name: str) -> bool:
    """Whether this job overrode `name` at all.

    Distinct from `option(name) is None`, because False is a real override and
    an absent one has to keep falling through to the projected default.
    """
    return name in _OPTIONS.get()


def record(**facts: Any) -> None:
    """Note what the job decided. A no-op outside a scope.

    Deliberately quiet when there is no scope: the upscaler is reachable from
    tests and from paths that never opened one, and a preflight that raised
    because nobody was listening would turn a diagnostic into an outage.
    """
    decisions = _DECISIONS.get()
    if decisions is not None:
        decisions.update(facts)


def recorded() -> dict[str, Any]:
    """What has been recorded so far in this scope. Empty outside one."""
    decisions = _DECISIONS.get()
    return dict(decisions) if decisions is not None else {}


__all__ = (
    "has_option",
    "job_scope",
    "option",
    "record",
    "recorded",
)
