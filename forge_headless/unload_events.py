"""One canonical record of what the product does during an unload.

The final owner trial compared the real unload against a required order and
found four mismatches. Three were readings of the same event under different
names; one was real -- the generation adapter stayed attached until after
public ``NO_MODEL``. That defect survived because the test suite asserted
against a hand-written *model* of the unload rather than against the unload,
and the model already had the adapter detaching early.

This module removes the gap. The product records its own cleanup steps here,
and both the test suite and the evidence harness read the same recording. There
is one order, in one place, and nothing describes it twice.

The recorder is OFF by default. A production unload records nothing, allocates
nothing and cannot grow without bound; recording is switched on deliberately by
a test or a rehearsal. Recording never raises: an observation that could fail a
teardown would be worse than no observation.

Import-safe: stdlib only, no torch, no CUDA, no product imports.
"""

from __future__ import annotations

import threading
import time
from typing import Any

#: The product's cleanup steps, in the order the product must perform them.
#:
#: The two restore steps are named for what they put back rather than for the
#: shape of the contract that asked for them. `_PublishedBookkeeping.restore`
#: undoes the direct-load reload bookkeeping first and republishes the retained
#: Forge sentinel (`shared.sd_model`) second, and that order is deliberate: its
#: own docstring records that republishing the sentinel last is what stops a
#: released engine from being left as the retained code's current model.
#: Reversing it to match a listing would trade a documented safety property for
#: a cosmetic one.
UNLOADING_PUBLISHED = "unloading_published"
ADAPTER_DETACHED = "adapter_detached"
ENGINE_OWNERSHIP_RELEASED = "engine_ownership_released"
RELOAD_BOOKKEEPING_RESTORED = "reload_bookkeeping_restored"
SENTINEL_RESTORED = "sentinel_restored"
TERMINAL_CACHE_CLEAR = "terminal_cache_clear"
NO_MODEL_PUBLISHED = "no_model_published"

#: The contract. `follows_product_order` is checked against exactly this.
PRODUCT_ORDER = (
    UNLOADING_PUBLISHED,
    ADAPTER_DETACHED,
    ENGINE_OWNERSHIP_RELEASED,
    RELOAD_BOOKKEEPING_RESTORED,
    SENTINEL_RESTORED,
    TERMINAL_CACHE_CLEAR,
    NO_MODEL_PUBLISHED,
)

#: Steps that must appear in every completed unload of a loaded session. The
#: terminal clear is deliberately absent: a process with no initialized CUDA
#: has nothing to clear and says so by absence rather than by pretending.
REQUIRED_STEPS = (
    UNLOADING_PUBLISHED,
    ADAPTER_DETACHED,
    NO_MODEL_PUBLISHED,
)


class UnloadEventRecorder:
    """Ordered, timestamped cleanup steps. Off unless switched on."""

    def __init__(self) -> None:
        self._events: list[tuple[str, float]] = []
        self._enabled = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> "UnloadEventRecorder":
        """Start recording, from empty. Returns self so tests can chain."""

        with self._lock:
            self._events = []
            self._enabled = True
        return self

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def reset(self) -> None:
        with self._lock:
            self._events = []

    def record(self, name: str) -> None:
        """Record one step. Never raises, never blocks on a caller's error."""

        if not self._enabled:
            return
        try:
            stamp = time.monotonic()
            with self._lock:
                self._events.append((str(name), stamp))
        except BaseException:  # noqa: BLE001 - observation never fails a teardown
            pass

    # -- reading -----------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(name for name, _ in self._events)

    def timeline(self) -> list[dict[str, Any]]:
        """Steps with milliseconds from the first one, for evidence."""

        with self._lock:
            events = list(self._events)
        if not events:
            return []
        origin = events[0][1]
        return [
            {"event": name, "ms": round((stamp - origin) * 1000, 3)}
            for name, stamp in events
        ]

    def follows_product_order(self) -> bool:
        """Whether what happened is consistent with the contract.

        A subsequence check, not equality: a step that could not happen (the
        terminal clear with no CUDA) is an absence, and absence is reported by
        `missing_steps`, not by pretending the order broke. What this catches
        is a step happening in the WRONG PLACE -- which is exactly the defect
        the owner trial found.
        """

        expected = list(PRODUCT_ORDER)
        position = 0
        for name in self.names():
            if name not in expected:
                continue
            try:
                index = expected.index(name, position)
            except ValueError:
                return False
            position = index + 1
        return True

    def missing_steps(self, required: tuple[str, ...] = REQUIRED_STEPS) -> tuple[str, ...]:
        observed = set(self.names())
        return tuple(name for name in required if name not in observed)

    def index_of(self, name: str) -> int:
        """First position of a step, or -1. For explicit before/after tests."""

        names = self.names()
        return names.index(name) if name in names else -1

    def happened_before(self, first: str, second: str) -> bool:
        """True only when BOTH were recorded and `first` came first."""

        left, right = self.index_of(first), self.index_of(second)
        return left >= 0 and right >= 0 and left < right

    def report(self) -> dict[str, Any]:
        return {
            "observed": list(self.names()),
            "required_order": list(PRODUCT_ORDER),
            "follows_product_order": self.follows_product_order(),
            "missing_required_steps": list(self.missing_steps()),
            "timeline": self.timeline(),
        }


#: The one recorder. Product code calls `record()`; nothing else is exported
#: as mutable state.
RECORDER = UnloadEventRecorder()


def record(name: str) -> None:
    """Module-level shorthand so product call sites stay one short line."""

    RECORDER.record(name)


__all__ = (
    "ADAPTER_DETACHED",
    "ENGINE_OWNERSHIP_RELEASED",
    "NO_MODEL_PUBLISHED",
    "PRODUCT_ORDER",
    "RECORDER",
    "RELOAD_BOOKKEEPING_RESTORED",
    "REQUIRED_STEPS",
    "SENTINEL_RESTORED",
    "TERMINAL_CACHE_CLEAR",
    "UNLOADING_PUBLISHED",
    "UnloadEventRecorder",
    "record",
)
