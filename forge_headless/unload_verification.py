"""Reference-safe unload observation for verification harnesses.

The V2 live UI smoke's verifier held strong references to the wrapper,
inner session, port, and engine ACROSS the frontend unload, pinning the
engine's memory through the product's cleanup window: the samples labeled
post-unload measured the verifier's grip, and the central classifier
rightly refused acceptance. This module owns the corrected discipline so
no future harness re-invents it wrong:

```text
capture scalar/equality facts        (while the session is warm)
create weakrefs                      (observation without ownership)
drop every verifier strong reference (before any unload is requested)
run the caller's unload action       (the frontend click)
let the product finish               (bounded weakref-death wait + gc)
only then sample registry/globals    (and let the caller sample memory)
```

`ObservedUnload` never stores a strong reference to any captured object:
`capture()` takes the objects, records scalars, keeps weakrefs, and lets
its locals die. The one hazard left is the CALLER's own variables, which
`ObservedUnload` cannot see -- `finish()` therefore reports any weakref
still alive, and the classifier stays fail-closed on it.

Import-safe: importing this module reaches no torch, no backend, no
modules.
"""

from __future__ import annotations

import gc
import sys
import time
import weakref
from typing import Any, Callable


class ObservedUnload:
    """One reference-safe unload observation."""

    def __init__(self) -> None:
        self.facts: dict[str, Any] = {}
        self._refs: dict[str, weakref.ref] = {}
        self.order: list[str] = []

    # -- phase 1: warm capture --------------------------------------------

    def capture(self, *, wrapper: Any, extra: dict[str, Any] | None = None,
                ) -> dict[str, Any]:
        """Record scalar facts and weakrefs; retain NO strong reference.

        Returns the scalar facts. After this call the verifier must drop
        its own variables for the same objects before requesting unload.
        """

        inner = getattr(wrapper, "session", None)
        port = getattr(wrapper, "port", None)
        engine = getattr(port, "_engine", None)
        self.facts = {
            "wrapper_type": type(wrapper).__name__,
            "inner_session_type": type(inner).__name__
            if inner is not None else None,
            "port_generate_calls": int(getattr(port, "generate_calls", 0)),
            "port_release_calls": int(getattr(port, "release_calls", 0)),
            "engine_type": type(engine).__name__
            if engine is not None else None,
        }
        if extra:
            self.facts.update(extra)
        # The weakref set is VERIFIER-scoped: wrapper, inner session, and
        # port are the objects a harness holds and must not hold across an
        # unload. The engine's own release is judged by the registry count
        # and the memory thresholds -- an engine weakref would couple this
        # check to registry internals (and, in the synthetic seam world,
        # to a stub artifact) rather than to the verifier's discipline.
        self._refs = {"wrapper": weakref.ref(wrapper)}
        if inner is not None:
            self._refs["session"] = weakref.ref(inner)
        if port is not None:
            self._refs["port"] = weakref.ref(port)
        self.order.append("captured")
        # Locals die here; nothing above stored the objects themselves.
        return dict(self.facts)

    # -- phase 2: pre-unload discipline ------------------------------------

    def ready_for_unload(self) -> dict[str, Any]:
        """The go/no-go gate the harness calls AFTER dropping its refs.

        The product still owns the session, so every weakref must be
        alive here; what this step asserts is the ORDER -- it marks the
        moment the verifier declared itself reference-free, and
        `finish()` will expose any lie by liveness after cleanup.
        """

        gc.collect()
        alive = {name: ref() is not None for name, ref in self._refs.items()}
        self.order.append("verifier_refs_dropped")
        return {"weakrefs_alive_pre_unload": alive}

    # -- phase 3: after the caller's unload action --------------------------

    def finish(self, *, registry_reader: Callable[[], Any] | None = None,
               timeout_seconds: float = 30.0) -> dict[str, Any]:
        """Wait (bounded) for the observed objects to die, then sample.

        Registry and global-state reads happen HERE, strictly after the
        product's cleanup had its window, never before.
        """

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            gc.collect()
            if not any(ref() is not None for ref in self._refs.values()):
                break
            time.sleep(0.05)
        alive = {name: ref() is not None for name, ref in self._refs.items()}
        self.order.append("cleanup_settled")
        registry = None
        if registry_reader is not None:
            registry = registry_reader()
        else:
            memory_management = sys.modules.get("backend.memory_management")
            if memory_management is not None:
                loaded = getattr(
                    memory_management, "current_loaded_models", None)
                registry = len(loaded) if loaded is not None else None
        self.order.append("registry_sampled")
        return {
            "weakrefs_alive_after_cleanup": alive,
            "owned_weakrefs_dead": not any(alive.values()),
            "registry_count": registry,
            "order": list(self.order),
        }


def ownership_facts(*, finish_report: dict[str, Any],
                    global_state_restored: bool) -> dict[str, Any]:
    """The three classifier ownership facts, from one observation."""

    registry = finish_report.get("registry_count")
    return {
        "owned_weakrefs_dead": bool(finish_report.get("owned_weakrefs_dead")),
        "registry_restored": registry == 0 if registry is not None else False,
        "global_state_restored": bool(global_state_restored),
    }


__all__ = ("ObservedUnload", "ownership_facts")
