"""Generate owns model readiness. The P0.2 exit gate.

Forge Neo does not have a Load button. Selecting a checkpoint updates desired
loading parameters; `forge_model_reload()` runs when a generation begins and
compares the desired loading-parameter hash against the resident one --
unchanged reuses the model, changed unloads and reloads once. `ensure_loaded`
is the Studio equivalent.

Three properties are load-bearing here, and two of them are the accepted
memory contracts that several prior milestones exist to establish. They are
re-proven in this phase rather than at the end of the program, because this is
the phase that can break them:

```text
same selection      -> served warm, loads does NOT move
changed selection   -> A closed BEFORE B opens, overlap zero, one switch
terminal unload     -> exactly one terminal cache clear per closed session
```

Residency is decided by comparing the resident object's `profile_id` against
the requested one, and for a `ResolvedSelection` that value IS the fingerprint.
There is deliberately no second identity field, so there is nothing to keep in
step; a test below pins that.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.contracts import StudioError  # noqa: E402
from forge_studio.model_lifecycle import (  # noqa: E402
    MODEL_ALREADY_LOADING,
    MODEL_NOT_SELECTED,
    WarmSessionManager,
)
from forge_studio.model_profiles import ModelProfileRepository  # noqa: E402
from forge_studio.model_selection import ModelSelection, ResolvedSelection  # noqa: E402

A_IDS = ("a" * 32, "b" * 32, "c" * 32)
B_IDS = ("d" * 32, "e" * 32, "f" * 32)


def selection(ids=A_IDS) -> ResolvedSelection:
    chosen = ModelSelection(
        checkpoint_model_id=ids[0],
        text_encoder_model_id=ids[1],
        vae_model_id=ids[2],
    )
    return ResolvedSelection(
        selection=chosen,
        payload_references={
            "checkpoint": f"/models/{ids[0]}.safetensors",
            "text_encoder": f"/models/{ids[1]}.safetensors",
            "vae": f"/models/{ids[2]}.safetensors",
        },
    )


class _Session:
    def __init__(self, identity: str) -> None:
        self.profile_id = identity
        self.closed = False

    def close(self):
        self.closed = True
        # The shape `_close_session_locked` reads to count a terminal clear.
        return {"terminal_release": {"called": True, "reason": "session closed"}}


class _Loader:
    """Records loads/closes and, critically, whether A was still live when B
    opened. Overlap is the A-before-B proof."""

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.loaded: list[str] = []
        self.live: list[_Session] = []
        self.overlap = 0
        self.gate: threading.Event | None = None

    def load(self, resolved):
        identity = resolved.profile_id
        if identity in self.fail_on:
            raise RuntimeError("synthetic load failure")
        if any(not s.closed for s in self.live):
            self.overlap += 1
        if self.gate is not None:
            self.gate.wait(timeout=5)
        session = _Session(identity)
        self.loaded.append(identity)
        self.live.append(session)
        return session

    def close(self, session):
        return session.close()


def service(loader: _Loader | None = None):
    """The real WarmSessionManager, with an EMPTY profile repository.

    Empty on purpose: `ensure_loaded` must never consult one, and a lookup
    against this repository would raise.
    """

    loader = loader or _Loader()
    warm = WarmSessionManager(
        profiles=ModelProfileRepository([]),
        loader=loader.load,
        closer=loader.close,
    )
    return warm, warm, loader


# --------------------------------------------------------------------------
# 1. Load, reuse, switch
# --------------------------------------------------------------------------


class EnsureLoadedTests(unittest.TestCase):
    def test_from_no_model_a_selection_loads(self) -> None:
        api, warm, loader = service()
        self.assertEqual("no_model", api.describe()["state"])
        api.ensure_loaded(selection())
        self.assertEqual("ready", api.describe()["state"])
        self.assertEqual([selection().profile_id], loader.loaded)
        self.assertEqual(1, warm.counters["loads"])

    def test_the_same_selection_is_served_warm_without_reloading(self) -> None:
        # The core Neo behaviour: a second Generate must not reload.
        api, warm, loader = service()
        api.ensure_loaded(selection())
        api.ensure_loaded(selection())
        api.ensure_loaded(selection())
        self.assertEqual(1, warm.counters["loads"], "reloaded a resident selection")
        self.assertEqual(2, warm.counters["reuses"])
        self.assertEqual(1, len(loader.loaded))

    def test_reuse_is_observable_rather_than_inferred(self) -> None:
        # `loads` staying flat is the absence of evidence. `reuses` moving is
        # evidence, and is why it is counted separately.
        api, warm, _loader = service()
        api.ensure_loaded(selection())
        self.assertEqual(0, warm.counters["reuses"])
        api.ensure_loaded(selection())
        self.assertEqual(1, warm.counters["reuses"])

    def test_a_changed_selection_closes_a_before_opening_b(self) -> None:
        api, warm, loader = service()
        api.ensure_loaded(selection(A_IDS))
        api.ensure_loaded(selection(B_IDS))
        self.assertEqual(
            [selection(A_IDS).profile_id, selection(B_IDS).profile_id], loader.loaded
        )
        self.assertEqual(0, loader.overlap, "B opened while A was still live")
        self.assertEqual(1, warm.counters["switches"])
        self.assertEqual(2, warm.counters["loads"])
        self.assertTrue(loader.live[0].closed)

    def test_a_changed_selection_switches_exactly_once(self) -> None:
        api, warm, loader = service()
        api.ensure_loaded(selection(A_IDS))
        api.ensure_loaded(selection(B_IDS))
        api.ensure_loaded(selection(B_IDS))
        self.assertEqual(1, warm.counters["switches"])
        self.assertEqual(1, warm.counters["reuses"])
        self.assertEqual(2, len(loader.loaded))

    def test_switching_back_reloads_because_nothing_is_cached(self) -> None:
        # There is one resident session, not a pool. Returning to A is a load.
        api, warm, _loader = service()
        api.ensure_loaded(selection(A_IDS))
        api.ensure_loaded(selection(B_IDS))
        api.ensure_loaded(selection(A_IDS))
        self.assertEqual(3, warm.counters["loads"])
        self.assertEqual(2, warm.counters["switches"])


# --------------------------------------------------------------------------
# 2. The accepted memory contracts, re-proven here
# --------------------------------------------------------------------------


class MemoryAccountingTests(unittest.TestCase):
    def test_terminal_unload_clears_the_cache_exactly_once(self) -> None:
        api, warm, _loader = service()
        api.ensure_loaded(selection())
        api.unload()
        self.assertEqual("no_model", api.describe()["state"])
        self.assertEqual(1, warm.counters["unloads"])
        self.assertEqual(1, warm.counters["closes"])
        self.assertEqual(1, warm.counters["terminal_cache_clears"])

    def test_reuse_never_closes_a_session(self) -> None:
        # The failure this guards: a reuse that quietly tore down and rebuilt
        # would still look "correct" from the outside.
        api, warm, loader = service()
        api.ensure_loaded(selection())
        api.ensure_loaded(selection())
        api.ensure_loaded(selection())
        self.assertEqual(0, warm.counters["closes"])
        self.assertEqual(0, warm.counters["terminal_cache_clears"])
        self.assertFalse(loader.live[0].closed)

    def test_a_switch_clears_the_previous_session_exactly_once(self) -> None:
        api, warm, _loader = service()
        api.ensure_loaded(selection(A_IDS))
        api.ensure_loaded(selection(B_IDS))
        self.assertEqual(1, warm.counters["closes"])
        self.assertEqual(1, warm.counters["terminal_cache_clears"])

    def test_load_switch_unload_accounts_for_every_session(self) -> None:
        api, warm, loader = service()
        api.ensure_loaded(selection(A_IDS))
        api.ensure_loaded(selection(B_IDS))
        api.unload()
        self.assertEqual(2, warm.counters["loads"])
        self.assertEqual(2, warm.counters["closes"])
        self.assertEqual(2, warm.counters["terminal_cache_clears"])
        self.assertTrue(all(s.closed for s in loader.live))


# --------------------------------------------------------------------------
# 3. Failure, recovery and refusal
# --------------------------------------------------------------------------


class FailureTests(unittest.TestCase):
    def test_a_failed_load_leaves_a_deterministic_state(self) -> None:
        wanted = selection()
        api, _warm, _loader = service(_Loader(fail_on={wanted.profile_id}))
        with self.assertRaises(StudioError):
            api.ensure_loaded(wanted)
        self.assertEqual("failed", api.describe()["state"])

    def test_a_valid_selection_recovers_from_failed_without_a_manual_clear(self) -> None:
        # The owner's remedy for a failed load is to pick something that works
        # and press Generate. Requiring an explicit clear first would be the
        # Load button returning under another name.
        failing = selection(A_IDS)
        api, warm, _loader = service(_Loader(fail_on={failing.profile_id}))
        with self.assertRaises(StudioError):
            api.ensure_loaded(failing)
        api.ensure_loaded(selection(B_IDS))
        self.assertEqual("ready", api.describe()["state"])
        self.assertEqual(1, warm.counters["loads"])

    def test_a_failed_switch_does_not_resurrect_the_previous_model(self) -> None:
        bad = selection(B_IDS)
        api, _warm, loader = service(_Loader(fail_on={bad.profile_id}))
        api.ensure_loaded(selection(A_IDS))
        with self.assertRaises(StudioError):
            api.ensure_loaded(bad)
        self.assertEqual("failed", api.describe()["state"])
        self.assertTrue(loader.live[0].closed)

    def test_an_unidentifiable_selection_is_refused(self) -> None:
        api, _warm, _loader = service()
        with self.assertRaises(StudioError) as caught:
            api.ensure_loaded(object())
        self.assertEqual(MODEL_NOT_SELECTED, caught.exception.error.code)


# --------------------------------------------------------------------------
# 4. One decision point, one identity
# --------------------------------------------------------------------------


class SingleOwnerTests(unittest.TestCase):
    def test_a_second_ensure_during_a_load_is_refused_not_doubled(self) -> None:
        # Two concurrent Generates naming different selections must not both
        # load. The first holds LOADING; the second is refused rather than
        # racing it.
        loader = _Loader()
        loader.gate = threading.Event()
        api, _warm, _loader = service(loader)
        errors: list[BaseException] = []

        def first():
            try:
                api.ensure_loaded(selection(A_IDS))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        for _ in range(500):
            if api.describe()["state"] == "loading":
                break
            threading.Event().wait(0.002)
        with self.assertRaises(StudioError) as caught:
            api.ensure_loaded(selection(B_IDS))
        self.assertEqual(MODEL_ALREADY_LOADING, caught.exception.error.code)
        loader.gate.set()
        thread.join(timeout=5)
        self.assertEqual([], errors)
        self.assertEqual(1, len(loader.loaded))

    def test_there_is_no_second_resident_identity_field(self) -> None:
        # Residency is read off the resident object. A parallel field would be
        # a second source of truth for "what is loaded", which is how a switch
        # silently reuses the wrong model.
        source = (
            APP_ROOT / "forge_studio" / "model_lifecycle.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "_resident_fingerprint",
            "_resident_selection",
            "_current_fingerprint",
        ):
            self.assertNotIn(forbidden, source)

    def test_ensure_loaded_never_consults_a_profile_repository(self) -> None:
        # The repository is empty in these tests; a lookup would raise.
        api, warm, _loader = service()
        api.ensure_loaded(selection())
        self.assertEqual("ready", api.describe()["state"])
        self.assertEqual(1, warm.counters["loads"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
