"""Auto-load through the real seam, not a unit of it.

`test_ensure_loaded.py` proves the manager's decision table and
`test_generate_owns_readiness.py` proves the ordering, both against doubles.
Neither would have caught the two integration gaps this file exists to close:

```text
1. StudioApplication was never given a selection_resolver, so
   _ensure_requested_model returned immediately and nothing auto-loaded.
2. The application gates on ModelLifecycleService, but ensure_loaded lived on
   WarmSessionManager -- so getattr(lifecycle, "ensure_loaded") missed and the
   whole path fell through silently.
```

Both were invisible to unit tests and would have shipped as "proven".

So this drives the REAL StudioApplication, the REAL ModelLifecycleService, the
REAL WarmSessionManager and the REAL resolver, with doubles only at the two
edges the product cannot supply in a test: the registry that resolves ids to
files, and the loader that opens them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = TEST_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from forge_studio.application import StudioApplication  # noqa: E402
from forge_studio.contracts import GenerationRequest, StudioError  # noqa: E402
from forge_studio.model_lifecycle import WarmSessionManager  # noqa: E402
from forge_studio.model_profiles import ModelProfileRepository  # noqa: E402
from forge_studio.model_selection import make_selection_resolver  # noqa: E402
from forge_studio.model_service import ModelLifecycleService  # noqa: E402

A = {"checkpoint_model_id": "a" * 32, "text_encoder_model_id": "b" * 32,
     "vae_model_id": "c" * 32}
B = {"checkpoint_model_id": "d" * 32, "text_encoder_model_id": "e" * 32,
     "vae_model_id": "f" * 32}


class _Registry:
    """Resolves ids to files. Every id resolves; containment is not the
    subject here and has its own suite."""

    def build_payload_references(self, ids):
        return {role: f"/models/{ids[role]}.safetensors" for role in ids}


class _Session:
    def __init__(self, identity: str) -> None:
        self.profile_id = identity
        self.closed = False

    def close(self):
        self.closed = True
        return {"terminal_release": {"called": True}}


class _Loader:
    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.fail_on = fail_on or set()
        self.loaded: list[str] = []
        self.live: list[_Session] = []
        self.overlap = 0

    def load(self, resolved):
        if resolved.profile_id in self.fail_on:
            raise RuntimeError("synthetic load failure")
        if any(not s.closed for s in self.live):
            self.overlap += 1
        session = _Session(resolved.profile_id)
        self.loaded.append(resolved.profile_id)
        self.live.append(session)
        return session

    def close(self, session):
        return session.close()


class _Backend:
    def __init__(self) -> None:
        self.submitted: list[object] = []

    def submit_generation(self, request):
        self.submitted.append(request)
        return {"job_id": f"job-{len(self.submitted)}"}


def wired(loader: _Loader | None = None):
    """The real objects, wired the way `launch.py` wires them."""

    loader = loader or _Loader()
    manager = WarmSessionManager(
        profiles=ModelProfileRepository([]),
        loader=loader.load,
        closer=loader.close,
    )
    lifecycle = ModelLifecycleService(
        profiles=ModelProfileRepository([]), manager=manager
    )
    backend = _Backend()
    app = StudioApplication(backend, model_lifecycle=lifecycle)
    app.use_selection_resolver(make_selection_resolver(_Registry()))
    app._validate_request = lambda request: None  # noqa: SLF001
    return app, manager, loader, backend


def request(selection):
    return GenerationRequest(
        model_id="runtime",
        positive_prompt="p",
        negative_prompt="",
        seed=1,
        steps=4,
        cfg_scale=5.0,
        width=768,
        height=768,
        model_selection=selection,
    )


class AutoLoadThroughTheApplicationTests(unittest.TestCase):
    def test_generate_from_no_model_loads_then_generates(self) -> None:
        app, manager, loader, backend = wired()
        self.assertEqual("no_model", manager.describe()["state"])
        app.submit_generation(request(A))
        self.assertEqual(1, len(loader.loaded))
        self.assertEqual(1, len(backend.submitted))
        self.assertEqual("ready", manager.describe()["state"])

    def test_the_same_selection_reuses_with_the_load_count_flat(self) -> None:
        app, manager, loader, backend = wired()
        app.submit_generation(request(A))
        app.submit_generation(request(A))
        app.submit_generation(request(A))
        self.assertEqual(1, manager.counters["loads"], "reloaded a resident model")
        self.assertEqual(2, manager.counters["reuses"])
        self.assertEqual(1, len(loader.loaded))
        self.assertEqual(3, len(backend.submitted))

    def test_a_changed_selection_switches_once_closing_a_before_b(self) -> None:
        app, manager, loader, backend = wired()
        app.submit_generation(request(A))
        app.submit_generation(request(B))
        self.assertEqual(2, len(loader.loaded))
        self.assertEqual(0, loader.overlap, "B opened while A was still live")
        self.assertEqual(1, manager.counters["switches"])
        self.assertTrue(loader.live[0].closed)
        self.assertEqual(2, len(backend.submitted))

    def test_a_valid_selection_recovers_from_a_failed_state(self) -> None:
        app, manager, loader, backend = wired(_Loader(fail_on={"__none__"}))
        # Fail A's load by targeting its fingerprint.
        first = request(A)
        loader.fail_on = {
            make_selection_resolver(_Registry())(first).profile_id
        }
        with self.assertRaises(StudioError):
            app.submit_generation(first)
        self.assertEqual("failed", manager.describe()["state"])
        app.submit_generation(request(B))
        self.assertEqual("ready", manager.describe()["state"])
        self.assertEqual(1, len(backend.submitted))

    def test_leases_and_releases_balance_across_every_path(self) -> None:
        app, manager, _loader, _backend = wired()
        app.submit_generation(request(A))
        app.submit_generation(request(A))
        app.submit_generation(request(B))
        self.assertEqual(
            manager.counters["leases"],
            manager.counters["releases"],
            "a lease was not released",
        )
        self.assertEqual(3, manager.counters["leases"])

    def test_a_lease_is_released_even_when_the_backend_fails(self) -> None:
        app, manager, _loader, backend = wired()

        def explode(_request):
            raise RuntimeError("backend failure")

        backend.submit_generation = explode
        with self.assertRaises(RuntimeError):
            app.submit_generation(request(A))
        self.assertEqual(
            manager.counters["leases"], manager.counters["releases"]
        )

    def test_exactly_one_terminal_clear_per_closed_loaded_session(self) -> None:
        app, manager, loader, _backend = wired()
        app.submit_generation(request(A))   # load A
        app.submit_generation(request(A))   # reuse: closes nothing
        app.submit_generation(request(B))   # switch: closes A
        manager.unload()                    # closes B
        self.assertEqual(2, manager.counters["loads"])
        self.assertEqual(2, manager.counters["closes"])
        self.assertEqual(2, manager.counters["terminal_cache_clears"])
        self.assertTrue(all(s.closed for s in loader.live))

    def test_reuse_closes_nothing_and_clears_nothing(self) -> None:
        app, manager, _loader, _backend = wired()
        app.submit_generation(request(A))
        app.submit_generation(request(A))
        self.assertEqual(0, manager.counters["closes"])
        self.assertEqual(0, manager.counters["terminal_cache_clears"])


class TheGatesThatBlockedTheOwnerTests(unittest.TestCase):
    """Two refusals that survived 1878 green tests and broke the live UI.

    Both only fire when NOTHING is loaded. Every other test in the suite
    arranges a resident model first, so neither path was ever taken. They were
    found by pressing Generate in a browser and are guarded here so a later
    edit cannot restore either.
    """

    def test_a_request_naming_a_selection_needs_no_model_id(self) -> None:
        # presentation refused an empty `model`, which asked the client to
        # report what is resident in order to MAKE something resident.
        from forge_studio.presentation import _validated_request_payload

        validated = _validated_request_payload({
            "model": "",
            "model_selection": dict(A),
            "positive_prompt": "p", "negative_prompt": "",
            "seed": 1, "steps": 4, "cfg_scale": 5.0,
            "width": 768, "height": 768,
        })
        self.assertEqual(A, validated["model_selection"])

    def test_a_request_naming_nothing_still_requires_a_model_id(self) -> None:
        from forge_studio.presentation import PresentationError, _validated_request_payload

        with self.assertRaises(PresentationError):
            _validated_request_payload({
                "model": "",
                "positive_prompt": "p", "negative_prompt": "",
                "seed": 1, "steps": 4, "cfg_scale": 5.0,
                "width": 768, "height": 768,
            })

    def test_the_resolved_identity_fills_an_empty_model_id(self) -> None:
        # Downstream readers must still see a request that names its model.
        app, _manager, _loader, backend = wired()
        app.submit_generation(request(A))
        submitted = backend.submitted[0]
        self.assertTrue(str(submitted.model_id).strip())

    def test_the_coordinator_accepts_a_job_that_names_its_selection(self) -> None:
        # JobCoordinator.submit refused on `accepting_jobs`, which reinstated
        # the Load step at the one gate the owner cannot see -- with the
        # button already gone from the screen.
        source = (APP_ROOT / "forge_studio" / "jobs.py").read_text(encoding="utf-8")
        self.assertIn("names_a_selection", source)
        # Anchor on the condition itself, not on the word where it is first
        # explained -- the comment above it mentions `accepting_jobs` too.
        gate = source[source.index('if not snapshot.get("accepting_jobs")'):][:120]
        self.assertIn("not names_a_selection", gate)

    def test_the_coordinator_still_refuses_a_job_naming_nothing(self) -> None:
        # The refusal is correct when the job cannot make itself runnable.
        source = (APP_ROOT / "forge_studio" / "jobs.py").read_text(encoding="utf-8")
        self.assertIn("MODEL_NOT_READY", source)


class TheWiringItselfTests(unittest.TestCase):
    """The two gaps that unit tests could not see."""

    def test_the_service_answers_ensure_loaded(self) -> None:
        # The application gates on the SERVICE. If ensure_loaded is only on the
        # manager, _ensure_requested_model falls through and auto-load is
        # proven by unit tests while never running.
        _app, manager, _loader, _backend = wired()
        service = ModelLifecycleService(
            profiles=ModelProfileRepository([]), manager=manager
        )
        self.assertTrue(callable(getattr(service, "ensure_loaded", None)))

    def test_launch_wires_the_resolver_to_the_one_registry(self) -> None:
        source = (APP_ROOT / "forge_studio" / "launch.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("use_selection_resolver", source)
        self.assertIn("make_selection_resolver(registry)", source)
        # Exactly one registry exists in the product; a second would mean a
        # second case-policy probe and containment snapshot.
        self.assertEqual(1, source.count("ModelRootRegistry("))

    def test_a_request_without_a_selection_does_not_auto_load(self) -> None:
        # Nothing named, nothing resident: the lease's refusal is correct.
        app, manager, loader, _backend = wired()
        with self.assertRaises(StudioError):
            app.submit_generation(request(None))
        self.assertEqual([], loader.loaded)
        self.assertEqual("no_model", manager.describe()["state"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
